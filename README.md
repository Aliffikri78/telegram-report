
# All-in-One Pack – Modern Web UI + GPU Matching + Strict DOC Layout

Everything in one: modern web UI (Tailwind/Alpine), Torch CUDA matching, strict DOCX generator, Telegram notify, Docker GPU setup.

## Folders
```
data/photos/{MONTH}/{SITE}/{TASK}/before/*.jpg
data/photos/{MONTH}/{SITE}/{TASK}/after/*.jpg
```
Output DOCX saved to: `data/reports/`

## Quick Start (Docker, with GPU)
1) Ensure Unraid has **NVIDIA Driver** and Docker GPU enabled.
2) Create `.env` (optional for Telegram):
```
TG_BOT_TOKEN=
TG_CHAT_ID=
```
3) Build & run:
```
docker compose up --build
```
4) Open: `http://<UNRAID-IP>:8080`
5) Pick **Month/Site**, Task, thresholds, and click **Generate Report**.
6) Click **Download latest report** once ready.

## CUDA
- Health indicator shows CUDA availability. Matching uses GPU hist-cosine when available, auto-fallback to CPU otherwise.

## Layout (follows your DOC spec exactly)
- Header: Times New Roman 22, centered, not bold.
- Zone: Times New Roman 12, **bold + underline**, centered.
- Section title: Times New Roman 12, **bold**, LEFT-aligned.
- Table: images on top, **labels below** (Sebelum/Selepas or Before/After).
- Margins: **2.0 cm** all sides.
- Only matched pairs kept (filename + visual score).

## Dev (no Docker)
```
pip install -r requirements.txt
python app.py
```
Then open http://localhost:8080

## Notes
- Torch wheels pinned to CUDA 11.8 for GTX 1050 Ti (Pascal) compatibility.
- You can tweak `THRESHOLD` and `VISUAL_WEIGHT` in UI.


---
## Telegram Bot (Auto-sort + Report Duration)
Commands:
- `/grass <SITE> <sebelum|selepas> [YYYY-MM]`
- `/drainage <SITE> <sebelum|selepas> [YYYY-MM]`
- `/done` → sends a single "Finished download & sorting" summary (not per photo)
- `/build <SITE> <grass|drainage> [YYYY-MM]` → generates DOCX and replies with how long it took

Env required:
```
TG_BOT_TOKEN=xxx
# (Optional) TG_CHAT_ID used by Web UI notifications; not required for bot commands
```

Photos are auto-sorted into:
```
/data/photos/{YYYY-MM}/{SITE}/{grass_cutting|drainage_cleaning}/{before|after}/
```

The container runs **Web UI + Telegram bot in the same process**.
