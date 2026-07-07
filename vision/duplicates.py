from pathlib import Path
from typing import Dict, List

import hashlib
import imagehash
from PIL import Image

from .cache import VisionCache


def file_sha256(path: Path, cache: VisionCache) -> str:
    cached = cache.get(path, "sha256")
    if cached:
        return cached
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    cache.set(path, "sha256", digest)
    return digest


def perceptual_hash(path: Path, cache: VisionCache) -> str:
    cached = cache.get(path, "phash")
    if cached:
        return cached
    with Image.open(path) as image:
        digest = str(imagehash.phash(image.convert("RGB")))
    cache.set(path, "phash", digest)
    return digest


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def find_similar_groups(images: List[Path], cache: VisionCache, max_distance: int = 5) -> List[Dict]:
    hashes = []
    for path in images:
        try:
            hashes.append((path, perceptual_hash(path, cache)))
        except Exception:
            continue

    groups = []
    used = set()
    for idx, (path, digest) in enumerate(hashes):
        if path in used:
            continue
        files = [path]
        distances = {str(path): 0}
        used.add(path)
        for other, other_digest in hashes[idx + 1:]:
            if other in used:
                continue
            distance = hamming(digest, other_digest)
            if distance <= max_distance:
                files.append(other)
                distances[str(other)] = distance
                used.add(other)
        if len(files) > 1:
            groups.append(
                {
                    "kind": "near",
                    "max_distance": max_distance,
                    "files": [str(item) for item in files],
                    "distances": distances,
                    "count": len(files),
                }
            )
    return groups


def find_duplicate_groups(similar_groups: List[Dict], cache: VisionCache) -> List[Dict]:
    duplicate_groups = []
    for group in similar_groups:
        by_sha = {}
        for file_name in group["files"]:
            path = Path(file_name)
            try:
                digest = file_sha256(path, cache)
                by_sha.setdefault(digest, []).append(path)
            except Exception:
                continue
        for digest, paths in by_sha.items():
            if len(paths) > 1:
                duplicate_groups.append(
                    {
                        "kind": "exact",
                        "key": digest,
                        "files": [str(path) for path in paths],
                        "count": len(paths),
                    }
                )
    return duplicate_groups
