# Single-Container Patch (uses your OLD build pattern)

## Build
docker build -t telegram-report-allinone:latest .

## Run (Docker)
docker run -d --name telegram-report-allinone \
  -p 8080:8080 -p 5000:5000 \
  --gpus all --ulimit nofile=65535:65535 \
  -e TELEGRAM_TOKEN=$TELEGRAM_TOKEN -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  -v /mnt/user/NAS/telegram_report_automation/photos:/data/photos \
  -v /mnt/user/NAS/telegram_report_automation/reports:/data/reports \
  telegram-report-allinone:latest

## Unraid
Copy unraid/telegram-report-allinone.xml to:
  /boot/config/plugins/dockerMan/templates-user/
Then add container from template.

Notes:
- Keeps your original `python3 entrypoint.py` web server AND the bot running together.
- Installs missing deps: requests, flask, python-telegram-bot.
- GPU must be enabled on host (Unraid NVIDIA plugin) and container (`--gpus all`).
