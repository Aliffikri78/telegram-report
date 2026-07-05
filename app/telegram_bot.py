#!/usr/bin/env python3
import os, re, sys, logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from telegram import Update, Message
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

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
SAVE_ROOT = Path(os.getenv('SAVE_ROOT','/data/photos')).resolve()

CTX: Dict[int, Dict[str,str]] = {}

def set_ctx(chat_id:int, site:Optional[str]=None, task:Optional[str]=None, when:Optional[str]=None):
    CTX.setdefault(chat_id, {})
    if site: CTX[chat_id]['site'] = site
    if task: CTX[chat_id]['task'] = task
    if when: CTX[chat_id]['when'] = when

def get_ctx(chat_id:int, key:str) -> Optional[str]:
    return CTX.get(chat_id, {}).get(key)

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
    month=dt.strftime('%Y-%m')
    folder=SAVE_ROOT / month / site / task / (when if when in ('before','after') else 'unknown')
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def unique_path(path: Path) -> Path:
    if not path.exists(): return path
    stem, suf, parent = path.stem, path.suffix, path.parent
    i=1
    while True:
        cand = parent / f"{stem}-{i}{suf}"
        if not cand.exists(): return cand
        i+=1

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
    CTX.pop(update.effective_chat.id, None)
    await update.message.reply_text("Context cleared. Use /grass or /drainage again.")

async def handle_photo(update:Update, context:ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    if not msg or not msg.photo: return
    chat_id = update.effective_chat.id
    caption = msg.caption or ''
    dt = msg.date

    site = get_ctx(chat_id,'site') or detect_site_free(caption) or 'UNSPECIFIED'
    task = get_ctx(chat_id,'task') or (TASK_DRAIN if any(k in caption.lower() for k in ('longkang','parit','drain')) else TASK_GRASS)
    when = get_ctx(chat_id,'when') or detect_when_fallback(caption, dt)

    folder = target_dir(site, task, when, dt)
    ph = msg.photo[-1]
    file = await ph.get_file()
    ts = dt.strftime('%Y%m%d_%H%M%S')
    uniq = getattr(ph, 'file_unique_id','') or 'nofid'
    safe = re.sub(r'[^a-zA-Z0-9_-]+','_', caption.strip())[:40] or 'photo'
    fname = f"{site.lower()}_{task}_{when}_{ts}_{uniq}_{safe}.jpg"
    dest = unique_path(folder / fname)

    await file.download_to_drive(custom_path=str(dest))
    log.info("Saved %s", dest)
    try: await msg.reply_text(f"Saved: {dest}", quote=False)
    except Exception: pass

def main():
    token = os.getenv('TG_BOT_TOKEN')
    if not token:
        log.error("Missing env var: TG_BOT_TOKEN"); sys.exit(2)
    SAVE_ROOT.mkdir(parents=True, exist_ok=True)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('where',     cmd_where))
    app.add_handler(CommandHandler('addsite',   cmd_addsite))
    app.add_handler(CommandHandler('grass',     cmd_grass))
    app.add_handler(CommandHandler('drainage',  cmd_drainage))
    app.add_handler(CommandHandler('reset',     cmd_reset))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    log.info("Bot up. Saving into %s", SAVE_ROOT)
    app.run_polling()

if __name__ == '__main__':
    try: main()
    except (KeyboardInterrupt, SystemExit): pass
