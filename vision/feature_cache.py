import os
import pickle
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from core.config import DATA
except ImportError:
    from app.core.config import DATA


CACHE_PATH = Path(
    os.getenv("MATCHER_FEATURE_CACHE_PATH", str(DATA / "cache" / "matcher_features.pkl"))
).resolve()


class FeatureCache:
    def __init__(self, path: Path = CACHE_PATH):
        self.path = Path(path)
        self.cache_hits = 0
        self.cache_misses = 0
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("rb") as fh:
                data = pickle.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("wb") as fh:
                pickle.dump(self._data, fh, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(self.path)
        except Exception:
            pass

    def key(self, path: Path, max_side: int, nfeatures: int) -> str:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        return "|".join(
            [
                str(resolved),
                str(stat.st_size),
                str(stat.st_mtime),
                str(max_side),
                str(nfeatures),
            ]
        )

    def get(self, path: Path, max_side: int, nfeatures: int) -> Optional[Dict[str, Any]]:
        try:
            item = self._data.get(self.key(path, max_side, nfeatures))
            if isinstance(item, dict):
                self.cache_hits += 1
                return item
        except Exception:
            pass
        self.cache_misses += 1
        return None

    def set(self, path: Path, max_side: int, nfeatures: int, value: Dict[str, Any]) -> None:
        try:
            self._data[self.key(path, max_side, nfeatures)] = value
        except Exception:
            pass
