#!/usr/bin/env python3
import os, re, sys, logging, signal
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from telegram import Update, Message
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

try:
    from download.manager import DownloadManager
    from session.manager import SessionManager
    from storage.storage import Storage
except ImportError:
    from app.download.manager import DownloadManager
    from app.session.manager import SessionManager
    from app.storage.storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("photo-bot")

SITES: Dict[str, Dict[str,bool]] = {
    'ALPHA':   {'alpha': True, 'a': True},
    'BRAVO':   {'bravo': True, 'b': True},
    'CHARLIE': {'charlie': True, 'c': True},
    'DELTA':   {'delta': True, 'd': True},
    'ECHO':    {'echo': True,  'e': True},
}
TASK_GRASS = 'grass_cutting'
TASK_DRAIN = 'drainage_cleaning'
BEFORE_WORDS = {'sebelum','sblm','sblum','sebelom','before'}
AFTER_WORDS  = {'selepas','slps','slpas','after','lepas'}

TIME_BEFORE_HOUR = int(os.getenv('TIME_BEFORE_HOUR','12'))
TIME_AFTER_HOUR  = int(os.getenv('TIME_AFTER_HOUR','15'))
storage = Storage()
session_manager = SessionManager()
download_manager = DownloadManager()
SAVE_ROOT = storage.photos_root

def set_ctx(chat_id:int, site:Optional[str]=None, task:Optional[str]=None, when:Optional[str]=None):
    current = session_manager.get_session(chat_id)
    if current:
        session_manager.update_session(chat_id, site=site, task=task, when=when)
    else:
        session_manager.create_session(chat_id, site=site, task=task, when=when)

def get_ctx(chat_id:int, key:str) -> Optional[str]:
    return session_manager.get_session(chat_id).get(key)

def all_aliases() -> Dict[str,str]:
    out={}
    for site, aliases in SITES.items():
        for alias in aliases:
            out[alias.lower()]=site
    return out

def normalize_when(word: Optional[str]) -> Optional[str]:
    if not word: return None
    w = word.lower()
    if w in BEFORE_WORDS: return 'before'
    if w in AFTER_WORDS:  return 'after'
    return None

def detect_site_free(text:str) -> Optional[str]:
    aliases = all_aliases()
    t=(text or '').lower()
    for token in re.split(r'[\s,;/\-_.]+', t):
        if token in aliases:
            return aliases[token]
    m=re.search(r'\bzone\s*([a-z])\b', t)
    if m: return aliases.get(m.group(1).lower())
    return None

def detect_when_fallback(caption:str, dt:datetime) -> str:
    t=(caption or '').lower()
    if any(w in t for w in BEFORE_WORDS): return 'before'
    if any(w in t for w in AFTER_WORDS):  return 'after'
    if dt.hour < TIME_BEFORE_HOUR:        return 'before'
    if dt.hour >= TIME_AFTER_HOUR:        return 'after'
    return 'unknown'

def target_dir(site:str, task:str, when:str, dt:datetime) -> Path:
    return storage.target_photo_dir(dt.strftime('%Y-%m'), site, task, when)

def unique_path(path: Path) -> Path:
    return storage.unique_path(path)

async def cmd_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I sort photos into monthly folders.\n"
        "Use:\n"
        "  /grass <site> <sebelum|selepas>\n"
        "  /drainage <site> <sebelum|selepas>\n"
        "Add site: /addsite HOTEL h   |   Reset: /reset   |   Path: /where"
    )

async def cmd_where(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"SAVE_ROOT = {SAVE_ROOT}")

async def cmd_addsite(update:Update, context:ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /addsite NAME [shortcut]\\nExample: /addsite HOTEL h")
        return
    name = args[0].upper()
    shortcut = args[1].lower() if len(args) > 1 else None
    if name in SITES:
        await update.message.reply_text(f"Site exists: {name}")
        return
    SITES[name] = {name.lower(): True}
    if shortcut: SITES[name][shortcut] = True
    await update.message.reply_text(f"Added site: {name}" + (f" (/{shortcut})" if shortcut else ""))

def parse_cmd_site_when(args):
    site=None; when=None
    aliases = all_aliases()
    for tok in args:
        low=tok.lower()
        if not site and low in aliases:
            site = aliases[low]; continue
        w = normalize_when(low)
        if not when and w: when = w
    return site, when

async def cmd_grass(update:Update, context:ContextTypes.DEFAULT_TYPE):
    site, when = parse_cmd_site_when(context.args or [])
    if not site: site = detect_site_free(" ".join(context.args or []))
    if not site:
        await update.message.reply_text("Usage: /grass <site> <sebelum|selepas>\\nExample: /grass echo sebelum"); return
    if not when: when = 'before'
    set_ctx(update.effective_chat.id, site=site, task=TASK_GRASS, when=when)
    await update.message.reply_text(f"OK. Task=grass_cutting, Site={site}, When={when.upper()}. Send photos now.")

async def cmd_drainage(update:Update, context:ContextTypes.DEFAULT_TYPE):
    site, when = parse_cmd_site_when(context.args or [])
    if not site: site = detect_site_free(" ".join(context.args or []))
    if not site:
        await update.message.reply_text("Usage: /drainage <site> <sebelum|selepas>\\nExample: /drainage delta selepas"); return
    if not when: when = 'before'
    set_ctx(update.effective_chat.id, site=site, task=TASK_DRAIN, when=when)
    await update.message.reply_text(f"OK. Task=drainage_cleaning, Site={site}, When={when.upper()}. Send photos now.")

async def cmd_reset(update:Update, context:ContextTypes.DEFAULT_TYPE):
    session_manager.clear_session(update.effective_chat.id)
    await update.message.reply_text("Context cleared. Use /grass or /drainage again.")

async def safe_reply(msg: Message, text: str):
    try:
        await msg.reply_text(text, quote=False)
    except Exception as exc:
        log.warning("Failed to send upload acknowledgement: %s", exc)

async def handle_photo(update:Update, context:ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    if not msg or not msg.photo: return
    chat_id = update.effective_chat.id
    caption = msg.caption or ''
    dt = msg.date

    site = get_ctx(chat_id,'site') or detect_site_free(caption) or 'UNSPECIFIED'
    task = get_ctx(chat_id,'task') or (TASK_DRAIN if any(k in caption.lower() for k in ('longkang','parit','drain')) else TASK_GRASS)
    when = get_ctx(chat_id,'when') or detect_when_fallback(caption, dt)

    ph = msg.photo[-1]
    uniq = getattr(ph, 'file_unique_id', None) or f"{chat_id}_{msg.message_id}_{ph.file_id}"

    result = download_manager.enqueue(
        file_id=ph.file_id,
        file_unique_id=uniq,
        chat_id=chat_id,
        user_id=msg.from_user.id if msg.from_user else None,
        message_id=msg.message_id,
        message_date=dt.isoformat(),
        site=site,
        task=task,
        when=when,
        caption=caption,
    )
    if result.get("duplicate"):
        text = "Already queued/saved."
    else:
        text = "Queued for download."
    context.application.create_task(safe_reply(msg, text))

def main():
    token = os.getenv('TG_BOT_TOKEN')
    if not token:
        log.error("Missing env var: TG_BOT_TOKEN"); sys.exit(2)
    storage.ensure_dir(SAVE_ROOT)
    download_manager.start(token)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('where',     cmd_where))
    app.add_handler(CommandHandler('addsite',   cmd_addsite))
    app.add_handler(CommandHandler('grass',     cmd_grass))
    app.add_handler(CommandHandler('drainage',  cmd_drainage))
    app.add_handler(CommandHandler('reset',     cmd_reset))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    log.info("Bot up. Saving into %s", SAVE_ROOT)
    stop_signals = None if os.name == "nt" else (signal.SIGINT, signal.SIGTERM)
    try:
        app.run_polling(stop_signals=stop_signals)
    finally:
        download_manager.stop()

if __name__ == '__main__':
    try: main()
    except (KeyboardInterrupt, SystemExit): pass
