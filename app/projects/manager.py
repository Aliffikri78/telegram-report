import json
import re
import uuid
from typing import Dict, List, Optional

try:
    from database.db import connection
    from database.migrate import migrate
except ImportError:
    from app.database.db import connection
    from app.database.migrate import migrate


class ProjectManager:
    def __init__(self):
        migrate()

    def list_companies(self) -> List[Dict]:
        return self._all("SELECT * FROM companies ORDER BY name")

    def list_projects(self) -> List[Dict]:
        return self._all("SELECT p.*, c.name AS company_name FROM projects p JOIN companies c ON c.id = p.company_id ORDER BY c.name, p.name")

    def list_sites(self, project_id: Optional[str] = None) -> List[Dict]:
        sql = "SELECT s.*, p.name AS project_name FROM sites s JOIN projects p ON p.id = s.project_id"
        params = ()
        if project_id:
            sql += " WHERE s.project_id = ?"
            params = (project_id,)
        sites = self._all(sql + " ORDER BY p.name, s.name", params)
        for site in sites:
            site["aliases_text"] = ", ".join(json.loads(site.get("aliases") or "[]"))
        return sites

    def list_categories(self, project_id: Optional[str] = None) -> List[Dict]:
        sql = "SELECT c.*, p.name AS project_name FROM categories c JOIN projects p ON p.id = c.project_id"
        params = ()
        if project_id:
            sql += " WHERE c.project_id = ?"
            params = (project_id,)
        return self._all(sql + " ORDER BY p.name, c.name", params)

    def list_tasks(self, project_id: Optional[str] = None) -> List[Dict]:
        sql = """
            SELECT t.*, c.name AS category_name, c.project_id, p.name AS project_name
            FROM tasks t
            JOIN categories c ON c.id = t.category_id
            JOIN projects p ON p.id = c.project_id
            WHERE t.active = 1
        """
        params = ()
        if project_id:
            sql += " AND c.project_id = ?"
            params = (project_id,)
        return self._all(sql + " ORDER BY p.name, c.name, t.name", params)

    def default_project_id(self) -> Optional[str]:
        with connection() as conn:
            row = conn.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
        return row["id"] if row else None

    def find_site(self, text: str, project_id: Optional[str] = None) -> Optional[Dict]:
        token = (text or "").lower()
        for site in self.list_sites(project_id):
            aliases = json.loads(site.get("aliases") or "[]")
            if token in [site["slug"].lower(), site["name"].lower(), *[a.lower() for a in aliases]]:
                return site
        return None

    def detect_site_free(self, text: str, project_id: Optional[str] = None) -> Optional[Dict]:
        aliases = {}
        for site in self.list_sites(project_id):
            aliases[site["name"].lower()] = site
            aliases[site["slug"].lower()] = site
            for alias in json.loads(site.get("aliases") or "[]"):
                aliases[alias.lower()] = site
        for token in re.split(r"[\s,;/\-_.]+", (text or "").lower()):
            if token in aliases:
                return aliases[token]
        match = re.search(r"\bzone\s*([a-z])\b", text or "", re.I)
        if match:
            return aliases.get(match.group(1).lower())
        return None

    def task_by_command(self, command: str, project_id: Optional[str] = None) -> Optional[Dict]:
        command = command.lower().lstrip("/")
        for task in self.list_tasks(project_id):
            if command in {str(task.get("command") or "").lower(), task["slug"].lower()}:
                return task
        return None

    def task_by_slug(self, slug: str, project_id: Optional[str] = None) -> Optional[Dict]:
        slug = slug.lower()
        for task in self.list_tasks(project_id):
            if task["slug"].lower() == slug:
                return task
        return None

    def create(self, entity: str, data: Dict) -> Dict:
        entity = self._entity(entity)
        item = self._normalize(entity, data)
        item["id"] = uuid.uuid4().hex
        with connection() as conn:
            columns = ", ".join(item.keys())
            placeholders = ", ".join(["?"] * len(item))
            conn.execute(f"INSERT INTO {entity} ({columns}) VALUES ({placeholders})", tuple(item.values()))
            conn.commit()
        return item

    def update(self, entity: str, item_id: str, data: Dict) -> Dict:
        entity = self._entity(entity)
        item = self._normalize(entity, data, partial=True)
        if not item:
            return {"id": item_id}
        with connection() as conn:
            assignments = ", ".join([f"{key} = ?" for key in item])
            conn.execute(f"UPDATE {entity} SET {assignments} WHERE id = ?", (*item.values(), item_id))
            conn.commit()
        return {"id": item_id, **item}

    def delete(self, entity: str, item_id: str) -> None:
        entity = self._entity(entity)
        with connection() as conn:
            conn.execute(f"DELETE FROM {entity} WHERE id = ?", (item_id,))
            conn.commit()

    def _all(self, sql: str, params: tuple = ()) -> List[Dict]:
        with connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def _entity(self, entity: str) -> str:
        allowed = {"companies", "projects", "sites", "categories", "tasks"}
        if entity not in allowed:
            raise ValueError("Unknown settings entity")
        return entity

    def _normalize(self, entity: str, data: Dict, partial: bool = False) -> Dict:
        out = {}
        if entity == "companies":
            if data.get("name") or not partial:
                out["name"] = data.get("name", "").strip()
        elif entity == "projects":
            for key in ("company_id", "name"):
                if data.get(key) or not partial:
                    out[key] = data.get(key, "").strip()
            if data.get("slug") or data.get("name") or not partial:
                out["slug"] = self._slug(data.get("slug") or data.get("name"))
        elif entity == "sites":
            for key in ("project_id", "name"):
                if data.get(key) or not partial:
                    out[key] = data.get(key, "").strip()
            if data.get("slug") or data.get("name") or not partial:
                out["slug"] = self._slug(data.get("slug") or data.get("name"))
            if "aliases" in data or not partial:
                aliases = data.get("aliases", [])
                if isinstance(aliases, str):
                    aliases = [a.strip() for a in aliases.split(",") if a.strip()]
                out["aliases"] = json.dumps(aliases)
        elif entity == "categories":
            for key in ("project_id", "name"):
                if data.get(key) or not partial:
                    out[key] = data.get(key, "").strip()
            if data.get("slug") or data.get("name") or not partial:
                out["slug"] = self._slug(data.get("slug") or data.get("name"))
        elif entity == "tasks":
            for key in ("category_id", "name"):
                if data.get(key) or not partial:
                    out[key] = data.get(key, "").strip()
            if data.get("slug") or data.get("name") or not partial:
                out["slug"] = self._slug(data.get("slug") or data.get("name"))
            for key in ("command", "title"):
                if key in data or not partial:
                    out[key] = data.get(key, "").strip()
            if "active" in data:
                out["active"] = 1 if str(data.get("active")).lower() not in {"0", "false", "no"} else 0
        return {k: v for k, v in out.items() if v is not None}

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", (value or "").lower()).strip("_")
