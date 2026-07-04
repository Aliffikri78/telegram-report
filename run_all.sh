#!/usr/bin/env sh
set -eu

# Raise file limit inside container (Docker also needs host ulimit set)
ulimit -n 65535 || true

# Start the Telegram bot (non-blocking) if present
if [ -f "telegram_bot.py" ]; then
  echo "[run_all] Starting telegram_bot.py ..."
  python3 telegram_bot.py &
fi

# Optional notify bridge (won't crash if missing deps)
if [ -f "notify_api.py" ]; then
  echo "[run_all] Starting notify_api.py on :5000 ..."
  python3 notify_api.py &
fi

# Finally start your ORIGINAL web entry (blocking)
# This keeps the behavior of your old build (no Gunicorn, no split)
echo "[run_all] Starting web via python3 entrypoint.py on :${PORT:-8080} ..."
exec python3 entrypoint.py
