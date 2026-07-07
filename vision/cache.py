import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from core.config import DATA
except ImportError:
    from app.core.config import DATA


CACHE_PATH = Path(os.getenv("VISION_CACHE_PATH", str(DATA / "cache" / "vision_analysis.json"))).resolve()


class VisionCache:
    def __init__(self, path: Path = CACHE_PATH):
        self.path = Path(path)
        self.cache_hits = 0
        self.cache_misses = 0
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def signature(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        return {"path": str(path.resolve()), "size": stat.st_size, "mtime": stat.st_mtime}

    def get(self, path: Path, key: str) -> Optional[Any]:
        sig = self.signature(path)
        record = self._data.get(sig["path"])
        if record and record.get("size") == sig["size"] and record.get("mtime") == sig["mtime"] and key in record:
            self.cache_hits += 1
            return record[key]
        self.cache_misses += 1
        return None

    def set(self, path: Path, key: str, value: Any) -> None:
        sig = self.signature(path)
        record = self._data.setdefault(sig["path"], {})
        record.update({"size": sig["size"], "mtime": sig["mtime"]})
        record[key] = value
