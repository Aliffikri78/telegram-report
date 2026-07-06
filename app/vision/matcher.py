import os
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import cv2
import imagehash
import numpy as np
from PIL import Image


MAX_SIDE = int(os.getenv("FAST_MAX_SIDE", "1600"))
NFEATURES = int(os.getenv("FAST_NFEATURES", "600"))
TOPK = int(os.getenv("FAST_TOPK", "5"))
RATIO = float(os.getenv("FAST_RATIO", "0.75"))

Pair = Tuple[Path, Path, float]
VALID_BACKENDS = {"auto", "cpu", "gpu"}


def opencv_cuda_available() -> bool:
    try:
        return hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0
    except Exception:
        return False


def resolve_backend(requested: str) -> Tuple[str, str, str]:
    requested = (requested or "cpu").lower()
    if requested not in VALID_BACKENDS:
        requested = "cpu"
    if requested == "cpu":
        return requested, "cpu", "CPU matcher selected"
    if requested == "auto":
        return requested, "cpu", "GPU backend not implemented yet; using CPU"
    if opencv_cuda_available():
        return requested, "cpu", "GPU backend not implemented yet; using CPU"
    return requested, "cpu", "OpenCV CUDA unavailable; using CPU"


def imread_gray_resized(path: Path):
    arr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        return None
    h, w = arr.shape
    m = max(h, w)
    if m > MAX_SIDE and m > 0:
        scale = MAX_SIDE / m
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return arr


def phash(path: Path) -> int:
    with Image.open(path) as im:
        return int(str(imagehash.phash(im.convert("RGB"))), 16)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def score_pair(des_a, des_b) -> float:
    if des_a is None or des_b is None or len(des_a) == 0 or len(des_b) == 0:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    try:
        matches = bf.knnMatch(des_a, des_b, k=2)
    except cv2.error:
        return 0.0
    good = 0
    for m_n in matches:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < RATIO * n.distance:
            good += 1
    return min(1.0, good / 200.0)


def match_pairs(
    befores: List[Path],
    afters: List[Path],
    threshold: float,
    progress: Dict,
    should_cancel: Callable[[], bool],
    cancelled_exception,
    backend: str = "cpu",
) -> List[Pair]:
    backend_requested, backend_used, backend_note = resolve_backend(backend)
    progress.update(
        backend_requested=backend_requested,
        backend_used=backend_used,
        backend_note=backend_note,
    )

    orb = cv2.ORB_create(nfeatures=NFEATURES)
    be_ph, af_ph = [], []
    be_desc, af_desc = {}, {}

    for p in befores:
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        be_ph.append(phash(p))
        arr = imread_gray_resized(p)
        _, d = orb.detectAndCompute(arr, None) if arr is not None else (None, None)
        be_desc[p] = d

    for p in afters:
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        af_ph.append(phash(p))
        arr = imread_gray_resized(p)
        _, d = orb.detectAndCompute(arr, None) if arr is not None else (None, None)
        af_desc[p] = d

    progress.update(state="matching", done=0, matched=0, unmatched=0)

    pairs = []
    used = {}
    af_items = list(zip(afters, af_ph))
    for idx, b in enumerate(befores):
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        dlist = [(j, hamming(be_ph[idx], aph)) for j, (_, aph) in enumerate(af_items) if not used.get(j)]
        dlist.sort(key=lambda x: x[1])
        cand = [j for j, _ in dlist[:max(1, TOPK)]]
        best_j, best_s = None, -1.0
        for j in cand:
            apath = af_items[j][0]
            s = score_pair(be_desc[b], af_desc[apath])
            if s > best_s:
                best_s, best_j = s, j
        if best_j is not None and best_s >= threshold:
            used[best_j] = True
            pairs.append((b, afters[best_j], best_s))
            progress["matched"] += 1
        else:
            progress["unmatched"] += 1
        progress["done"] = idx + 1

    return pairs
