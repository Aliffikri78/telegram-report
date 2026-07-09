import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageOps

try:
    from database.db import connection
    from database.migrate import migrate
    from storage.storage import Storage
except ImportError:
    from app.database.db import connection
    from app.database.migrate import migrate
    from app.storage.storage import Storage


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_IMAGE_EDGE = 2048
MAX_THUMB_EDGE = 300
JPEG_QUALITY = 85


class WorkerUploadManager:
    def __init__(self, storage: Optional[Storage] = None):
        migrate()
        self.storage = storage or Storage()
        self.cache_root = self.storage.ensure_dir(self.storage.photos_root / ".worker_upload_cache")
        self.temp_root = self.storage.ensure_dir(self.cache_root / "tmp")
        self.thumb_root = self.storage.ensure_dir(self.cache_root / "thumbs")

    def create(self, site: str, task: str, month: str, worker_name: str = "") -> Dict:
        job_id = uuid.uuid4().hex
        now = self._now()
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO worker_upload_jobs (
                    id, site, task, month, worker_name, status,
                    before_count, after_count, files, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (job_id, site, task, month, worker_name, "draft", "[]", now, now),
            )
            conn.commit()
        return self.get(job_id)

    def list_jobs(self, limit: int = 50, status: str = "") -> List[Dict]:
        safe_limit = max(1, min(int(limit or 50), 200))
        where = ""
        params = []
        if status and status != "all":
            where = "WHERE status = ?"
            params.append(status)
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM worker_upload_jobs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get(self, job_id: str) -> Optional[Dict]:
        with connection() as conn:
            row = conn.execute("SELECT * FROM worker_upload_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def add_file(self, job_id: str, when: str, upload) -> Dict:
        job = self.get(job_id)
        if not job:
            raise ValueError("Upload job not found")
        if job["status"] == "ready":
            raise ValueError("Upload job is already ready for admin review")
        when = when if when in {"before", "after"} else ""
        if not when:
            raise ValueError("Upload type must be before or after")
        original_filename = Path(upload.filename or "").name
        if not original_filename:
            raise ValueError("Missing file name")
        if Path(original_filename).suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("Only image uploads are allowed")

        item_id = uuid.uuid4().hex
        temp_path = self._temp_upload_path(original_filename, item_id)
        destination = None
        thumbnail_path = None
        try:
            upload.save(temp_path)
            original_size = temp_path.stat().st_size
            destination, thumbnail_path = self._compress_upload(job, when, original_filename, item_id, temp_path)
            compressed_size = destination.stat().st_size
            item = {
                "id": item_id,
                "when": when,
                "filename": destination.name,
                "path": str(destination),
                "thumbnail_filename": thumbnail_path.name,
                "thumbnail_path": str(thumbnail_path),
                "original_filename": original_filename,
                "original_size": original_size,
                "compressed_size": compressed_size,
                "uploaded_at": self._now(),
            }
            files = list(job.get("files") or [])
            files.append(item)
            self._save_files(job_id, files, "draft")
            return item
        except Exception as exc:
            if destination is not None:
                self.storage.unlink(destination)
            if thumbnail_path is not None:
                self.storage.unlink(thumbnail_path)
            raise ValueError(f"Could not process image upload: {exc}") from exc
        finally:
            self.storage.unlink(temp_path)

    def delete_file(self, job_id: str, file_id: str) -> Dict:
        job = self.get(job_id)
        if not job:
            raise ValueError("Upload job not found")
        if job["status"] == "ready":
            raise ValueError("Ready upload jobs cannot be edited")

        files = list(job.get("files") or [])
        keep = []
        removed = None
        for item in files:
            if item.get("id") == file_id:
                removed = item
            else:
                keep.append(item)
        if not removed:
            raise ValueError("Uploaded file not found")

        if removed.get("path"):
            self.storage.unlink(Path(removed["path"]))
        if removed.get("thumbnail_path"):
            self.storage.unlink(Path(removed["thumbnail_path"]))
        self._save_files(job_id, keep, "draft")
        return removed

    def mark_ready(self, job_id: str) -> Dict:
        job = self.get(job_id)
        if not job:
            raise ValueError("Upload job not found")
        self._save_files(job_id, list(job.get("files") or []), "ready")
        return self.get(job_id)

    def _temp_upload_path(self, original_filename: str, item_id: str) -> Path:
        suffix = Path(original_filename).suffix.lower() or ".upload"
        path = self.temp_root / f"{item_id}{suffix}"
        return self.storage.secure_path(path)

    def _compress_upload(self, job: Dict, when: str, original_filename: str, item_id: str, temp_path: Path):
        stem = Path(original_filename).stem or "photo"
        destination = self.storage.allocate_photo_path(job["month"], job["site"], job["task"], when, f"{stem}.jpg")
        thumb_dir = self.storage.ensure_dir(self.thumb_root / job["id"])
        thumbnail_path = self.storage.unique_path(thumb_dir / f"{destination.stem}-{item_id[:8]}.jpg")

        with Image.open(temp_path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")

            compressed = image.copy()
            compressed.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            compressed.save(destination, "JPEG", quality=JPEG_QUALITY, optimize=True)

            thumbnail = image.copy()
            thumbnail.thumbnail((MAX_THUMB_EDGE, MAX_THUMB_EDGE), Image.Resampling.LANCZOS)
            thumbnail.save(thumbnail_path, "JPEG", quality=JPEG_QUALITY, optimize=True)

        self.storage.validate_image(destination)
        self.storage.validate_image(thumbnail_path)
        return destination, thumbnail_path

    def _save_files(self, job_id: str, files: List[Dict], status: str) -> None:
        before_count = sum(1 for item in files if item.get("when") == "before")
        after_count = sum(1 for item in files if item.get("when") == "after")
        with connection() as conn:
            conn.execute(
                """
                UPDATE worker_upload_jobs
                SET status = ?,
                    before_count = ?,
                    after_count = ?,
                    files = ?,
                    ready_at = CASE WHEN ? = 'ready' THEN COALESCE(ready_at, CURRENT_TIMESTAMP) ELSE ready_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, before_count, after_count, json.dumps(files), status, job_id),
            )
            conn.commit()

    def _row_to_job(self, row) -> Dict:
        files = []
        raw_files = row["files"] if row else "[]"
        if raw_files:
            try:
                files = json.loads(raw_files)
            except json.JSONDecodeError:
                files = []
        return {
            "id": row["id"],
            "site": row["site"],
            "task": row["task"],
            "month": row["month"],
            "worker_name": row["worker_name"],
            "status": row["status"],
            "before_count": row["before_count"],
            "after_count": row["after_count"],
            "files": files,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "ready_at": row["ready_at"],
        }

    def _now(self) -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"
