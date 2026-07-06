import json
import threading
from queue import Queue
from typing import Any, Dict, List, Optional

try:
    from core.logger import logger
    from database.db import connection
    from database.migrate import migrate
except ImportError:
    from app.core.logger import logger
    from app.database.db import connection
    from app.database.migrate import migrate


class JobState(dict):
    def __init__(self, manager: "JobManager", job_id: str, initial: Dict[str, Any]):
        super().__init__(initial)
        self._manager = manager
        self._job_id = job_id

    def update(self, *args, **kwargs):
        result = super().update(*args, **kwargs)
        self._manager.save_state(self._job_id, dict(self))
        return result

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._manager.save_state(self._job_id, dict(self))


class JobManager:
    def __init__(self):
        migrate()
        self._states: Dict[str, JobState] = {}
        self._events: Dict[str, Queue] = {}
        self._cancelled = set()
        self._lock = threading.Lock()

    def create(self, job_id: str, name: str, payload: Dict[str, Any], state: Dict[str, Any]) -> JobState:
        initial = {**state, "state": state.get("state", "queued")}
        logger.info("Creating report job %s", job_id)
        with connection() as conn:
            logger.debug("Inserting job row %s", job_id)
            conn.execute(
                "INSERT INTO jobs (id, name, status, payload) VALUES (?, ?, ?, ?)",
                (job_id, name, initial["state"], json.dumps(payload)),
            )
            conn.commit()
        tracked = JobState(self, job_id, initial)
        with self._lock:
            self._states[job_id] = tracked
            self._events[job_id] = Queue()
        return tracked

    def get_state(self, job_id: str) -> Optional[JobState]:
        return self._states.get(job_id)

    def get_events(self, job_id: str) -> Optional[Queue]:
        return self._events.get(job_id)

    def cancel(self, job_id: str) -> bool:
        logger.info("Cancelling report job %s", job_id)
        with self._lock:
            self._cancelled.add(job_id)
        state = self.get_state(job_id)
        if state:
            state.update(state="cancelled", error="Cancelled by user", download=None)
            return True
        with connection() as conn:
            row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, result_path = NULL, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("cancelled", "Cancelled by user", job_id),
            )
            conn.commit()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def list_jobs(self, limit: int = 50, status: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 50), 200))
        where, params = self._job_filters(status, search)
        logger.debug("Listing report jobs limit=%s status=%s search=%s", safe_limit, status, search)
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, status, payload, result_path, error,
                       created_at, updated_at, started_at, finished_at
                FROM jobs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def job_counts(self) -> Dict[str, int]:
        logger.debug("Counting report jobs by status")
        counts = {"total": 0, "running": 0, "done": 0, "failed": 0}
        with connection() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        for row in rows:
            status = row["status"]
            count = row["count"]
            counts["total"] += count
            if status == "done":
                counts["done"] += count
            elif status == "error":
                counts["failed"] += count
            elif status in {"queued", "starting", "preprocess", "matching", "running"}:
                counts["running"] += count
        return counts

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        logger.debug("Fetching report job %s", job_id)
        with connection() as conn:
            row = conn.execute(
                """
                SELECT id, name, status, payload, result_path, error,
                       created_at, updated_at, started_at, finished_at
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def save_state(self, job_id: str, state: Dict[str, Any]) -> None:
        status = state.get("state") or state.get("status") or "unknown"
        result_path = state.get("download")
        error = state.get("error")
        logger.debug("Saving report job %s status=%s", job_id, status)
        with connection() as conn:
            row = conn.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
            payload = {}
            if row and row["payload"]:
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    logger.warning("Invalid job payload JSON for job %s", job_id)
            payload["_state"] = state
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    result_path = ?,
                    error = ?,
                    payload = ?,
                    started_at = COALESCE(started_at, CASE WHEN ? != 'queued' THEN CURRENT_TIMESTAMP END),
                    finished_at = CASE WHEN ? IN ('done', 'error', 'cancelled') THEN CURRENT_TIMESTAMP ELSE finished_at END
                WHERE id = ?
                """,
                (status, result_path, error, json.dumps(payload), status, status, job_id),
            )
            conn.commit()
        self.publish(job_id, state)

    def publish(self, job_id: str, state: Optional[Dict[str, Any]] = None) -> None:
        events = self.get_events(job_id)
        if events:
            logger.debug("Publishing report job event %s", job_id)
            events.put(json.dumps(state or self._states.get(job_id, {})))

    def _row_to_job(self, row) -> Dict[str, Any]:
        payload = {}
        raw_payload = row["payload"]
        if raw_payload:
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                logger.warning("Invalid job payload JSON for job %s", row["id"])
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "payload": payload,
            "result_path": row["result_path"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def _job_filters(self, status: Optional[str], search: Optional[str]):
        clauses = []
        params = []
        normalized_status = (status or "").strip().lower()
        if normalized_status and normalized_status != "all":
            if normalized_status == "running":
                clauses.append("status IN (?, ?, ?, ?, ?)")
                params.extend(["queued", "starting", "preprocess", "matching", "running"])
            elif normalized_status == "failed":
                clauses.append("status = ?")
                params.append("error")
            else:
                clauses.append("status = ?")
                params.append(normalized_status)

        normalized_search = (search or "").strip()
        if normalized_search:
            like = f"%{normalized_search}%"
            clauses.append(
                """
                (
                    id LIKE ? OR
                    name LIKE ? OR
                    status LIKE ? OR
                    payload LIKE ? OR
                    result_path LIKE ? OR
                    error LIKE ?
                )
                """
            )
            params.extend([like, like, like, like, like, like])

        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(clauses), params
