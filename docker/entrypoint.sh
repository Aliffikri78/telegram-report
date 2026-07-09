#!/usr/bin/env bash
set -euo pipefail
cd /app || { echo "[entrypoint] /app not found"; exit 1; }

if [[ -f /app/run_all.sh ]]; then
  chmod +x /app/run_all.sh || true
  echo "[entrypoint] Executing run_all.sh"
  exec bash -lc "/app/run_all.sh"
fi

if [[ -f /app/web.py ]]; then
  echo "[entrypoint] run_all.sh not found, starting python /app/web.py"
  exec python3 /app/web.py
fi

echo "[entrypoint] No run target found. Sleeping..."
exec tail -f /dev/null
