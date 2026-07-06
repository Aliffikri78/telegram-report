#!/usr/bin/env python3
import os, re, subprocess, threading, uuid
from pathlib import Path
from typing import List, Dict
from urllib import parse, request as urlrequest
from flask import Flask, render_template, request, Response, jsonify, send_file

from PIL import Image
import cv2
from docx import Document
from docx.shared import Cm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL

try:
    import importlib.util

    from jobs.manager import JobManager
    from projects.manager import ProjectManager
    from storage.storage import Storage
    from vision.analyze import analyze_folder
    from vision.cache import VisionCache
    from vision.matcher import match_pairs

    queue_manager_path = Path(__file__).resolve().parent / "queue" / "manager.py"
    spec = importlib.util.spec_from_file_location("local_queue_manager", queue_manager_path)
    queue_manager_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(queue_manager_module)
    QueueManager = queue_manager_module.QueueManager
except ImportError:
    from app.jobs.manager import JobManager
    from app.projects.manager import ProjectManager
    from app.queue.manager import QueueManager
    from app.storage.storage import Storage
    from app.vision.analyze import analyze_folder
    from app.vision.cache import VisionCache
    from app.vision.matcher import match_pairs

storage = Storage()
project_manager = ProjectManager()
DATA_ROOT = storage.photos_root
REPORT_ROOT = storage.ensure_dir(storage.reports_root)

def list_dirs(p: Path):
    return storage.list_dirs(p)

def is_month_name(name: str) -> bool:
    import re
    return re.match(r"^\d{4}-\d{2}$", name) is not None

def scan_months_sites() -> Dict[str, list]:
    out = {}
    if not DATA_ROOT.exists(): return out
    configured = {task["slug"] for task in project_manager.list_tasks()}
    legacy = {"grass_cutting", "drainage_cleaning"}
    task_slugs = configured | legacy
    for m in list_dirs(DATA_ROOT):
        if not is_month_name(m.name): continue
        sites = []
        for s in list_dirs(m):
            if any((s / task_slug).exists() for task_slug in task_slugs):
                sites.append(s.name)
        if sites:
            out[m.name] = sites
    return out

def report_tasks():
    tasks = project_manager.list_tasks()
    by_slug = {task["slug"]: task for task in tasks}
    ordered = []
    for slug in ("grass_cutting", "drainage_cleaning"):
        if slug in by_slug:
            ordered.append(by_slug.pop(slug))
    ordered.extend(by_slug.values())
    return ordered

def report_defaults():
    tasks = report_tasks()
    default_task = next(
        (task for task in tasks if task["slug"] == "grass_cutting"),
        tasks[0] if tasks else {"slug": "", "title": "1. Grass Cutting", "name": "Grass Cutting"},
    )
    return {
        "company": os.getenv("COMPANY", "HW UNGGUL (901587-V)"),
        "zone": "ZONE",
        "title": default_task.get("title") or default_task.get("name") or "1. Grass Cutting",
        "threshold": os.getenv("THRESHOLD", "0.70"),
        "task": default_task["slug"],
    }

IMG_H_CM = float(os.getenv("IMG_H_CM", "4.3"))

class CancelledJob(Exception):
    pass

def notify_telegram(message: str) -> None:
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return

    def send():
        try:
            data = parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
            req = urlrequest.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            urlrequest.urlopen(req, timeout=5).close()
        except Exception:
            pass

    threading.Thread(target=send, daemon=True).start()

def detect_gpu_status() -> Dict:
    status = {
        "nvidia_detected": False,
        "gpu_name": None,
        "opencv_cuda_available": False,
        "opencv_cuda_device_count": 0,
        "opencv_cuda_build": False,
        "error": None,
    }
    errors = []

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            status["nvidia_detected"] = bool(names)
            status["gpu_name"] = names[0] if names else None
        elif result.stderr.strip():
            errors.append(result.stderr.strip())
    except Exception as exc:
        errors.append(str(exc))

    try:
        build_info = cv2.getBuildInformation()
        status["opencv_cuda_build"] = "NVIDIA CUDA" in build_info and "YES" in build_info.split("NVIDIA CUDA", 1)[1][:80]
    except Exception as exc:
        errors.append(f"OpenCV build info unavailable: {exc}")

    try:
        if hasattr(cv2, "cuda"):
            count = cv2.cuda.getCudaEnabledDeviceCount()
            status["opencv_cuda_device_count"] = int(count)
            status["opencv_cuda_available"] = count > 0
    except Exception as exc:
        errors.append(f"OpenCV CUDA unavailable: {exc}")

    status["error"] = "; ".join(errors) if errors else None
    return status

def crop_to_4x3(im: Image.Image) -> Image.Image:
    w,h = im.size; target = 4/3; cur = w/h
    if abs(cur-target) < 0.005: return im
    if cur > target:
        new_w = int(h*target); x0 = (w-new_w)//2; return im.crop((x0,0,x0+new_w,h))
    new_h = int(w/target); y0 = (h-new_h)//2; return im.crop((0,y0,w,y0+new_h))

def load_images(folder: Path):
    exts = {".jpg",".jpeg",".png",".bmp",".tif",".tiff",".webp"}
    return storage.list_files(folder, exts)

def build_report(input_root: Path, out_path: Path, company: str, zone: str, title: str,
                 threshold: float, progress: dict, should_cancel=None, backend="cpu"):
    should_cancel = should_cancel or (lambda: False)
    if should_cancel():
        raise CancelledJob("Cancelled by user")
    before_dir = input_root / "before"
    after_dir  = input_root / "after"
    befores = load_images(before_dir)
    afters  = load_images(after_dir)

    progress.update(total=len(befores), state="preprocess", done=0,
                    before=len(befores), after=len(afters))

    if not befores or not afters:
        raise RuntimeError("No images in before/ or after/")

    pairs = match_pairs(
        befores=befores,
        afters=afters,
        threshold=threshold,
        progress=progress,
        should_cancel=should_cancel,
        cancelled_exception=CancelledJob,
        backend=backend,
    )

    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.right_margin = Cm(2.0)
        section.left_margin = Cm(3.0)
        section.header_distance = Cm(1.0)
        section.footer_distance = Cm(1.0)
    normal = doc.styles["Normal"].paragraph_format
    normal.space_before = Pt(0)
    normal.space_after = Pt(0)
    normal.line_spacing_rule = WD_LINE_SPACING.SINGLE

    def add_header(company, zone):
        p = doc.add_paragraph(); r=p.add_run(company.upper()); r.font.size=Pt(14)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2 = doc.add_paragraph()
        r2 = p2.add_run(zone.upper())
        r2.bold = True
        r2.underline = True
        r2.font.size = Pt(12)
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    def add_title(title):
        p = doc.add_paragraph(); r=p.add_run(title); r.bold=True; r.font.size=Pt(12); p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    def add_pair_table(before_path, after_path):
        from docx.oxml.shared import OxmlElement, qn
        tbl = doc.add_table(rows=2, cols=2)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        for row in tbl.rows:
            for cell in row.cells:
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        tbl_pr = tbl._tbl.tblPr
        borders = OxmlElement('w:tblBorders')
        for edge in ('top','left','bottom','right','insideH','insideV'):
            el = OxmlElement(f'w:{edge}'); el.set(qn('w:val'), 'nil'); borders.append(el)
        tbl_pr.append(borders)
        try:
            im = Image.open(before_path).convert("RGB"); im = crop_to_4x3(im)
            tmp = REPORT_ROOT / f"~tmp_b.jpg"; im.save(tmp, quality=90)
            cell = tbl.cell(0,0).paragraphs[0]; cell.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.add_run().add_picture(str(tmp), height=Cm(IMG_H_CM)); storage.unlink(tmp)
        except Exception:
            tbl.cell(0,0).text="(gambar gagal)"
        try:
            im = Image.open(after_path).convert("RGB"); im = crop_to_4x3(im)
            tmp = REPORT_ROOT / f"~tmp_a.jpg"; im.save(tmp, quality=90)
            cell = tbl.cell(0,1).paragraphs[0]; cell.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.add_run().add_picture(str(tmp), height=Cm(IMG_H_CM)); storage.unlink(tmp)
        except Exception:
            tbl.cell(0,1).text="(gambar gagal)"
        pL = tbl.cell(1,0).paragraphs[0]; pL.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rL = pL.add_run("Sebelum"); rL.bold=True; rL.font.size=Pt(11)
        pR = tbl.cell(1,1).paragraphs[0]; pR.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rR = pR.add_run("Selepas"); rR.bold=True; rR.font.size=Pt(11)
        doc.add_paragraph().paragraph_format.space_after = Pt(6)

    add_header(company, zone)
    add_title(title)
    per_page = 4
    for i in range(min(per_page, len(pairs))):
        if should_cancel():
            raise CancelledJob("Cancelled by user")
        add_pair_table(pairs[i][0], pairs[i][1])
    if len(pairs) > per_page:
        idx = per_page; pages = 1
        while idx < len(pairs):
            if should_cancel():
                raise CancelledJob("Cancelled by user")
            doc.add_page_break()
            add_header(company, zone)
            for k in range(per_page):
                j = idx + k
                if j >= len(pairs): break
                if should_cancel():
                    raise CancelledJob("Cancelled by user")
                add_pair_table(pairs[j][0], pairs[j][1])
            idx += per_page; pages += 1
            progress["pages"] = pages

    if should_cancel():
        raise CancelledJob("Cancelled by user")
    doc.save(out_path)
    progress.update(state="done", download=str(out_path))

app = Flask(__name__)

job_manager = JobManager()
queue_manager = QueueManager()
REQUIRED_JOB_PAYLOAD_FIELDS = ("month", "site", "task", "company", "zone", "title", "threshold")
RETRYABLE_STATUSES = {"error", "failed", "cancelled"}
RERUN_STATUSES = {"done"}

def sse_stream(job_id):
    q = job_manager.get_events(job_id)
    yield f"event: ping\ndata: keepalive\n\n"
    while True:
        msg = q.get()
        yield f"data: {msg}\n\n"

def push(job_id):
    state = job_manager.get_state(job_id)
    if state:
        job_manager.publish(job_id, dict(state))

def create_report_job(payload):
    month = str(payload["month"]).strip()
    site = str(payload["site"]).strip()
    task = str(payload["task"]).strip()
    company = str(payload["company"]).strip()
    zone = str(payload["zone"]).strip()
    title = str(payload["title"]).strip()
    threshold = float(payload["threshold"])
    backend = str(payload.get("backend", "cpu")).strip().lower() or "cpu"
    job_id = uuid.uuid4().hex
    job_payload = dict(payload)
    job_payload["threshold"] = threshold
    job_payload["backend"] = backend
    job_manager.create(
        job_id,
        name=f"{month} {site} {task}",
        payload=job_payload,
        state={"state":"queued","done":0,"total":0,"matched":0,"unmatched":0,"before":0,"after":0,"pages":0},
    )
    queue_manager.submit(run_job, job_id, month, site, task, company, zone, title, threshold, backend)
    return job_id

def clean_saved_payload(job):
    payload = dict((job or {}).get("payload") or {})
    payload.pop("_state", None)
    return payload

def validate_report_payload(payload):
    missing = [field for field in REQUIRED_JOB_PAYLOAD_FIELDS if payload.get(field) in (None, "")]
    if missing:
        return f"Missing saved job payload fields: {', '.join(missing)}"
    try:
        float(payload["threshold"])
    except (TypeError, ValueError):
        return "Invalid saved job threshold"
    return None

def run_job(job_id, month, site, task, company, zone, title, threshold, backend="cpu"):
    try:
        job = job_manager.get_state(job_id)
        if job_manager.is_cancelled(job_id):
            raise CancelledJob("Cancelled by user")
        job.update(state="starting", done=0, total=0)
        notify_telegram(f"Report job started: {month} {site} {task}")
        push(job_id)
        input_root = DATA_ROOT / month / site / task
        legacy_name = "drainage" if task == "drainage_cleaning" else task
        out_name = f"{month}_{site}_{legacy_name}.docx"
        out_path = REPORT_ROOT / out_name
        build_report(input_root, out_path, company, zone, title, threshold, job, lambda: job_manager.is_cancelled(job_id), backend)
        notify_telegram(f"Report job completed: {out_name}")
        push(job_id)
    except CancelledJob:
        job = job_manager.get_state(job_id)
        if job:
            job.update(state="cancelled", error="Cancelled by user", download=None)
        push(job_id)
    except Exception as e:
        job = job_manager.get_state(job_id)
        if job:
            job.update(state="error", error=str(e))
        notify_telegram(f"Report job failed: {month} {site} {task}\n{e}")
        push(job_id)

@app.route("/")
def index():
    ms = scan_months_sites()
    return render_template("index.html", months_sites=ms, tasks=report_tasks(), defaults=report_defaults())

@app.get("/settings")
def settings():
    return render_template(
        "settings.html",
        companies=project_manager.list_companies(),
        projects=project_manager.list_projects(),
        sites=project_manager.list_sites(),
        categories=project_manager.list_categories(),
        tasks=project_manager.list_tasks(),
    )

@app.get("/api/settings/<entity>")
def settings_list(entity):
    try:
        mapping = {
            "companies": project_manager.list_companies,
            "projects": project_manager.list_projects,
            "sites": project_manager.list_sites,
            "categories": project_manager.list_categories,
            "tasks": project_manager.list_tasks,
        }
        return jsonify(ok=True, items=mapping[entity]())
    except KeyError:
        return jsonify(ok=False, error="Unknown settings entity"), 404

@app.post("/api/settings/<entity>")
def settings_create(entity):
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        return jsonify(ok=True, item=project_manager.create(entity, data))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400

@app.put("/api/settings/<entity>/<item_id>")
def settings_update(entity, item_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        return jsonify(ok=True, item=project_manager.update(entity, item_id, data))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400

@app.delete("/api/settings/<entity>/<item_id>")
def settings_delete(entity, item_id):
    try:
        project_manager.delete(entity, item_id)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400

@app.post("/start")
def start():
    data = request.form
    month = data.get("month","").strip()
    site  = data.get("site","").strip()
    defaults = report_defaults()
    task  = data.get("task", defaults["task"]).strip()
    company = data.get("company", defaults["company"]).strip()
    zone    = data.get("zone", f"{site} ZONE").strip()
    task_row = project_manager.task_by_slug(task)
    title   = data.get("title", (task_row or {}).get("title") or defaults["title"]).strip()
    threshold = float(data.get("threshold", defaults["threshold"]))
    backend = data.get("backend", "cpu").strip().lower() or "cpu"
    if not month or not site:
        return jsonify({"ok": False, "error": "Please choose a month and a site"}), 400
    payload = {"month": month, "site": site, "task": task, "company": company, "zone": zone, "title": title, "threshold": threshold, "backend": backend}
    job_id = create_report_job(payload)
    return jsonify({"ok": True, "job_id": job_id})

@app.get("/progress/<job_id>")
def progress(job_id):
    if not job_manager.get_events(job_id):
        return "no such job", 404
    return Response(sse_stream(job_id), mimetype="text/event-stream")

@app.get("/api/jobs")
def jobs_list():
    limit = request.args.get("limit", "50")
    status = request.args.get("status", "").strip()
    search = request.args.get("search", "").strip()
    try:
        jobs = job_manager.list_jobs(int(limit), status=status, search=search)
    except ValueError:
        jobs = job_manager.list_jobs(status=status, search=search)
    return jsonify({"ok": True, "jobs": jobs, "counts": job_manager.job_counts()})

@app.get("/api/jobs/<job_id>")
def jobs_get(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({"ok": True, "job": job})

@app.post("/api/jobs/<job_id>/retry")
def jobs_retry(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    status = (job.get("status") or "").lower()
    if status not in RETRYABLE_STATUSES and status not in RERUN_STATUSES:
        return jsonify({"ok": False, "error": "Only failed, cancelled, or done jobs can be retried"}), 400

    payload = clean_saved_payload(job)
    error = validate_report_payload(payload)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    mode = "duplicate" if status in RERUN_STATUSES else "retry"
    payload["duplicate_of" if mode == "duplicate" else "retry_of"] = job_id
    new_job_id = create_report_job(payload)
    return jsonify({"ok": True, "job_id": new_job_id, "mode": mode})

@app.post("/api/jobs/<job_id>/cancel")
def jobs_cancel(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    status = (job.get("status") or "").lower()
    if status not in {"queued", "starting", "preprocess", "matching", "running"}:
        return jsonify({"ok": False, "error": "Only running or queued jobs can be cancelled"}), 400
    job_manager.cancel(job_id)
    notify_telegram(f"Report job cancelled: {job.get('name') or job_id}")
    return jsonify({"ok": True, "job_id": job_id, "status": "cancelled"})

@app.get("/api/folders/scan")
def folders_scan():
    months_sites = scan_months_sites()
    return jsonify(
        {
            "ok": True,
            "months_sites": months_sites,
            "counts": {
                "months": len(months_sites),
                "sites": sum(len(sites) for sites in months_sites.values()),
            },
        }
    )

@app.get("/api/system/gpu")
def system_gpu():
    return jsonify({"ok": True, "gpu": detect_gpu_status()})

@app.post("/api/vision/analyze")
def vision_analyze():
    data = request.get_json(silent=True) or request.form.to_dict()
    month = str(data.get("month", "")).strip()
    site = str(data.get("site", "")).strip()
    task = str(data.get("task", "")).strip()
    try:
        max_distance = max(1, min(int(data.get("max_distance", 5) or 5), 10))
    except (TypeError, ValueError):
        max_distance = 5
    if not month or not site or not task:
        return jsonify({"ok": False, "error": "month, site, and task are required"}), 400

    root = DATA_ROOT / month / site / task
    try:
        root.resolve().relative_to(DATA_ROOT.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid folder path"}), 400
    if not root.exists():
        return jsonify({"ok": False, "error": "Photo folder not found"}), 404

    result = analyze_folder(root, cache=VisionCache(), max_distance=max_distance)
    return jsonify({"ok": True, **result})

@app.get("/download")
def download():
    path = request.args.get("path")
    if not path or not storage.exists(Path(path)):
        return "Not found", 404
    return send_file(path, as_attachment=True)

@app.get("/download/job/<job_id>")
def download_job(job_id):
    job = job_manager.get_job(job_id)
    if not job or not job.get("result_path"):
        return "Not found", 404
    path = Path(job["result_path"]).resolve()
    try:
        path.relative_to(REPORT_ROOT.resolve())
    except ValueError:
        return "Not found", 404
    if not path.exists() or not path.is_file():
        return "Not found", 404
    return send_file(path, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
