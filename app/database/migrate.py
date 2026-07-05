try:
    from core.logger import logger
    from database.db import connection
    from database.models import SCHEMA
except ImportError:
    from app.core.logger import logger
    from app.database.db import connection
    from app.database.models import SCHEMA


def migrate() -> None:
    logger.info("Running SQLite migrations")
    with connection() as conn:
        for statement in SCHEMA:
            logger.debug("Executing migration statement")
            conn.execute(statement)
        _add_column(conn, "download_queue", "message_id", "TEXT")
        _add_column(conn, "download_queue", "message_date", "TEXT")
        _add_column(conn, "download_queue", "next_attempt_at", "TEXT")
        conn.commit()
    logger.info("SQLite migrations complete")


def _add_column(conn, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        logger.info("Adding SQLite column %s.%s", table, column)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


if __name__ == "__main__":
    migrate()
