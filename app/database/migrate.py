try:
    from core.config import PHOTOS
    from core.logger import logger
    from database.db import connection
    from database.models import SCHEMA
except ImportError:
    from app.core.config import PHOTOS
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
        _add_column(conn, "worker_upload_jobs", "worker_name", "TEXT")
        _add_column(conn, "worker_upload_jobs", "status", "TEXT NOT NULL DEFAULT 'draft'")
        _add_column(conn, "worker_upload_jobs", "before_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "worker_upload_jobs", "after_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "worker_upload_jobs", "files", "TEXT NOT NULL DEFAULT '[]'")
        _add_column(conn, "worker_upload_jobs", "ready_at", "TEXT")
        seed_defaults(conn)
        conn.commit()
    logger.info("SQLite migrations complete")


def _add_column(conn, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        logger.info("Adding SQLite column %s.%s", table, column)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def seed_defaults(conn) -> None:
    import json
    import re
    import uuid

    def slug(value: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")

    def get_or_create(table: str, where: str, params: tuple, values: dict) -> str:
        row = conn.execute(f"SELECT id FROM {table} WHERE {where}", params).fetchone()
        if row:
            return row["id"]
        item_id = uuid.uuid4().hex
        data = {"id": item_id, **values}
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(data.values()))
        return item_id

    company_id = get_or_create("companies", "name = ?", ("Default",), {"name": "Default"})
    project_id = get_or_create(
        "projects",
        "company_id = ? AND slug = ?",
        (company_id, "landscape"),
        {"company_id": company_id, "name": "Landscape", "slug": "landscape"},
    )
    grass_category_id = get_or_create(
        "categories",
        "project_id = ? AND slug = ?",
        (project_id, "grass_cutting"),
        {"project_id": project_id, "name": "Grass Cutting", "slug": "grass_cutting"},
    )
    drainage_category_id = get_or_create(
        "categories",
        "project_id = ? AND slug = ?",
        (project_id, "drainage"),
        {"project_id": project_id, "name": "Drainage", "slug": "drainage"},
    )
    get_or_create(
        "tasks",
        "category_id = ? AND slug = ?",
        (grass_category_id, "grass_cutting"),
        {"category_id": grass_category_id, "name": "Grass Cutting", "slug": "grass_cutting", "command": "grass", "title": "1. Grass Cutting"},
    )
    get_or_create(
        "tasks",
        "category_id = ? AND slug = ?",
        (drainage_category_id, "drainage_cleaning"),
        {"category_id": drainage_category_id, "name": "Drainage Cleaning", "slug": "drainage_cleaning", "command": "drainage", "title": "2. Drainage Cleaning"},
    )

    seeded_sites = {
        "ALPHA": ["alpha", "a"],
        "BRAVO": ["bravo", "b"],
        "CHARLIE": ["charlie", "c"],
        "DELTA": ["delta", "d"],
        "ECHO": ["echo", "e"],
    }
    if PHOTOS.exists():
        for month_dir in PHOTOS.iterdir():
            if not month_dir.is_dir() or not re.match(r"^\d{4}-\d{2}$", month_dir.name):
                continue
            for site_dir in month_dir.iterdir():
                if site_dir.is_dir():
                    seeded_sites.setdefault(site_dir.name.upper(), [site_dir.name.lower()])

    for name, aliases in seeded_sites.items():
        site_slug = slug(name)
        get_or_create(
            "sites",
            "project_id = ? AND slug = ?",
            (project_id, site_slug),
            {"project_id": project_id, "name": name, "slug": site_slug, "aliases": json.dumps(sorted(set(aliases + [name.lower()])) )},
        )


if __name__ == "__main__":
    migrate()
