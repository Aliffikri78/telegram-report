import sqlite3
from contextlib import contextmanager
from typing import Iterator

try:
    from core.config import DB
    from core.logger import logger
except ImportError:
    from app.core.config import DB
    from app.core.logger import logger


DB_PATH = DB


def get_connection() -> sqlite3.Connection:
    logger.debug("Opening SQLite connection: %s", DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        logger.debug("Closing SQLite connection: %s", DB_PATH)
        conn.close()
