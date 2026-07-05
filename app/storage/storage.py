from pathlib import Path
import os
import re
from typing import Iterable, Optional, Set

from PIL import Image

try:
    from core.config import PHOTOS, REPORTS
    from core.logger import logger
except ImportError:
    from app.core.config import PHOTOS, REPORTS
    from app.core.logger import logger


class Storage:
    def __init__(self, photos_root: Path = PHOTOS, reports_root: Path = REPORTS):
        self.photos_root = Path(photos_root).resolve()
        self.reports_root = Path(reports_root).resolve()

    def ensure_dir(self, path: Path) -> Path:
        logger.debug("Ensuring directory exists: %s", path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def secure_path(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(self.photos_root)
        except ValueError as exc:
            raise ValueError(f"Path escapes SAVE_ROOT: {resolved}") from exc
        return resolved

    def list_dirs(self, path: Path) -> Iterable[Path]:
        if not path.exists():
            return []
        return [d for d in sorted(path.iterdir()) if d.is_dir()]

    def list_files(self, path: Path, suffixes: Optional[Set[str]] = None) -> Iterable[Path]:
        if not path.exists():
            return []
        files = [p for p in sorted(path.iterdir()) if p.is_file()]
        if suffixes:
            return [p for p in files if p.suffix.lower() in suffixes]
        return files

    def exists(self, path: Path) -> bool:
        return path.exists()

    def unlink(self, path: Path) -> None:
        logger.debug("Removing file if present: %s", path)
        path.unlink(missing_ok=True)

    def target_photo_dir(self, month: str, site: str, task: str, when: str) -> Path:
        safe_when = when if when in ("before", "after") else "unknown"
        path = self.secure_path(self.photos_root / self._safe_part(month) / self._safe_part(site) / self._safe_part(task) / safe_when)
        return self.ensure_dir(path)

    def allocate_photo_path(self, month: str, site: str, task: str, when: str, filename: str) -> Path:
        folder = self.target_photo_dir(month, site, task, when)
        return self.unique_path(folder / self._safe_filename(filename))

    def unique_path(self, path: Path) -> Path:
        path = self.secure_path(path)
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        index = 1
        while True:
            candidate = parent / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def temp_path_for(self, destination: Path) -> Path:
        destination = self.secure_path(destination)
        return destination.with_name(destination.name + ".tmp")

    def validate_image(self, path: Path) -> None:
        path = self.secure_path(path)
        if not path.exists():
            raise ValueError(f"Downloaded file missing: {path}")
        if path.stat().st_size <= 0:
            raise ValueError(f"Downloaded file is empty: {path}")
        with Image.open(path) as image:
            image.verify()

    def atomic_replace(self, source: Path, destination: Path) -> None:
        source = self.secure_path(source)
        destination = self.secure_path(destination)
        os.replace(source, destination)

    def _safe_part(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())
        cleaned = cleaned.strip("._")
        if not cleaned:
            raise ValueError("Empty path component")
        return cleaned

    def _safe_filename(self, value: str) -> str:
        name = Path(value).name
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name).strip("._")
        if not cleaned:
            raise ValueError("Empty filename")
        return cleaned
