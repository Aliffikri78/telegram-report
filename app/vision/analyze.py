import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .cache import VisionCache
from .duplicates import find_duplicate_groups, find_similar_groups
from .quality import score_image


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def list_images(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS)


def rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def relativize(value: Any, root: Path) -> Any:
    if isinstance(value, list):
        return [relativize(item, root) for item in value]
    if isinstance(value, dict):
        return {relativize(key, root): relativize(item, root) for key, item in value.items()}
    if isinstance(value, str):
        try:
            path = Path(value)
            if path.is_absolute():
                return rel(path, root)
        except Exception:
            return value
    return value


def file_info(path: Path, root: Path) -> Dict:
    stat = path.stat()
    return {"file": rel(path, root), "name": path.name, "size": stat.st_size, "mtime": stat.st_mtime}


def best_images_for_groups(groups: Iterable[Dict], quality_by_file: Dict[str, Dict]) -> List[Dict]:
    best = []
    for group in groups:
        scored = [quality_by_file[file] for file in group["files"] if file in quality_by_file]
        if not scored:
            continue
        winner = max(scored, key=lambda item: item["overall_score"])
        best.append(
            {
                "group_kind": group["kind"],
                "file": winner["file"],
                "overall_score": winner["overall_score"],
                "group_count": group["count"],
            }
        )
    return best


def analyze_folder(root: Path, cache: VisionCache, max_distance: int = 5) -> Dict:
    started = time.perf_counter()
    images = list_images(root)
    files = []
    quality_scores = []

    for image in images:
        try:
            files.append(file_info(image, root))
        except Exception as exc:
            files.append({"file": str(image), "error": str(exc)})

        try:
            quality_scores.append(score_image(image, cache))
        except Exception as exc:
            quality_scores.append({"file": str(image), "error": str(exc)})

    similar_groups = find_similar_groups(images, cache, max_distance=max_distance)
    duplicate_groups = find_duplicate_groups(similar_groups, cache)

    quality_by_file = {item["file"]: item for item in quality_scores if "overall_score" in item}
    all_groups = duplicate_groups + similar_groups
    best_images = best_images_for_groups(all_groups, quality_by_file)

    result = {
        "files": files,
        "duplicate_groups": duplicate_groups,
        "similar_groups": similar_groups,
        "quality_scores": quality_scores,
        "best_images": best_images,
        "summary": {
            "images": len(images),
            "duplicate_groups": len(duplicate_groups),
            "similar_groups": len(similar_groups),
            "blurred": sum(1 for item in quality_scores if item.get("is_blurry")),
            "dark": sum(1 for item in quality_scores if item.get("is_dark")),
            "best_images": len(best_images),
        },
        "metrics": {
            "analysis_time_ms": round((time.perf_counter() - started) * 1000, 2),
            "cache_hits": cache.cache_hits,
            "cache_misses": cache.cache_misses,
        },
    }

    cache.save()
    return relativize(result, root)
