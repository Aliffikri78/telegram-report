#!/usr/bin/env sh
set -eu

# Raise file limit inside container (Docker also needs host ulimit set)
ulimit -n 65535 || true

# Start the Telegram bot (non-blocking) if present
if [ -f "/app/app/telegram_bot.py" ]; then
  echo "[run_all] Starting /app/app/telegram_bot.py ..."
  python3 /app/app/telegram_bot.py &
fi

# Optional notify bridge (won't crash if missing deps)
if [ -f "notify_api.py" ]; then
  echo "[run_all] Starting notify_api.py on :5000 ..."
  python3 notify_api.py &
fi

# Finally start the canonical web entry (blocking)
echo "[run_all] Starting web via python3 /app/web.py on :${PORT:-8080} ..."
exec python3 /app/web.py
