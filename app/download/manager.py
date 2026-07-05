import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

try:
    from core.logger import logger
    from database.db import connection
    from database.migrate import migrate
except ImportError:
    from app.core.logger import logger
    from app.database.db import connection
    from app.database.migrate import migrate


class DownloadManager:
    def __init__(
        self,
        token: Optional[str] = None,
        worker_count: Optional[int] = None,
        max_attempts: Optional[int] = None,
        timeout: Optional[float] = None,
        requests_per_second: Optional[float] = None,
    ):
        migrate()
        self.token = token
        self.worker_count = worker_count or int(os.getenv("DOWNLOAD_WORKERS", "2"))
        self.max_attempts = max_attempts or int(os.getenv("DOWNLOAD_MAX_ATTEMPTS", "3"))
        self.timeout = timeout or float(os.getenv("DOWNLOAD_TIMEOUT", "30"))
        self.requests_per_second = requests_per_second or float(os.getenv("DOWNLOAD_REQUESTS_PER_SECOND", "1"))
        self.shutdown_timeout = float(os.getenv("DOWNLOAD_SHUTDOWN_TIMEOUT", "60"))
        self._threads = []
        self._workers = []
        self._lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._accepting = True
        self.resume_pending()

    def start(self, token: Optional[str] = None) -> None:
        if token:
            self.token = token
        if not self.token:
            logger.warning("Download workers not started: missing Telegram token")
            return
        with self._lock:
            if self._threads:
                return
            try:
                from download.worker import DownloadWorker
            except ImportError:
                from app.download.worker import DownloadWorker

            for _ in range(self.worker_count):
                worker = DownloadWorker(self, self.token, self.timeout)
                thread = threading.Thread(target=worker.run, daemon=False)
                thread.start()
                self._workers.append(worker)
                self._threads.append(thread)
            logger.info("Started %s download worker(s)", len(self._threads))

    def stop(self) -> None:
        with self._lock:
            self._accepting = False
            for worker in self._workers:
                worker.stop()
            threads = list(self._threads)
        for thread in threads:
            logger.info("Waiting for download worker %s to stop", thread.name)
            thread.join(timeout=self.shutdown_timeout)
        self.mark_unfinished_queued()

    def wait_for_rate_limit(self) -> None:
        interval = 1.0 / max(self.requests_per_second, 0.1)
        with self._rate_lock:
            now = time.monotonic()
            wait_time = self._last_request_at + interval - now
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_request_at = time.monotonic()

    def wait_for_retry_after(self, retry_after: float) -> None:
        logger.warning("Telegram rate limit hit; waiting %.1fs", retry_after)
        time.sleep(max(0.0, retry_after))

    def enqueue(self, *, file_id: str, file_unique_id: str, chat_id: int, user_id: Optional[int], message_id: int, message_date: str, site: str, task: str, when: str, caption: str) -> Dict[str, object]:
        if not self._accepting:
            logger.warning("Rejecting Telegram photo because DownloadManager is stopping")
            return {"queued": False, "duplicate": False, "status": "stopping"}
        existing = self.find_duplicate(file_unique_id)
        if existing:
            logger.info("Duplicate Telegram photo ignored: %s", file_unique_id)
            return {"queued": False, "duplicate": True, "task_id": existing.get("id"), "status": existing.get("status")}

        task_id = uuid.uuid4().hex
        logger.info("Queueing Telegram photo download task %s", task_id)
        try:
            with connection() as conn:
                logger.debug("Inserting download_queue row %s", task_id)
                conn.execute(
                    """
                    INSERT INTO download_queue (
                        id, file_id, file_unique_id, chat_id, user_id, status,
                        max_attempts, site, task, when_label, message_id, message_date, caption
                    )
                    VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        file_id,
                        file_unique_id,
                        str(chat_id),
                        str(user_id) if user_id is not None else None,
                        self.max_attempts,
                        site,
                        task,
                        when,
                        str(message_id),
                        message_date,
                        caption,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            duplicate = self.find_duplicate(file_unique_id) or {}
            return {"queued": False, "duplicate": True, "task_id": duplicate.get("id"), "status": duplicate.get("status")}
        return {"queued": True, "duplicate": False, "task_id": task_id, "status": "queued"}

    def find_duplicate(self, file_unique_id: str) -> Optional[Dict[str, object]]:
        logger.debug("Checking duplicate download for %s", file_unique_id)
        with connection() as conn:
            row = conn.execute(
                "SELECT id, status FROM download_queue WHERE file_unique_id = ?",
                (file_unique_id,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id, status FROM download_history WHERE file_unique_id = ?",
                    (file_unique_id,),
                ).fetchone()
        return dict(row) if row else None

    def resume_pending(self) -> None:
        logger.info("Resuming unfinished Telegram download tasks")
        self.mark_unfinished_queued()

    def mark_unfinished_queued(self) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE download_queue
                SET status = 'queued', progress = 0, error = NULL, started_at = NULL, next_attempt_at = NULL
                WHERE status = 'downloading'
                """
            )
            conn.commit()

    def claim_next_task(self):
        with connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM download_queue
                WHERE status IN ('queued', 'retry')
                  AND attempts < max_attempts
                  AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                ORDER BY created_at
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            updated = conn.execute(
                """
                UPDATE download_queue
                SET status = 'downloading',
                    attempts = attempts + 1,
                    progress = 10,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    error = NULL
                WHERE id = ?
                  AND status IN ('queued', 'retry')
                """,
                (row["id"],),
            ).rowcount
            conn.commit()
            if not updated:
                return None
            logger.debug("Claimed Telegram download task %s", row["id"])
            return dict(conn.execute("SELECT * FROM download_queue WHERE id = ?", (row["id"],)).fetchone())

    def mark_destination(self, task_id: str, destination: str) -> None:
        with connection() as conn:
            conn.execute("UPDATE download_queue SET destination = ? WHERE id = ?", (destination, task_id))
            conn.commit()

    def mark_done(self, task_id: str) -> None:
        logger.debug("Marking Telegram download task done: %s", task_id)
        with connection() as conn:
            row = conn.execute("SELECT * FROM download_queue WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE download_queue
                SET status = 'done', progress = 100, error = NULL, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO download_history (
                    id, file_unique_id, file_id, destination, status, attempts, error, finished_at
                )
                VALUES (?, ?, ?, ?, 'done', ?, NULL, CURRENT_TIMESTAMP)
                """,
                (row["id"], row["file_unique_id"], row["file_id"], row["destination"], row["attempts"]),
            )
            conn.commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        with connection() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM download_queue WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return
            status = "retry" if row["attempts"] < row["max_attempts"] else "failed"
            progress = 0 if status == "retry" else 100
            wait_time = self.retry_wait_seconds(row["attempts"])
            next_attempt_at = (datetime.utcnow() + timedelta(seconds=wait_time)).strftime("%Y-%m-%d %H:%M:%S") if status == "retry" else None
            if status == "retry":
                logger.warning(
                    "Telegram download task %s failed on attempt %s/%s; retrying in %.1fs: %s",
                    task_id, row["attempts"], row["max_attempts"], wait_time, error
                )
            else:
                logger.error(
                    "Telegram download task %s permanently failed after %s/%s attempts: %s",
                    task_id, row["attempts"], row["max_attempts"], error
                )
            conn.execute(
                """
                UPDATE download_queue
                SET status = ?, progress = ?, error = ?, next_attempt_at = ?, finished_at = CASE WHEN ? = 'failed' THEN CURRENT_TIMESTAMP ELSE finished_at END
                WHERE id = ?
                """,
                (status, progress, error, next_attempt_at, status, task_id),
            )
            if status == "failed":
                task = conn.execute("SELECT * FROM download_queue WHERE id = ?", (task_id,)).fetchone()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO download_history (
                        id, file_unique_id, file_id, destination, status, attempts, error, finished_at
                    )
                    VALUES (?, ?, ?, ?, 'failed', ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (task["id"], task["file_unique_id"], task["file_id"], task["destination"] or "", task["attempts"], error),
                )
            conn.commit()

    def retry_wait_seconds(self, attempts: int) -> float:
        return min(300.0, 2.0 ** max(0, attempts - 1))

    def progress(self) -> Dict[str, int]:
        logger.debug("Reading Telegram download queue progress")
        with connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM download_queue GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}
