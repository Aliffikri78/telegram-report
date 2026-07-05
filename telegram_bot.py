#!/usr/bin/env python3
import os, time
from pathlib import Path
from functools import partial
from typing import Dict, Any
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from report_menu_table import build_report

SAVE_ROOT = Path(os.getenv("SAVE_ROOT", "/data/photos"))
REPORTS_ROOT = Path(os.getenv("REPORTS_ROOT", "/data/reports"))
COMPANY = os.getenv("COMPANY", "HW UNGGUL (901587-V)")

# Per-chat session
SESS: Dict[int, Dict[str, Any]] = {}

HELP = (
    "📸 *Auto-sort Bot*\n"
    "`/grass <SITE> <sebelum|selepas> [YYYY-MM]`\n"
    "`/drainage <SITE> <sebelum|selepas> [YYYY-MM]`\n"
    "`/done` → single finish summary\n"
    "`/build <SITE> <grass|drainage> [YYYY-MM]` → build report + duration\n"
)

def _task_map(cmd: str) -> str:
    return "grass_cutting" if cmd == "grass" else "drainage_cleaning"

def _phase_map(word: str) -> str:
    word = word.lower()
    if word in ("sebelum", "before"): return "before"
    if word in ("selepas", "after"): return "after"
    return ""

def start(update: Update, ctx: CallbackContext):
    update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

def set_route(task_cmd: str, update: Update, ctx: CallbackContext):
    args = ctx.args
    if len(args) < 2:
        update.message.reply_text(f"Usage: /{task_cmd} <SITE> <sebelum|selepas> [YYYY-MM]")
        return
    site = args[0].upper()
    phase = _phase_map(args[1])
    if not phase:
        update.message.reply_text("Phase must be `sebelum` or `selepas`.")
        return
    month = args[2] if len(args) >= 3 else time.strftime("%Y-%m")
    chat_id = update.effective_chat.id
    SESS[chat_id] = {
        "task": _task_map(task_cmd),
        "site": site,
        "phase": phase,
        "month": month,
        "count": 0,
        "started_at": time.time(),
    }
    update.message.reply_text(
        f"✅ Routing photos to *{site}* / *{SESS[chat_id]['task']}* / *{phase}* for *{month}*.\n"
        f"Send photos now, then `/done`.",
        parse_mode=ParseMode.MARKDOWN
    )

def handle_photo(update: Update, ctx: CallbackContext):
    msg = update.message
    chat_id = update.effective_chat.id
    sess = SESS.get(chat_id)
    if not sess:
        msg.reply_text("No active routing. Use /grass or /drainage first.")
        return

    photo = msg.photo[-1]
    file = ctx.bot.get_file(photo.file_id)

    out_dir = SAVE_ROOT / sess["month"] / sess["site"] / sess["task"] / sess["phase"]
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time()*1000)}_{msg.from_user.id}.jpg"
    out_path = out_dir / fname
    file.download(custom_path=str(out_path))
    sess["count"] += 1

def done_cmd(update: Update, ctx: CallbackContext):
    chat_id = update.effective_chat.id
    sess = SESS.pop(chat_id, None)
    if not sess:
        update.message.reply_text("No active routing session.")
        return
    elapsed = time.time() - sess["started_at"]
    update.message.reply_text(
        f"📦 Finished download & sorting.\n"
        f"• Month: *{sess['month']}*\n"
        f"• Site: *{sess['site']}*\n"
        f"• Task: *{sess['task']}*\n"
        f"• Phase: *{sess['phase']}*\n"
        f"• Saved: *{sess['count']}*\n"
        f"• Took: *{elapsed:.1f}s*",
        parse_mode=ParseMode.MARKDOWN
    )

def build_cmd(update: Update, ctx: CallbackContext):
    args = ctx.args
    if len(args) < 2:
        update.message.reply_text("Usage: /build <SITE> <grass|drainage> [YYYY-MM]")
        return
    site = args[0].upper()
    task_word = args[1].lower()
    task = "grass_cutting" if task_word.startswith("grass") else "drainage_cleaning"
    month = args[2] if len(args) >= 3 else time.strftime("%Y-%m")

    update.message.reply_text("🛠️ Generating report…")
    t0 = time.time()
    try:
        out = build_report(
            SAVE_ROOT, REPORTS_ROOT, month, site, task,
            company=COMPANY,
            threshold=float(os.getenv("THRESHOLD","0.7")),
            visual_weight=float(os.getenv("VISUAL_WEIGHT","0.5")),
            image_height_cm=float(os.getenv("IMG_H_CM","5.0")),
            label_lang=os.getenv("LABEL_LANG","ms"),
        )
        dt = time.time() - t0
        try:
            with open(out, "rb") as f:
                ctx.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=out.name,
                    caption=f"✅ Report ready in {dt:.1f}s\n{out}"
                )
        except Exception:
            update.message.reply_text(f"✅ Report generated in {dt:.1f}s\n{out}")
    except Exception as e:
        update.message.reply_text(f"❌ Error: {e}")

def main():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        print("TG_BOT_TOKEN not set; Telegram bot disabled.")
        return

    updater = Updater(token, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", start))
    dp.add_handler(CommandHandler("grass", partial(set_route, "grass")))
    dp.add_handler(CommandHandler("drainage", partial(set_route, "drainage")))
    dp.add_handler(CommandHandler("done", done_cmd))
    dp.add_handler(CommandHandler("build", build_cmd))
    dp.add_handler(MessageHandler(Filters.photo, handle_photo))

    updater.start_polling()
    print("Telegram bot running (threaded, no signals).")
    # block without installing signal handlers
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass