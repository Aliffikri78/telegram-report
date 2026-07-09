#!/usr/bin/env python3
import os, re, sys, logging, signal, json, asyncio
from datetime import datetime
from pathlib import Path
from urllib import parse, request as urlrequest, error as urlerror
from typing import Optional, Dict
from telegram import Update, Message, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

try:
    from download.manager import DownloadManager
    from projects.manager import ProjectManager
    from session.manager import SessionManager
    from storage.storage import Storage
except ImportError:
    from app.download.manager import DownloadManager
    from app.projects.manager import ProjectManager
    from app.session.manager import SessionManager
    from app.storage.storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("photo-bot")

BEFORE_WORDS = {'sebelum','sblm','sblum','sebelom','before'}
AFTER_WORDS  = {'selepas','slps','slpas','after','lepas'}

TIME_BEFORE_HOUR = int(os.getenv('TIME_BEFORE_HOUR','12'))
TIME_AFTER_HOUR  = int(os.getenv('TIME_AFTER_HOUR','15'))
storage = Storage()
project_manager = ProjectManager()
session_manager = SessionManager()
download_manager = DownloadManager()
SAVE_ROOT = storage.photos_root
WEB_BASE_URL = os.getenv('WEB_BASE_URL', os.getenv('REPORT_WEB_URL', 'http://127.0.0.1:8080')).rstrip('/')
INTERNAL_API_TOKEN = os.getenv('INTERNAL_API_TOKEN')
TELEGRAM_FILE_LIMIT = int(os.getenv('TELEGRAM_FILE_LIMIT_BYTES', str(45 * 1024 * 1024)))
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
ACTIVE_JOBS = {}
JOB_MILESTONES = {}
JOB_POLL_TASKS = {}
UPLOAD_FLOWS = {}
UPLOAD_PAUSE_SECONDS = int(os.getenv('TELEGRAM_UPLOAD_PAUSE_SECONDS', '8'))
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ['New Job', 'Job Status'],
        ['Reports', 'Help'],
    ],
    resize_keyboard=True,
)
BEFORE_REVIEW_MENU = ReplyKeyboardMarkup(
    [
        ['Continue to AFTER'],
        ['Upload More BEFORE', 'Cancel'],
    ],
    resize_keyboard=True,
)
MATCH_REVIEW_MENU = ReplyKeyboardMarkup(
    [
        ['Generate Report'],
        ['Upload More', 'Cancel'],
    ],
    resize_keyboard=True,
)
MISMATCH_REVIEW_MENU = ReplyKeyboardMarkup(
    [
        ['Upload More BEFORE', 'Upload More AFTER'],
        ['Generate Anyway', 'Cancel'],
    ],
    resize_keyboard=True,
)


def set_ctx(chat_id:int, site:Optional[str]=None, task:Optional[str]=None, when:Optional[str]=None):
    current = session_manager.get_session(chat_id)
    if current:
        session_manager.update_session(chat_id, site=site, task=task, when=when)
    else:
        session_manager.create_session(chat_id, site=site, task=task, when=when)

def get_ctx(chat_id:int, key:str) -> Optional[str]:
    return session_manager.get_session(chat_id).get(key)

def all_aliases() -> Dict[str,str]:
    out={}
    import json
    for site in project_manager.list_sites(project_manager.default_project_id()):
        out[site["name"].lower()] = site["name"]
        out[site["slug"].lower()] = site["name"]
        for alias in json.loads(site.get("aliases") or "[]"):
            out[alias.lower()] = site["name"]
    return out

def normalize_when(word: Optional[str]) -> Optional[str]:
    if not word: return None
    w = word.lower()
    if w in BEFORE_WORDS: return 'before'
    if w in AFTER_WORDS:  return 'after'
    return None

def detect_site_free(text:str) -> Optional[str]:
    site = project_manager.detect_site_free(text, project_manager.default_project_id())
    return site["name"] if site else None

def detect_when_fallback(caption:str, dt:datetime) -> str:
    t=(caption or '').lower()
    if any(w in t for w in BEFORE_WORDS): return 'before'
    if any(w in t for w in AFTER_WORDS):  return 'after'
    if dt.hour < TIME_BEFORE_HOUR:        return 'before'
    if dt.hour >= TIME_AFTER_HOUR:        return 'after'
    return 'unknown'

def target_dir(site:str, task:str, when:str, dt:datetime) -> Path:
    return storage.target_photo_dir(dt.strftime('%Y-%m'), site, task, when)

def unique_path(path: Path) -> Path:
    return storage.unique_path(path)


def menu_kwargs():
    return {"reply_markup": MAIN_MENU}

def current_month(dt: Optional[datetime] = None) -> str:
    return (dt or datetime.now()).strftime('%Y-%m')

def image_count(path: Path) -> int:
    try:
        return len([p for p in storage.list_files(path, IMAGE_SUFFIXES)])
    except Exception:
        return 0

def session_counts(chat_id: int, dt: Optional[datetime] = None):
    flow = UPLOAD_FLOWS.get(chat_id) or {}
    month = flow.get('month') or current_month(dt)
    site = flow.get('site') or get_ctx(chat_id, 'site') or 'UNSPECIFIED'
    task = flow.get('task') or get_ctx(chat_id, 'task') or task_slug_for('grass', 'grass')
    root = SAVE_ROOT / month / site / task
    before_count = image_count(root / 'before')
    after_count = image_count(root / 'after')
    return month, site, task, before_count, after_count

def count_warning(before_count: int, after_count: int) -> str:
    if before_count == 0 and after_count == 0:
        return ''
    diff = abs(before_count - after_count)
    limit = max(3, int(max(before_count, after_count) * 0.25))
    if diff > limit:
        return f"\nWarning: before/after counts differ by {diff}."
    return ''

def session_summary(chat_id: int, dt: Optional[datetime] = None) -> str:
    month, site, task, before_count, after_count = session_counts(chat_id, dt)
    when = get_ctx(chat_id, 'when') or 'not set'
    return (
        f"Session:\n"
        f"Month: {month}\n"
        f"Site: {site}\n"
        f"Task: {task}\n"
        f"Upload mode: {when}\n"
        f"Before: {before_count}\n"
        f"After: {after_count}"
        f"{count_warning(before_count, after_count)}"
    )


def task_label(task: dict) -> str:
    command = str(task.get('command') or task.get('slug') or '').strip()
    label = task.get('name') or task.get('slug') or command
    title = task.get('title') or ''
    suffix = f" - {title}" if title and title != label else ''
    return f"/{command} {label}{suffix}" if command else f"{label}{suffix}"

def task_options_text() -> str:
    tasks = project_manager.list_tasks(project_manager.default_project_id())
    if not tasks:
        return 'Type the job/task name, for example grass or drainage.'
    lines = ['Choose Job/Task first. Reply with one of:']
    lines.extend(f"- {task_label(task)}" for task in tasks)
    return '\n'.join(lines)

def site_options_text() -> str:
    sites = project_manager.list_sites(project_manager.default_project_id())
    if not sites:
        return 'No sites configured. Add one with /addsite NAME shortcut.'
    lines = ['Choose Site second. Reply with one of:']
    lines.extend(f"- {site['name']}" for site in sites)
    return '\n'.join(lines)

def resolve_task_input(text: str) -> Optional[dict]:
    raw = (text or '').strip()
    token = raw.lower().lstrip('/').split('@')[0]
    project_id = project_manager.default_project_id()
    task = project_manager.task_by_command(token, project_id) or project_manager.task_by_slug(token, project_id)
    if task:
        return task
    compact = re.sub(r'[^a-z0-9]+', '_', token).strip('_')
    for task in project_manager.list_tasks(project_id):
        values = {
            str(task.get('name') or '').lower(),
            str(task.get('title') or '').lower(),
            str(task.get('slug') or '').lower(),
            str(task.get('command') or '').lower(),
        }
        if token in values or compact in values:
            return task
    aliases = {
        'grass': 'grass',
        'grass_cutting': 'grass',
        'cutting': 'grass',
        'drain': 'drainage',
        'drainage': 'drainage',
        'longkang': 'drainage',
    }
    alias = aliases.get(token) or aliases.get(compact)
    return project_manager.task_by_command(alias, project_id) if alias else None

def resolve_site_input(text: str) -> Optional[dict]:
    project_id = project_manager.default_project_id()
    return project_manager.find_site((text or '').strip(), project_id) or project_manager.detect_site_free(text or '', project_id)

def task_keyboard() -> ReplyKeyboardMarkup:
    rows = []
    for task in project_manager.list_tasks(project_manager.default_project_id()):
        rows.append([str(task.get('command') or task.get('slug') or task.get('name'))])
    rows.append(['Cancel'])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def site_keyboard() -> ReplyKeyboardMarkup:
    rows = [[site['name']] for site in project_manager.list_sites(project_manager.default_project_id())]
    rows.append(['Cancel'])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def month_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[current_month()], ['Cancel']], resize_keyboard=True)

def normalize_month(value: str) -> Optional[str]:
    text = (value or '').strip()
    if re.match(r'^\d{4}-\d{2}$', text):
        return text
    if text.lower() in {'current month', 'this month', 'now'}:
        return current_month()
    return None

def cancel_pause_timer(chat_id: int):
    flow = UPLOAD_FLOWS.get(chat_id)
    task = flow.get('pause_task') if flow else None
    if task and not task.done():
        task.cancel()
    if flow:
        flow.pop('pause_task', None)

def clear_upload_flow(chat_id: int):
    cancel_pause_timer(chat_id)
    UPLOAD_FLOWS.pop(chat_id, None)

def start_guided_flow(chat_id: int):
    clear_upload_flow(chat_id)
    UPLOAD_FLOWS[chat_id] = {'step': 'task', 'pause_token': 0, 'batch_when': None, 'batch_received': 0, 'batch_duplicates': 0, 'batch_total': 0}

def selected_message_date(month: str, original: datetime) -> str:
    try:
        return f"{month}-{max(1, min(original.day, 28)):02d}T{original.strftime('%H:%M:%S')}"
    except Exception:
        return f"{month}-01T00:00:00"

def review_summary(chat_id: int) -> str:
    month, site, task, before_count, after_count = session_counts(chat_id)
    warning = count_warning(before_count, after_count)
    return (
        f"Review Summary:\n"
        f"Task: {task}\n"
        f"Site: {site}\n"
        f"Month: {month}\n"
        f"Before count: {before_count}\n"
        f"After count: {after_count}"
        f"{warning}"
    )

def review_menu(chat_id: int):
    _, _, _, before_count, after_count = session_counts(chat_id)
    return MISMATCH_REVIEW_MENU if before_count != after_count else MATCH_REVIEW_MENU

def upload_batch_summary(chat_id: int, when: str) -> str:
    flow = UPLOAD_FLOWS.get(chat_id) or {}
    _, _, _, before_count, after_count = session_counts(chat_id)
    label = 'BEFORE' if when == 'before' else 'AFTER'
    folder_total = before_count if when == 'before' else after_count
    total = max(folder_total, int(flow.get('batch_total') or 0))
    duplicates = int(flow.get('batch_duplicates') or 0)
    text = f"{label} upload complete.\nTotal {label}: {total}"
    if duplicates:
        text += f"\nDuplicates ignored: {duplicates}"
    return text

def reset_upload_batch(flow: dict):
    flow['batch_when'] = None
    flow['batch_received'] = 0
    flow['batch_duplicates'] = 0
    flow['batch_total'] = 0

def record_upload_batch(flow: dict, when: str, duplicate: bool, total: int):
    if flow.get('batch_when') != when:
        flow['batch_when'] = when
        flow['batch_received'] = 0
        flow['batch_duplicates'] = 0
        flow['batch_total'] = 0
    flow['batch_received'] = int(flow.get('batch_received') or 0) + 1
    flow['batch_total'] = max(int(flow.get('batch_total') or 0), int(total or 0))
    if duplicate:
        flow['batch_duplicates'] = int(flow.get('batch_duplicates') or 0) + 1

async def upload_pause_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int, token: int):
    try:
        await asyncio.sleep(UPLOAD_PAUSE_SECONDS)
        flow = UPLOAD_FLOWS.get(chat_id)
        if not flow or flow.get('pause_token') != token:
            return
        if flow.get('step') == 'upload_before':
            text = upload_batch_summary(chat_id, 'before')
            reset_upload_batch(flow)
            flow['step'] = 'review_before'
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=BEFORE_REVIEW_MENU,
            )
            return
        if flow.get('step') == 'upload_after':
            text = upload_batch_summary(chat_id, 'after') + "\n\n" + review_summary(chat_id)
            reset_upload_batch(flow)
            flow['step'] = 'review'
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=review_menu(chat_id),
            )
    except asyncio.CancelledError:
        return

def schedule_upload_pause(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    flow = UPLOAD_FLOWS.get(chat_id)
    if not flow:
        return
    cancel_pause_timer(chat_id)
    flow['pause_token'] = int(flow.get('pause_token') or 0) + 1
    token = flow['pause_token']
    flow['pause_task'] = context.application.create_task(upload_pause_prompt(context, chat_id, token))

def web_json(path: str, data: Optional[dict] = None, method: str = 'GET'):
    url = WEB_BASE_URL + path
    body = None
    headers = {}
    if data is not None:
        body = parse.urlencode(data).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    if INTERNAL_API_TOKEN:
        headers['X-Internal-Api-Token'] = INTERNAL_API_TOKEN
    req = urlrequest.Request(url, data=body, headers=headers, method=method)
    with urlrequest.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode('utf-8'))

async def web_json_async(path: str, data: Optional[dict] = None, method: str = 'GET'):
    return await asyncio.to_thread(web_json, path, data, method)

def job_state(job: dict) -> dict:
    return dict(((job or {}).get('payload') or {}).get('_state') or {})

def active_job_status(status: str) -> bool:
    return str(status or '').lower() in {'queued', 'starting', 'preprocess', 'matching', 'running'}

def job_status_text(job: dict) -> str:
    state = job_state(job)
    status = state.get('state') or job.get('status') or 'unknown'
    ai_recovery = state.get('ai_recovery') or {}
    lines = [
        f"Job: {job.get('name') or job.get('id')}",
        f"Status: {status}",
        f"Matched: {state.get('matched', 0)}",
        f"Fallback: {state.get('unmatched', 0)}",
        f"Pages: {state.get('pages', 0)}",
    ]
    if ai_recovery.get('enabled'):
        lines.extend([
            f"AI Recovery: {ai_recovery.get('state', '-')}",
            f"Recovered: {ai_recovery.get('recovered', 0)}",
            f"AI Backend: {ai_recovery.get('backend', 'unknown')}",
        ])
    if job.get('error') or state.get('error'):
        lines.append(f"Error: {job.get('error') or state.get('error')}")
    return '\n'.join(lines)

def milestone_for(job: dict) -> Optional[str]:
    state = job_state(job)
    status = str(state.get('state') or job.get('status') or '').lower()
    ai_recovery = state.get('ai_recovery') or {}
    if status == 'done':
        return 'Report ready'
    if status in {'queued', 'starting'}:
        return 'Report started'
    if ai_recovery.get('enabled') and ai_recovery.get('state') == 'running':
        return 'AI Recovery running...'
    if status in {'preprocess', 'matching', 'running'}:
        return 'CPU matching...'
    if status == 'error':
        return 'Report failed'
    if status == 'cancelled':
        return 'Report cancelled'
    return None

async def send_milestone(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job: dict):
    job_id = job.get('id')
    milestone = milestone_for(job)
    if not job_id or not milestone:
        return
    key = (chat_id, job_id)
    if JOB_MILESTONES.get(key) == milestone:
        return
    JOB_MILESTONES[key] = milestone
    await context.bot.send_message(chat_id=chat_id, text=milestone, reply_markup=MAIN_MENU)

async def send_report_files(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job: dict):
    job_id = job.get('id')
    result_path = job.get('result_path') or job_state(job).get('download')
    sent_any = False
    paths = []
    if result_path:
        report = Path(result_path)
        paths.append(report)
        paths.append(report.with_suffix('.pdf'))
        paths.append(report.with_suffix('.debug.json'))
    for path in paths:
        try:
            if not path.exists() or not path.is_file():
                continue
            if path.stat().st_size > TELEGRAM_FILE_LIMIT:
                continue
            with path.open('rb') as fh:
                await context.bot.send_document(chat_id=chat_id, document=fh, filename=path.name)
            sent_any = True
        except Exception as exc:
            log.warning('Could not send report file %s: %s', path, exc)
    if not sent_any and job_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Report is ready, but the file could not be attached.\n"
                f"Download: {WEB_BASE_URL}/download/job/{job_id}\n"
                f"Debug: {WEB_BASE_URL}/download/debug/{job_id}"
            ),
            reply_markup=MAIN_MENU,
        )

async def poll_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job_id: str):
    try:
        while True:
            try:
                data = await web_json_async(f'/api/jobs/{parse.quote(job_id)}')
                job = data.get('job') or {}
                await send_milestone(context, chat_id, job)
                status = str(job.get('status') or job_state(job).get('state') or '').lower()
                if status == 'done':
                    await send_report_files(context, chat_id, job)
                    ACTIVE_JOBS.pop(chat_id, None)
                    return
                if status in {'error', 'failed', 'cancelled'}:
                    ACTIVE_JOBS.pop(chat_id, None)
                    return
            except Exception as exc:
                log.warning('Job poll failed for %s: %s', job_id, exc)
            await asyncio.sleep(5)
    finally:
        JOB_POLL_TASKS.pop(chat_id, None)

def start_polling_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job_id: str):
    existing = JOB_POLL_TASKS.get(chat_id)
    if existing and not existing.done():
        existing.cancel()
    ACTIVE_JOBS[chat_id] = job_id
    JOB_POLL_TASKS[chat_id] = context.application.create_task(poll_job(context, chat_id, job_id))

async def cmd_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I can guide photo uploads and report jobs.\n"
        "Use New Job to choose task, site, and month before uploading.\n"
        "Commands still work: /grass, /drainage, /status, /canceljob, /reset.",
        **menu_kwargs(),
    )

async def cmd_where(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"SAVE_ROOT = {SAVE_ROOT}", **menu_kwargs())

async def cmd_addsite(update:Update, context:ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /addsite NAME [shortcut]\\nExample: /addsite HOTEL h")
        return
    name = args[0].upper()
    shortcut = args[1].lower() if len(args) > 1 else None
    project_id = project_manager.default_project_id()
    if project_manager.find_site(name.lower(), project_id):
        await update.message.reply_text(f"Site exists: {name}")
        return
    aliases = [name.lower()]
    if shortcut:
        aliases.append(shortcut)
    project_manager.create("sites", {"project_id": project_id, "name": name, "aliases": aliases})
    await update.message.reply_text(f"Added site: {name}" + (f" (/{shortcut})" if shortcut else ""))

def parse_cmd_site_when(args):
    site=None; when=None
    for tok in args:
        low=tok.lower()
        found = project_manager.find_site(low, project_manager.default_project_id())
        if not site and found:
            site = found["name"]; continue
        w = normalize_when(low)
        if not when and w: when = w
    return site, when

def task_slug_for(command: str, fallback: str) -> str:
    task = project_manager.task_by_command(command, project_manager.default_project_id())
    return task["slug"] if task else fallback

async def cmd_grass(update:Update, context:ContextTypes.DEFAULT_TYPE):
    site, when = parse_cmd_site_when(context.args or [])
    if not site: site = detect_site_free(" ".join(context.args or []))
    if not site:
        await update.message.reply_text("Usage: /grass <site> <sebelum|selepas>\\nExample: /grass echo sebelum"); return
    if not when: when = 'before'
    task = task_slug_for("grass", "grass")
    set_ctx(update.effective_chat.id, site=site, task=task, when=when)
    clear_upload_flow(update.effective_chat.id)
    await update.message.reply_text(f"OK. Task={task}, Site={site}, When={when.upper()}. Send photos now.\n\n" + session_summary(update.effective_chat.id), **menu_kwargs())

async def cmd_drainage(update:Update, context:ContextTypes.DEFAULT_TYPE):
    site, when = parse_cmd_site_when(context.args or [])
    if not site: site = detect_site_free(" ".join(context.args or []))
    if not site:
        await update.message.reply_text("Usage: /drainage <site> <sebelum|selepas>\\nExample: /drainage delta selepas"); return
    if not when: when = 'before'
    task = task_slug_for("drainage", "drainage")
    set_ctx(update.effective_chat.id, site=site, task=task, when=when)
    clear_upload_flow(update.effective_chat.id)
    await update.message.reply_text(f"OK. Task={task}, Site={site}, When={when.upper()}. Send photos now.\n\n" + session_summary(update.effective_chat.id), **menu_kwargs())

async def cmd_dynamic(update:Update, context:ContextTypes.DEFAULT_TYPE):
    command = (update.message.text or "").split()[0].lstrip("/").split("@")[0]
    task_row = project_manager.task_by_command(command, project_manager.default_project_id())
    if not task_row:
        return
    site, when = parse_cmd_site_when(context.args or [])
    if not site:
        site = detect_site_free(" ".join(context.args or []))
    if not site:
        await update.message.reply_text(f"Usage: /{command} <site> <sebelum|selepas>")
        return
    if not when:
        when = 'before'
    set_ctx(update.effective_chat.id, site=site, task=task_row["slug"], when=when)
    clear_upload_flow(update.effective_chat.id)
    await update.message.reply_text(f"OK. Task={task_row['slug']}, Site={site}, When={when.upper()}. Send photos now.\n\n" + session_summary(update.effective_chat.id), **menu_kwargs())

async def cmd_reset(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session_manager.clear_session(chat_id)
    clear_upload_flow(chat_id)
    await update.message.reply_text("Context cleared. Use /grass or /drainage again.", **menu_kwargs())


async def cmd_status(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_id = ACTIVE_JOBS.get(chat_id)
    try:
        if not job_id:
            data = await web_json_async('/api/jobs?limit=10&status=running')
            jobs = data.get('jobs') or []
            if jobs:
                job_id = jobs[0].get('id')
                ACTIVE_JOBS[chat_id] = job_id
        if not job_id:
            await update.message.reply_text('No active report job.', **menu_kwargs())
            return
        data = await web_json_async(f'/api/jobs/{parse.quote(job_id)}')
        job = data.get('job') or {}
        await update.message.reply_text(job_status_text(job), **menu_kwargs())
        if active_job_status(job.get('status')):
            start_polling_job(context, chat_id, job_id)
    except Exception as exc:
        await update.message.reply_text(f'Could not get job status: {exc}', **menu_kwargs())

async def cmd_canceljob(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_id = ACTIVE_JOBS.get(chat_id)
    if not job_id:
        await update.message.reply_text('No active report job to cancel.', **menu_kwargs())
        return
    try:
        data = await web_json_async(f'/api/jobs/{parse.quote(job_id)}/cancel', method='POST')
        if data.get('ok'):
            ACTIVE_JOBS.pop(chat_id, None)
            task = JOB_POLL_TASKS.pop(chat_id, None)
            if task and not task.done():
                task.cancel()
            await update.message.reply_text('Report job cancelled.', **menu_kwargs())
        else:
            await update.message.reply_text(data.get('error') or 'Could not cancel report job.', **menu_kwargs())
    except Exception as exc:
        await update.message.reply_text(f'Could not cancel report job: {exc}', **menu_kwargs())

async def generate_report(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    month, site, task, before_count, after_count = session_counts(chat_id)
    if site == 'UNSPECIFIED' or not task or not month:
        await update.message.reply_text('Choose task, site, and month first with New Job.', **menu_kwargs())
        return
    if before_count == 0 or after_count == 0:
        await update.message.reply_text('Need both before and after photos before generating a report.\n\n' + review_summary(chat_id), reply_markup=review_menu(chat_id))
        return
    task_row = project_manager.task_by_slug(task)
    payload = {
        'month': month,
        'site': site,
        'task': task,
        'company': os.getenv('COMPANY', 'HW UNGGUL (901587-V)'),
        'zone': f'{site} ZONE',
        'title': (task_row or {}).get('title') or task.replace('_', ' ').title(),
        'threshold': os.getenv('THRESHOLD', '0.30'),
        'backend': os.getenv('TELEGRAM_REPORT_BACKEND', 'auto'),
        'ai_review': os.getenv('TELEGRAM_REPORT_AI_REVIEW', '0'),
        'ai_deep_recovery': os.getenv('TELEGRAM_REPORT_DEEP_RECOVERY', '0'),
    }
    try:
        data = await web_json_async('/start', payload, 'POST')
        if not data.get('ok'):
            await update.message.reply_text(data.get('error') or 'Could not start report.', **menu_kwargs())
            return
        job_id = data['job_id']
        start_polling_job(context, chat_id, job_id)
        summary = review_summary(chat_id)
        clear_upload_flow(chat_id)
        await update.message.reply_text('Report started\n\n' + summary, **menu_kwargs())
    except Exception as exc:
        await update.message.reply_text(f'Could not start report: {exc}', **menu_kwargs())

async def cmd_reports(update:Update, context:ContextTypes.DEFAULT_TYPE):
    try:
        data = await web_json_async('/api/jobs?limit=5&status=done')
        jobs = data.get('jobs') or []
        if not jobs:
            await update.message.reply_text('No completed reports yet.', **menu_kwargs())
            return
        lines = ['Recent reports:']
        for job in jobs:
            lines.append(f"- {job.get('name') or job.get('id')}: {WEB_BASE_URL}/download/job/{job.get('id')}")
        await update.message.reply_text('\n'.join(lines), **menu_kwargs())
    except Exception as exc:
        await update.message.reply_text(f'Could not load reports: {exc}', **menu_kwargs())

async def cmd_help(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def handle_menu_text(update:Update, context:ContextTypes.DEFAULT_TYPE):
    raw_text = (update.message.text or '').strip()
    text = raw_text.lower()
    chat_id = update.effective_chat.id

    if text == 'new job':
        start_guided_flow(chat_id)
        await update.message.reply_text(task_options_text(), reply_markup=task_keyboard())
        return
    if text == 'job status':
        await cmd_status(update, context)
        return
    if text == 'reports':
        await cmd_reports(update, context)
        return
    if text == 'help':
        await cmd_help(update, context)
        return
    if text == 'cancel':
        clear_upload_flow(chat_id)
        await update.message.reply_text('New job cancelled.', **menu_kwargs())
        return

    flow = UPLOAD_FLOWS.get(chat_id)
    if flow:
        step = flow.get('step')
        if step == 'task':
            task = resolve_task_input(raw_text)
            if not task:
                await update.message.reply_text('I could not recognize that job/task.\n\n' + task_options_text(), reply_markup=task_keyboard())
                return
            flow['task'] = task['slug']
            flow['step'] = 'site'
            await update.message.reply_text(f"Job/Task selected: {task['slug']}\n\n" + site_options_text(), reply_markup=site_keyboard())
            return
        if step == 'site':
            site = resolve_site_input(raw_text)
            if not site:
                await update.message.reply_text('I could not recognize that site.\n\n' + site_options_text(), reply_markup=site_keyboard())
                return
            flow['site'] = site['name']
            flow['step'] = 'month'
            await update.message.reply_text(f"Site selected: {site['name']}\nChoose month as YYYY-MM.", reply_markup=month_keyboard())
            return
        if step == 'month':
            month = normalize_month(raw_text)
            if not month:
                await update.message.reply_text('Please enter month as YYYY-MM, for example 2026-07.', reply_markup=month_keyboard())
                return
            flow['month'] = month
            flow['when'] = 'before'
            flow['step'] = 'upload_before'
            set_ctx(chat_id, site=flow['site'], task=flow['task'], when='before')
            await update.message.reply_text(
                f"New Job ready.\nTask: {flow['task']}\nSite: {flow['site']}\nMonth: {flow['month']}\nUpload: BEFORE\n\nSend BEFORE photos now.",
                **menu_kwargs(),
            )
            return
        if step == 'review_before':
            if text == 'continue to after':
                flow['when'] = 'after'
                flow['step'] = 'upload_after'
                set_ctx(chat_id, site=flow['site'], task=flow['task'], when='after')
                await update.message.reply_text('Upload AFTER photos now.', **menu_kwargs())
                return
            if text == 'upload more before':
                flow['when'] = 'before'
                flow['step'] = 'upload_before'
                set_ctx(chat_id, site=flow['site'], task=flow['task'], when='before')
                await update.message.reply_text('Send more BEFORE photos.', **menu_kwargs())
                return
        if step == 'review':
            if text == 'upload more before':
                flow['when'] = 'before'
                flow['step'] = 'upload_before'
                set_ctx(chat_id, site=flow['site'], task=flow['task'], when='before')
                await update.message.reply_text('Send more BEFORE photos.', **menu_kwargs())
                return
            if text in {'upload more after', 'upload more'}:
                flow['when'] = 'after'
                flow['step'] = 'upload_after'
                set_ctx(chat_id, site=flow['site'], task=flow['task'], when='after')
                await update.message.reply_text('Send more AFTER photos.', **menu_kwargs())
                return
            if text in {'generate report', 'generate anyway'}:
                await generate_report(update, context)
                return

        await update.message.reply_text('Please use the shown buttons to continue this job.', **menu_kwargs())
        return

    if text in {'generate report', 'generate anyway'}:
        await generate_report(update, context)
        return

async def safe_reply(msg: Message, text: str):
    try:
        await msg.reply_text(text, quote=False)
    except Exception as exc:
        log.warning("Failed to send upload acknowledgement: %s", exc)

async def handle_photo(update:Update, context:ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    if not msg or not msg.photo: return
    chat_id = update.effective_chat.id
    caption = msg.caption or ''
    dt = msg.date

    flow = UPLOAD_FLOWS.get(chat_id)
    if flow:
        if not all(flow.get(key) for key in ('task', 'site', 'month')):
            await msg.reply_text('Please choose task, site, and month first, then upload photos.', quote=False, **menu_kwargs())
            return
        if flow.get('step') not in {'upload_before', 'upload_after'}:
            await msg.reply_text('Please use the shown buttons before uploading more photos.', quote=False, **menu_kwargs())
            return
        site = flow['site']
        task = flow['task']
        when = 'before' if flow.get('step') == 'upload_before' else 'after'
        flow['when'] = when
        message_date = selected_message_date(flow['month'], dt)
    else:
        site = get_ctx(chat_id,'site') or detect_site_free(caption)
        fallback_task = task_slug_for("drainage", "drainage") if any(k in caption.lower() for k in ('longkang','parit','drain')) else task_slug_for("grass", "grass")
        task = get_ctx(chat_id,'task') or fallback_task
        when = get_ctx(chat_id,'when') or detect_when_fallback(caption, dt)
        if not site or not task:
            await msg.reply_text('Please choose task, site, and month first using New Job, or use /grass <site> /drainage <site>.', quote=False, **menu_kwargs())
            return
        message_date = dt.isoformat()

    ph = msg.photo[-1]
    uniq = getattr(ph, 'file_unique_id', None) or f"{chat_id}_{msg.message_id}_{ph.file_id}"

    result = download_manager.enqueue(
        file_id=ph.file_id,
        file_unique_id=uniq,
        chat_id=chat_id,
        user_id=msg.from_user.id if msg.from_user else None,
        message_id=msg.message_id,
        message_date=message_date,
        site=site,
        task=task,
        when=when,
        caption=caption,
    )
    month, current_site, current_task, before_count, after_count = session_counts(chat_id, dt)
    if not result.get("duplicate"):
        if when == 'before':
            before_count += 1
        elif when == 'after':
            after_count += 1
    label = 'BEFORE' if when == 'before' else ('AFTER' if when == 'after' else 'UNKNOWN')
    total = before_count if when == 'before' else after_count if when == 'after' else before_count + after_count
    prefix = "Photo already received." if result.get("duplicate") else "Photo received."
    text = f"{prefix} Total {label}: {total}"
    if flow:
        record_upload_batch(flow, when, bool(result.get("duplicate")), total)
        schedule_upload_pause(context, chat_id)
    else:
        context.application.create_task(safe_reply(msg, text))

def main():
    token = os.getenv('TG_BOT_TOKEN')
    if not token:
        log.error("Missing env var: TG_BOT_TOKEN"); sys.exit(2)
    storage.ensure_dir(SAVE_ROOT)
    download_manager.start(token)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('where',     cmd_where))
    app.add_handler(CommandHandler('addsite',   cmd_addsite))
    app.add_handler(CommandHandler('grass',     cmd_grass))
    app.add_handler(CommandHandler('drainage',  cmd_drainage))
    app.add_handler(CommandHandler('reset',     cmd_reset))
    app.add_handler(CommandHandler('status',    cmd_status))
    app.add_handler(CommandHandler('canceljob', cmd_canceljob))
    app.add_handler(CommandHandler('help',      cmd_help))
    app.add_handler(CommandHandler('reports',   cmd_reports))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_dynamic))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    log.info("Bot up. Saving into %s", SAVE_ROOT)
    stop_signals = None if os.name == "nt" else (signal.SIGINT, signal.SIGTERM)
    try:
        app.run_polling(stop_signals=stop_signals)
    finally:
        download_manager.stop()

if __name__ == '__main__':
    try: main()
    except (KeyboardInterrupt, SystemExit): pass
