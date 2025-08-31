#!/usr/bin/env bash
set -e
if [[ -z "$TG_BOT_TOKEN" ]]; then
  echo "ERROR: TG_BOT_TOKEN not set"; exit 2
fi
mkdir -p "$SAVE_ROOT" /data/reports
echo "[boot] SAVE_ROOT=$SAVE_ROOT  reports=/data/reports  TZ=${TZ:-UTC}"
python /app/telegram_bot.py &
python /app/web.py
