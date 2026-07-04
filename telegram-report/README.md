# Telegram Report (Bot + Web + DOCX Generator)

All-in-one Telegram photo sorter + report generator with a simple Web UI.

## Quick start (Docker)

```bash
git clone https://github.com/<your-username>/telegram-report.git
cd telegram-report
# Put your token into docker-compose.yml
docker compose up -d
```

- Bot saves photos to `./data/photos/YYYY-MM/<SITE>/<TASK>/<before|after>`
- Reports are written to `./data/reports/*.docx`
- Web UI at http://<server-ip>:8080

## Run a report (shell inside container)

```bash
docker exec -it telegram-report python /app/report_menu_table.py
```

## Environment variables

- `TG_BOT_TOKEN` (required)
- `SAVE_ROOT` default `/data/photos`
- `TIME_BEFORE_HOUR` default `12`
- `TIME_AFTER_HOUR` default `15`

Tuning (matching speed/quality, used by Web UI fast path):

- `FAST_MAX_SIDE` (default 1600)
- `FAST_NFEATURES` (default 600)
- `FAST_TOPK` (default 5)
- `FAST_RATIO` (default 0.75)
