from typing import Dict, Optional

try:
    from core.logger import logger
    from database.db import connection
    from database.migrate import migrate
except ImportError:
    from app.core.logger import logger
    from app.database.db import connection
    from app.database.migrate import migrate


class SessionManager:
    def __init__(self):
        migrate()

    def create_session(self, chat_id: int, site: Optional[str] = None, task: Optional[str] = None, when: Optional[str] = None) -> Dict[str, Optional[str]]:
        data = {"site": site, "task": task, "when": when}
        logger.info("Creating upload session for chat %s", chat_id)
        with connection() as conn:
            logger.debug("Upserting upload session row for chat %s", chat_id)
            conn.execute(
                """
                INSERT INTO upload_sessions (chat_id, site, task, when_label)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    site = excluded.site,
                    task = excluded.task,
                    when_label = excluded.when_label
                """,
                (str(chat_id), site, task, when),
            )
            conn.commit()
        return data

    def get_session(self, chat_id: int) -> Dict[str, Optional[str]]:
        logger.debug("Reading upload session for chat %s", chat_id)
        with connection() as conn:
            row = conn.execute(
                "SELECT site, task, when_label FROM upload_sessions WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
        if not row:
            return {}
        return {"site": row["site"], "task": row["task"], "when": row["when_label"]}

    def update_session(self, chat_id: int, site: Optional[str] = None, task: Optional[str] = None, when: Optional[str] = None) -> Dict[str, Optional[str]]:
        current = self.get_session(chat_id)
        current.update({k: v for k, v in {"site": site, "task": task, "when": when}.items() if v is not None})
        logger.info("Updating upload session for chat %s", chat_id)
        return self.create_session(chat_id, current.get("site"), current.get("task"), current.get("when"))

    def clear_session(self, chat_id: int) -> None:
        logger.info("Clearing upload session for chat %s", chat_id)
        with connection() as conn:
            logger.debug("Deleting upload session row for chat %s", chat_id)
            conn.execute("DELETE FROM upload_sessions WHERE chat_id = ?", (str(chat_id),))
            conn.commit()
