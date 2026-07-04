
#!/usr/bin/env python3
import os, json, threading, time, urllib.request, urllib.parse
from pathlib import Path
from flask import Flask, jsonify, request, send_file, render_template

# ---------- Config / Paths ----------
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/data/config.json"))
SAVE_ROOT   = Path(os.getenv("SAVE_ROOT", "/data/photos"))
REPORTS_ROOT= Path(os.getenv("REPORTS_ROOT", "/data/reports"))

# Telegram env (optional)
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "").strip()

DEFAULTS = {
    "company": "HW UNGGUL (901587-V)",
    "image_height_cm": 5.0,
    "label_lang": "ms",
    "threshold": 0.95,
    "visual_weight": 0.50,
    "photos_root": str(SAVE_ROOT),
    "reports_root": str(REPORTS_ROOT),
}

def _load_defaults():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**DEFAULTS, **data}
        except Exception:
            pass
    return DEFAULTS.copy()

def _save_defaults(d: dict):
    allowed = {k: d[k] for k in DEFAULTS if k in d}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(allowed, indent=2), encoding="utf-8")
    return allowed

RUNTIME_DEFAULTS = _load_defaults()

# ---------- Flask ----------
app = Flask(__name__, template_folder="templates", static_folder="static")
STATE = {"running": False, "last_report": None, "msg": ""}

def detect_cuda():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

@app.get("/health")
def health():
    return jsonify(ok=True, cuda=detect_cuda())

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/list")
def api_list():
    root = Path(RUNTIME_DEFAULTS.get("photos_root", str(SAVE_ROOT)))
    months = sorted([p.name for p in root.glob("*") if p.is_dir()])
    sites_by_month = {}
    for m in months:
        base = root / m
        sites_by_month[m] = sorted([p.name for p in base.glob("*") if p.is_dir()])
    return jsonify(months=months, sites_by_month=sites_by_month)

# ---------- Defaults API ----------
@app.get("/api/defaults")
def get_defaults():
    return jsonify(RUNTIME_DEFAULTS)

@app.post("/api/default")
def set_default():
    data = request.get_json(silent=True) or {}
    payload = data.get("defaults", data) if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return jsonify(ok=False, msg="Invalid payload"), 400
    saved = _save_defaults({**RUNTIME_DEFAULTS, **payload})
    RUNTIME_DEFAULTS.update(saved)
    return jsonify(ok=True, saved_to=str(CONFIG_PATH), defaults=RUNTIME_DEFAULTS)

# ---------- Telegram (built-in, stdlib only) ----------
def _tg_send(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return  # silently skip if not configured
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
    except Exception as e:
        print("Telegram notify failed:", e)

# ---------- Report (delegates to report_menu_table) ----------
def _run_report(form):
    try:
        STATE["msg"] = "Preparing..."
        time.sleep(0.15)

        merged = {**RUNTIME_DEFAULTS, **(form or {})}
        month = (merged.get("month") or "").strip()
        site  = (merged.get("site")  or "").strip()
        task  = (merged.get("task")  or "grass_cutting").strip() or "grass_cutting"  # ALWAYS present

        photos_root  = Path(merged.get("photos_root",  str(SAVE_ROOT)))
        reports_root = Path(merged.get("reports_root", str(REPORTS_ROOT)))
        reports_root.mkdir(parents=True, exist_ok=True)

        th    = merged.get("threshold", 0.95)
        vw    = merged.get("visual_weight", 0.5)
        img_h = merged.get("image_height_cm", 5.0)
        lang  = merged.get("label_lang", "ms")
        comp  = merged.get("company", "")

        import report_menu_table as builder

        # 1) Try kwargs with Path objects
        kw = dict(
            reports_root=reports_root,
            photos_root=photos_root,
            month=month,
            site=site,
            task=task,
            threshold=th,
            visual_weight=vw,
            image_height_cm=img_h,
            label_lang=lang,
            company=comp,
        )
        out_path = None
        try:
            out_path = builder.build_report(**kw)
        except TypeError:
            # 2) Positional minimal (Path roots)
            try:
                out_path = builder.build_report(reports_root, month, site, task)
            except TypeError:
                # 3) Extended positional (Path roots + params)
                out_path = builder.build_report(
                    reports_root, photos_root, month, site, task,
                    th, vw, img_h, lang, comp
                )

        # normalize to Path and make absolute if needed
        if out_path:
            out_path = Path(out_path)
            if not out_path.is_absolute():
                out_path = reports_root / out_path

        if out_path and out_path.exists():
            STATE["last_report"] = str(out_path)
            STATE["msg"] = f"✅ Report generated: {out_path.name}"
            _tg_send(f"{STATE['msg']}\n{out_path}")
        else:
            STATE["msg"] = "⚠️ Report builder did not produce a file."
            _tg_send(STATE["msg"])
        STATE["running"] = False

    except Exception as e:
        STATE["msg"] = f"⚠️ Report failed: {e}"
        _tg_send(STATE["msg"])
        STATE["running"] = False

@app.post("/api/start")
def api_start():
    if STATE["running"]:
        return jsonify(ok=False, msg="Already running"), 409
    form = request.get_json(silent=True) or {}
    if not form.get("month") or not form.get("site"):
        return jsonify(ok=False, msg="Month and site required"), 400
    STATE["running"] = True
    STATE["msg"] = "Starting…"
    threading.Thread(target=_run_report, args=(form,), daemon=True).start()
    return jsonify(ok=True)

@app.get("/api/status")
def api_status():
    return jsonify(running=STATE["running"], last_report=STATE["last_report"], msg=STATE["msg"])

@app.get("/download")
def download():
    p = STATE.get("last_report")
    if not p or not Path(p).exists():
        return jsonify(ok=False, msg="No report yet"), 404
    return send_file(p, as_attachment=True)

def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
