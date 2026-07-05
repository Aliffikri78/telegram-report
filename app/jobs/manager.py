import json
import threading
from queue import Queue
from typing import Any, Dict, Optional

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

    def save_state(self, job_id: str, state: Dict[str, Any]) -> None:
        status = state.get("state") or state.get("status") or "unknown"
        result_path = state.get("download")
        error = state.get("error")
        logger.debug("Saving report job %s status=%s", job_id, status)
        with connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    result_path = ?,
                    error = ?,
                    started_at = COALESCE(started_at, CASE WHEN ? != 'queued' THEN CURRENT_TIMESTAMP END),
                    finished_at = CASE WHEN ? IN ('done', 'error') THEN CURRENT_TIMESTAMP ELSE finished_at END
                WHERE id = ?
                """,
                (status, result_path, error, status, status, job_id),
            )
            conn.commit()
        self.publish(job_id, state)

    def publish(self, job_id: str, state: Optional[Dict[str, Any]] = None) -> None:
        events = self.get_events(job_id)
        if events:
            logger.debug("Publishing report job event %s", job_id)
            events.put(json.dumps(state or self._states.get(job_id, {})))
