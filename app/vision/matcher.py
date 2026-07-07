import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import cv2
import imagehash
import numpy as np
from PIL import Image

try:
    from vision import ai_matcher
    from vision.feature_cache import FeatureCache
except ImportError:
    from app.vision import ai_matcher
    from app.vision.feature_cache import FeatureCache


MAX_SIDE = int(os.getenv("FAST_MAX_SIDE", "1600"))
NFEATURES = int(os.getenv("FAST_NFEATURES", "600"))
TOPK = int(os.getenv("FAST_TOPK", "30"))
SECOND_PASS = int(os.getenv("FAST_SECOND_PASS", "30"))
RATIO = float(os.getenv("FAST_RATIO", "0.75"))
ORB_VERIFY_GEOMETRY = os.getenv("ORB_VERIFY_GEOMETRY", "0").lower() in {"1", "true", "yes", "on"}
ORB_MIN_INLIERS = int(os.getenv("ORB_MIN_INLIERS", "5"))
MATCH_WEIGHT_PHASH = float(os.getenv("MATCH_WEIGHT_PHASH", "0.30"))
MATCH_WEIGHT_ORB = float(os.getenv("MATCH_WEIGHT_ORB", "0.40"))
MATCH_WEIGHT_HIST = float(os.getenv("MATCH_WEIGHT_HIST", "0.20"))
MATCH_WEIGHT_SSIM = float(os.getenv("MATCH_WEIGHT_SSIM", "0.10"))
MATCH_WEIGHT_EDGE = float(os.getenv("MATCH_WEIGHT_EDGE", "0.10"))
MATCH_ASSIGNMENT = os.getenv("MATCH_ASSIGNMENT", "greedy").strip().lower()

Pair = Tuple[Path, Path, float]
VALID_BACKENDS = {"auto", "cpu", "gpu", "ai"}
VALID_ASSIGNMENTS = {"greedy", "hungarian", "auto"}


def get_linear_sum_assignment():
    try:
        from scipy.optimize import linear_sum_assignment
        return linear_sum_assignment
    except Exception:
        return None


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
    if requested == "ai":
        available, note = ai_matcher.availability()
        implemented = bool(getattr(ai_matcher, "IMPLEMENTED", False))
        if available and implemented:
            return requested, "ai", "AI matcher selected"
        return requested, "cpu", f"{note}; using CPU"
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


def serialize_keypoints(keypoints) -> List[Tuple[float, float, float, float, float, int, int]]:
    return [
        (kp.pt[0], kp.pt[1], kp.size, kp.angle, kp.response, kp.octave, kp.class_id)
        for kp in (keypoints or [])
    ]


def deserialize_keypoints(items) -> List:
    keypoints = []
    for x, y, size, angle, response, octave, class_id in items or []:
        keypoints.append(
            cv2.KeyPoint(float(x), float(y), float(size), float(angle), float(response), int(octave), int(class_id))
        )
    return keypoints


def compute_features(path: Path, orb, cache: FeatureCache):
    gray = imread_gray_resized(path)
    if gray is None:
        raise ValueError("OpenCV could not decode image")
    cached = cache.get(path, MAX_SIDE, NFEATURES) if cache else None
    if cached:
        try:
            return (
                cached["phash"],
                gray,
                deserialize_keypoints(cached.get("keypoints")),
                cached.get("descriptors"),
            )
        except Exception:
            pass

    image_hash = phash(path)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if cache:
        cache.set(
            path,
            MAX_SIDE,
            NFEATURES,
            {
                "phash": image_hash,
                "keypoints": serialize_keypoints(keypoints),
                "descriptors": descriptors,
            },
        )
    return image_hash, gray, keypoints or [], descriptors


def image_error(path: Path, error: Exception) -> Dict:
    return {"image": str(path), "error": str(error)}


def load_feature_set(paths, orb, cache, should_cancel, cancelled_exception):
    valid_paths = []
    hashes = []
    keypoints = {}
    descriptors = {}
    grays = {}
    errors = []
    for p in paths:
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        try:
            image_hash, gray, kp, desc = compute_features(p, orb, cache)
        except Exception as exc:
            errors.append(image_error(p, exc))
            continue
        valid_paths.append(p)
        hashes.append(image_hash)
        grays[p] = gray
        keypoints[p] = kp or []
        descriptors[p] = desc
    return valid_paths, hashes, keypoints, descriptors, grays, errors


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


def spatial_orb_score(kp_a, des_a, kp_b, des_b) -> Dict:
    result = {
        "orb_score": 0.0,
        "good_matches": 0,
        "inliers": 0,
        "spatial_verified": False,
        "reason": "",
    }
    if des_a is None or des_b is None or len(des_a) == 0 or len(des_b) == 0:
        result["reason"] = "missing_orb_descriptors"
        return result
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    try:
        matches = bf.knnMatch(des_a, des_b, k=2)
    except cv2.error:
        result["reason"] = "orb_match_failed"
        return result

    good = []
    for m_n in matches:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < RATIO * n.distance:
            good.append(m)

    result["good_matches"] = len(good)
    result["orb_score"] = min(1.0, len(good) / 200.0)
    if len(good) < 4:
        result["reason"] = "not_enough_good_orb_matches"
        return result

    try:
        src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    except cv2.error:
        result["reason"] = "homography_failed"
        return result

    inliers = int(mask.sum()) if mask is not None else 0
    result["inliers"] = inliers
    result["spatial_verified"] = inliers >= ORB_MIN_INLIERS
    result["reason"] = "accepted" if result["spatial_verified"] else "not_enough_ransac_inliers"
    return result


def phash_similarity(distance: int) -> float:
    return max(0.0, min(1.0, 1.0 - (distance / 64.0)))


def hist_similarity(gray_a, gray_b) -> float:
    if gray_a is None or gray_b is None:
        return 0.0
    hist_a = cv2.calcHist([gray_a], [0], None, [64], [0, 256])
    hist_b = cv2.calcHist([gray_b], [0], None, [64], [0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    score = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
    return max(0.0, min(1.0, (score + 1.0) / 2.0))


def simple_ssim_similarity(gray_a, gray_b) -> float:
    if gray_a is None or gray_b is None:
        return 0.0
    size = (128, 128)
    a = cv2.resize(gray_a, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    b = cv2.resize(gray_b, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    c1 = 6.5025
    c2 = 58.5225
    mu_a, mu_b = a.mean(), b.mean()
    var_a, var_b = a.var(), b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    score = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))
    return max(0.0, min(1.0, float(score)))


def edge_similarity(gray_a, gray_b) -> float:
    if gray_a is None or gray_b is None:
        return 0.0
    size = (128, 128)
    a = cv2.resize(gray_a, size, interpolation=cv2.INTER_AREA)
    b = cv2.resize(gray_b, size, interpolation=cv2.INTER_AREA)
    edge_a = cv2.Canny(a, 50, 150)
    edge_b = cv2.Canny(b, 50, 150)
    count_a = cv2.countNonZero(edge_a)
    count_b = cv2.countNonZero(edge_b)
    if count_a == 0 and count_b == 0:
        return 1.0
    if count_a == 0 or count_b == 0:
        return 0.0
    overlap = cv2.countNonZero(cv2.bitwise_and(edge_a, edge_b))
    return max(0.0, min(1.0, (2.0 * overlap) / (count_a + count_b)))


def weighted_score(phash_distance: int, orb_score: float, gray_a, gray_b) -> float:
    try:
        p_score = phash_similarity(phash_distance)
        h_score = hist_similarity(gray_a, gray_b)
        s_score = simple_ssim_similarity(gray_a, gray_b)
        e_score = edge_similarity(gray_a, gray_b)
        total_weight = MATCH_WEIGHT_PHASH + MATCH_WEIGHT_ORB + MATCH_WEIGHT_HIST + MATCH_WEIGHT_SSIM + MATCH_WEIGHT_EDGE
        if total_weight <= 0:
            return orb_score
        return (
            (MATCH_WEIGHT_PHASH * p_score)
            + (MATCH_WEIGHT_ORB * orb_score)
            + (MATCH_WEIGHT_HIST * h_score)
            + (MATCH_WEIGHT_SSIM * s_score)
            + (MATCH_WEIGHT_EDGE * e_score)
        ) / total_weight
    except Exception:
        return orb_score


def candidate_score(phash_distance: int, kp_a, des_a, kp_b, des_b, gray_a, gray_b) -> Dict:
    orb = spatial_orb_score(kp_a, des_a, kp_b, des_b)
    try:
        p_score = phash_similarity(phash_distance)
        h_score = hist_similarity(gray_a, gray_b)
        s_score = simple_ssim_similarity(gray_a, gray_b)
        e_score = edge_similarity(gray_a, gray_b)
    except Exception:
        p_score = phash_similarity(phash_distance)
        h_score = 0.0
        s_score = 0.0
        e_score = 0.0

    return {
        "phash_similarity": p_score,
        "orb_score": orb["orb_score"],
        "histogram_score": h_score,
        "ssim_score": s_score,
        "edge_score": e_score,
        "final_score": orb["orb_score"],
        "good_matches": orb["good_matches"],
        "inliers": orb["inliers"],
        "spatial_verified": orb["spatial_verified"],
        "reason": orb["reason"],
        "keypoint_count": min(len(kp_a or []), len(kp_b or [])),
    }


def normalize_candidates(details: List[Dict]) -> None:
    for source_key, normalized_key in [
        ("orb_score", "orb_score_normalized"),
        ("histogram_score", "histogram_score_normalized"),
        ("ssim_score", "ssim_score_normalized"),
        ("edge_score", "edge_score_normalized"),
    ]:
        values = [float(detail.get(source_key, 0.0)) for detail in details]
        if not values:
            continue
        low = min(values)
        high = max(values)
        if high - low < 1e-6:
            for detail in details:
                detail[normalized_key] = float(detail.get(source_key, 0.0))
            continue
        for detail in details:
            detail[normalized_key] = (float(detail.get(source_key, 0.0)) - low) / (high - low)


def adaptive_weights(keypoint_count: int) -> Dict[str, float]:
    weights = {
        "phash": MATCH_WEIGHT_PHASH,
        "orb": MATCH_WEIGHT_ORB,
        "hist": MATCH_WEIGHT_HIST,
        "ssim": MATCH_WEIGHT_SSIM,
        "edge": MATCH_WEIGHT_EDGE,
    }
    if keypoint_count >= 250:
        weights["orb"] *= 1.35
        weights["hist"] *= 0.85
    elif keypoint_count <= 80:
        weights["hist"] *= 1.45
        weights["orb"] *= 0.70
        weights["edge"] *= 1.15
    return weights


def apply_final_scores(details: List[Dict]) -> None:
    normalize_candidates(details)
    for detail in details:
        weights = adaptive_weights(int(detail.get("keypoint_count", 0)))
        total_weight = sum(weights.values())
        if total_weight <= 0:
            detail["final_score"] = detail.get("orb_score", 0.0)
            continue
        detail["final_score"] = (
            (weights["phash"] * detail.get("phash_similarity", 0.0))
            + (weights["orb"] * detail.get("orb_score_normalized", detail.get("orb_score", 0.0)))
            + (weights["hist"] * detail.get("histogram_score_normalized", detail.get("histogram_score", 0.0)))
            + (weights["ssim"] * detail.get("ssim_score_normalized", detail.get("ssim_score", 0.0)))
            + (weights["edge"] * detail.get("edge_score_normalized", detail.get("edge_score", 0.0)))
        ) / total_weight
        detail["weights"] = weights


def rejection_reason(detail: Dict, threshold: float) -> str:
    if detail.get("final_score", 0.0) < threshold:
        return "below_threshold"
    if ORB_VERIFY_GEOMETRY and not detail.get("spatial_verified"):
        return detail.get("reason") or "spatial_verification_failed"
    return detail.get("reason") or "not_selected"


def unmatched_debug(before: Path, detail: Dict, threshold: float) -> Dict:
    if not detail:
        return {
            "before": str(before),
            "top_candidate": None,
            "phash_similarity": 0.0,
            "orb_score": 0.0,
            "histogram_score": 0.0,
            "ssim_score": 0.0,
            "edge_score": 0.0,
            "final_score": 0.0,
            "inliers": 0,
            "reason": "no_candidates",
        }
    return {
        "before": str(before),
        "top_candidate": detail.get("candidate"),
        "phash_similarity": round(float(detail.get("phash_similarity", 0.0)), 4),
        "orb_score": round(float(detail.get("orb_score", 0.0)), 4),
        "orb_score_normalized": round(float(detail.get("orb_score_normalized", detail.get("orb_score", 0.0))), 4),
        "histogram_score": round(float(detail.get("histogram_score", 0.0)), 4),
        "histogram_score_normalized": round(float(detail.get("histogram_score_normalized", detail.get("histogram_score", 0.0))), 4),
        "ssim_score": round(float(detail.get("ssim_score", 0.0)), 4),
        "ssim_score_normalized": round(float(detail.get("ssim_score_normalized", detail.get("ssim_score", 0.0))), 4),
        "edge_score": round(float(detail.get("edge_score", 0.0)), 4),
        "edge_score_normalized": round(float(detail.get("edge_score_normalized", detail.get("edge_score", 0.0))), 4),
        "final_score": round(float(detail.get("final_score", 0.0)), 4),
        "inliers": int(detail.get("inliers", 0)),
        "reason": rejection_reason(detail, threshold),
    }


def choose_candidate(candidate_ids, be_path, be_hash, af_items, be_kp, be_desc, af_kp, af_desc, be_gray, af_gray, threshold):
    best_accept = None
    best_debug = None
    details = []
    for j in candidate_ids:
        apath, ahash = af_items[j]
        detail = candidate_score(
            hamming(be_hash, ahash),
            be_kp.get(be_path),
            be_desc.get(be_path),
            af_kp.get(apath),
            af_desc.get(apath),
            be_gray.get(be_path),
            af_gray.get(apath),
        )
        detail["candidate"] = str(apath)
        detail["candidate_index"] = j
        details.append(detail)
    apply_final_scores(details)
    for detail in details:
        if best_debug is None or detail["final_score"] > best_debug["final_score"]:
            best_debug = detail
        geometry_ok = detail["spatial_verified"] or not ORB_VERIFY_GEOMETRY
        if detail["final_score"] >= threshold and geometry_ok:
            if best_accept is None or detail["final_score"] > best_accept["final_score"]:
                best_accept = detail
    return best_accept, best_debug, len(details)


def match_detail(before: Path, after: Path, detail: Dict) -> Dict:
    confidence = max(0.0, min(100.0, float(detail.get("final_score", 0.0)) * 100.0))
    return {
        "before": str(before),
        "after": str(after),
        "final_score": round(float(detail.get("final_score", 0.0)), 4),
        "phash_similarity": round(float(detail.get("phash_similarity", 0.0)), 4),
        "orb_score": round(float(detail.get("orb_score", 0.0)), 4),
        "histogram_score": round(float(detail.get("histogram_score", 0.0)), 4),
        "ssim_score": round(float(detail.get("ssim_score", 0.0)), 4),
        "edge_score": round(float(detail.get("edge_score", 0.0)), 4),
        "confidence": round(confidence, 1),
        "good_matches": int(detail.get("good_matches", 0)),
        "inliers": int(detail.get("inliers", 0)),
    }


def update_matching_metrics(progress: Dict, start_time: float, total_images: int, candidate_comparisons: int) -> None:
    elapsed = max(0.0, time.perf_counter() - start_time)
    confidences = [float(item.get("confidence", 0.0)) for item in progress.get("match_debug", [])]
    progress["matching_time_sec"] = round(elapsed, 3)
    progress["avg_time_per_image_sec"] = round(elapsed / total_images, 3) if total_images else 0.0
    progress["images_per_sec"] = round(total_images / elapsed, 3) if elapsed > 0 else 0.0
    progress["candidate_comparisons"] = candidate_comparisons
    progress["cache_hits"] = progress.get("cache_hits", 0)
    progress["average_confidence"] = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    progress["lowest_confidence"] = round(min(confidences), 1) if confidences else 0.0
    progress["highest_confidence"] = round(max(confidences), 1) if confidences else 0.0


def reset_assignment_progress(progress: Dict, method: str) -> None:
    progress.update(done=0, matched=0, unmatched=0, unmatched_debug=[], match_debug=[], assignment_method=method)


def assignment_snapshot(progress: Dict) -> Dict:
    return {
        "done": progress.get("done", 0),
        "matched": progress.get("matched", 0),
        "unmatched": progress.get("unmatched", 0),
        "unmatched_debug": list(progress.get("unmatched_debug", [])),
        "match_debug": list(progress.get("match_debug", [])),
        "assignment_method": progress.get("assignment_method"),
        "candidate_comparisons": progress.get("candidate_comparisons", 0),
        "average_confidence": progress.get("average_confidence", 0.0),
        "lowest_confidence": progress.get("lowest_confidence", 0.0),
        "highest_confidence": progress.get("highest_confidence", 0.0),
    }


def restore_assignment_snapshot(progress: Dict, snapshot: Dict) -> None:
    progress.update(snapshot)


def geometry_ok(detail: Dict) -> bool:
    return detail.get("spatial_verified") or not ORB_VERIFY_GEOMETRY


def build_global_candidates(
    befores,
    afters,
    be_ph,
    af_ph,
    be_kp,
    be_desc,
    af_kp,
    af_desc,
    be_gray,
    af_gray,
    progress,
    should_cancel,
    cancelled_exception,
    match_start,
    total_images,
):
    af_items = list(zip(afters, af_ph))
    rows = []
    candidate_comparisons = 0
    for idx, b in enumerate(befores):
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        details = []
        for j, (apath, ahash) in enumerate(af_items):
            detail = candidate_score(
                hamming(be_ph[idx], ahash),
                be_kp.get(b),
                be_desc.get(b),
                af_kp.get(apath),
                af_desc.get(apath),
                be_gray.get(b),
                af_gray.get(apath),
            )
            detail["candidate"] = str(apath)
            detail["candidate_index"] = j
            details.append(detail)
        apply_final_scores(details)
        rows.append(details)
        candidate_comparisons += len(details)
        progress["done"] = idx + 1
        update_matching_metrics(progress, match_start, total_images, candidate_comparisons)
    return rows, candidate_comparisons


def apply_hungarian_assignment(befores, afters, rows, threshold, progress):
    score_matrix = np.array([[float(detail.get("final_score", 0.0)) for detail in row] for row in rows], dtype=np.float32)
    if score_matrix.size == 0:
        return []

    linear_sum_assignment = get_linear_sum_assignment()
    if linear_sum_assignment is None:
        return None

    before_ids, after_ids = linear_sum_assignment(-score_matrix)
    pairs = []
    assigned_before = set()
    for before_idx, after_idx in zip(before_ids, after_ids):
        detail = rows[before_idx][after_idx]
        assigned_before.add(before_idx)
        if detail["final_score"] >= threshold and geometry_ok(detail):
            pairs.append((befores[before_idx], afters[after_idx], detail["final_score"]))
            progress["match_debug"].append(match_detail(befores[before_idx], afters[after_idx], detail))
            progress["matched"] += 1
        else:
            progress["unmatched"] += 1
            progress["unmatched_debug"].append(unmatched_debug(befores[before_idx], detail, threshold))

    for before_idx, b in enumerate(befores):
        if before_idx in assigned_before:
            continue
        best_debug = max(rows[before_idx], key=lambda detail: detail.get("final_score", 0.0), default=None)
        progress["unmatched"] += 1
        progress["unmatched_debug"].append(unmatched_debug(b, best_debug, threshold))
    return pairs


def run_hungarian_assignment(
    befores,
    afters,
    be_ph,
    af_ph,
    be_kp,
    be_desc,
    af_kp,
    af_desc,
    be_gray,
    af_gray,
    threshold,
    progress,
    should_cancel,
    cancelled_exception,
    match_start,
    total_images,
):
    rows, candidate_comparisons = build_global_candidates(
        befores,
        afters,
        be_ph,
        af_ph,
        be_kp,
        be_desc,
        af_kp,
        af_desc,
        be_gray,
        af_gray,
        progress,
        should_cancel,
        cancelled_exception,
        match_start,
        total_images,
    )
    pairs = apply_hungarian_assignment(befores, afters, rows, threshold, progress)
    if pairs is None:
        return None, candidate_comparisons
    return pairs, candidate_comparisons


def apply_greedy_assignment(
    befores,
    afters,
    be_ph,
    af_ph,
    be_kp,
    be_desc,
    af_kp,
    af_desc,
    be_gray,
    af_gray,
    threshold,
    progress,
    should_cancel,
    cancelled_exception,
    match_start,
    total_images,
):
    pairs = []
    used = {}
    candidate_comparisons = 0
    af_items = list(zip(afters, af_ph))
    for idx, b in enumerate(befores):
        if should_cancel():
            raise cancelled_exception("Cancelled by user")
        dlist = [(j, hamming(be_ph[idx], aph)) for j, (_, aph) in enumerate(af_items) if not used.get(j)]
        dlist.sort(key=lambda x: x[1])
        primary = [j for j, _ in dlist[:max(1, TOPK)]]
        accepted, debug, compared = choose_candidate(
            primary,
            b,
            be_ph[idx],
            af_items,
            be_kp,
            be_desc,
            af_kp,
            af_desc,
            be_gray,
            af_gray,
            threshold,
        )
        candidate_comparisons += compared
        if accepted is None and SECOND_PASS > TOPK:
            expanded = [j for j, _ in dlist[:max(1, SECOND_PASS)]]
            accepted, debug, compared = choose_candidate(
                expanded,
                b,
                be_ph[idx],
                af_items,
                be_kp,
                be_desc,
                af_kp,
                af_desc,
                be_gray,
                af_gray,
                threshold,
            )
            candidate_comparisons += compared
        if accepted is not None:
            best_j = accepted["candidate_index"]
            best_s = accepted["final_score"]
            used[best_j] = True
            pairs.append((b, afters[best_j], best_s))
            progress["match_debug"].append(match_detail(b, afters[best_j], accepted))
            progress["matched"] += 1
        else:
            progress["unmatched"] += 1
            progress["unmatched_debug"].append(unmatched_debug(b, debug, threshold))
        progress["done"] = idx + 1
        update_matching_metrics(progress, match_start, total_images, candidate_comparisons)
    return pairs, candidate_comparisons


def match_pairs(
    befores: List[Path],
    afters: List[Path],
    threshold: float,
    progress: Dict,
    should_cancel: Callable[[], bool],
    cancelled_exception,
    backend: str = "cpu",
) -> List[Pair]:
    match_start = time.perf_counter()
    feature_cache = FeatureCache()
    candidate_comparisons = 0
    threshold = max(0.50, float(threshold or 0.0))
    backend_requested, backend_used, backend_note = resolve_backend(backend)
    ai_status = ai_matcher.status() if backend_requested == "ai" else {}
    progress.update(
        backend_requested=backend_requested,
        backend_used=backend_used,
        backend_note=backend_note,
        ai_available=bool(ai_status.get("implemented") and ai_status.get("model_available")),
        ai_device=ai_matcher.device_name() if backend_requested == "ai" else None,
        ai_model_loaded=bool(ai_status.get("model_loaded")),
    )

    orb = cv2.ORB_create(nfeatures=NFEATURES)
    befores, be_ph, be_kp, be_desc, be_gray, be_errors = load_feature_set(
        befores, orb, feature_cache, should_cancel, cancelled_exception
    )
    afters, af_ph, af_kp, af_desc, af_gray, af_errors = load_feature_set(
        afters, orb, feature_cache, should_cancel, cancelled_exception
    )
    image_errors = be_errors + af_errors

    progress.update(
        state="matching",
        done=0,
        matched=0,
        unmatched=0,
        unmatched_debug=[],
        match_debug=[],
        matching_time_sec=0.0,
        avg_time_per_image_sec=0.0,
        images_per_sec=0.0,
        candidate_comparisons=0,
        cache_hits=0,
        feature_cache_hits=feature_cache.cache_hits,
        feature_cache_misses=feature_cache.cache_misses,
        feature_cache_path=str(feature_cache.path),
        image_errors=image_errors,
        skipped_images=len(image_errors),
        average_confidence=0.0,
        lowest_confidence=0.0,
        highest_confidence=0.0,
        assignment_method="greedy",
        greedy_accepted=0,
        hungarian_accepted=0,
    )

    total_images = len(befores) + len(afters)
    assignment_mode = MATCH_ASSIGNMENT if MATCH_ASSIGNMENT in VALID_ASSIGNMENTS else "greedy"
    if assignment_mode == "hungarian" and get_linear_sum_assignment() is not None:
        try:
            reset_assignment_progress(progress, "hungarian")
            pairs, candidate_comparisons = run_hungarian_assignment(
                befores,
                afters,
                be_ph,
                af_ph,
                be_kp,
                be_desc,
                af_kp,
                af_desc,
                be_gray,
                af_gray,
                threshold,
                progress,
                should_cancel,
                cancelled_exception,
                match_start,
                total_images,
            )
            if pairs is None:
                reset_assignment_progress(progress, "greedy_fallback")
                pairs, candidate_comparisons = apply_greedy_assignment(
                    befores,
                    afters,
                    be_ph,
                    af_ph,
                    be_kp,
                    be_desc,
                    af_kp,
                    af_desc,
                    be_gray,
                    af_gray,
                    threshold,
                    progress,
                    should_cancel,
                    cancelled_exception,
                    match_start,
                    total_images,
                )
            progress["hungarian_accepted"] = len(pairs)
            progress["greedy_accepted"] = 0
        except Exception as exc:
            if isinstance(exc, cancelled_exception):
                raise
            reset_assignment_progress(progress, "greedy_fallback")
            pairs, candidate_comparisons = apply_greedy_assignment(
                befores,
                afters,
                be_ph,
                af_ph,
                be_kp,
                be_desc,
                af_kp,
                af_desc,
                be_gray,
                af_gray,
                threshold,
                progress,
                should_cancel,
                cancelled_exception,
                match_start,
                total_images,
            )
            progress["greedy_accepted"] = len(pairs)
            progress["hungarian_accepted"] = 0
    elif assignment_mode == "auto" and get_linear_sum_assignment() is not None:
        reset_assignment_progress(progress, "greedy")
        greedy_pairs, greedy_comparisons = apply_greedy_assignment(
            befores,
            afters,
            be_ph,
            af_ph,
            be_kp,
            be_desc,
            af_kp,
            af_desc,
            be_gray,
            af_gray,
            threshold,
            progress,
            should_cancel,
            cancelled_exception,
            match_start,
            total_images,
        )
        greedy_snapshot = assignment_snapshot(progress)
        greedy_snapshot["greedy_accepted"] = len(greedy_pairs)

        try:
            reset_assignment_progress(progress, "hungarian")
            hungarian_pairs, hungarian_comparisons = run_hungarian_assignment(
                befores,
                afters,
                be_ph,
                af_ph,
                be_kp,
                be_desc,
                af_kp,
                af_desc,
                be_gray,
                af_gray,
                threshold,
                progress,
                should_cancel,
                cancelled_exception,
                match_start,
                total_images,
            )
            hungarian_count = len(hungarian_pairs or [])
            if hungarian_pairs is not None and hungarian_count > len(greedy_pairs):
                pairs = hungarian_pairs
                candidate_comparisons = greedy_comparisons + hungarian_comparisons
                progress["assignment_method"] = "hungarian"
                progress["greedy_accepted"] = len(greedy_pairs)
                progress["hungarian_accepted"] = hungarian_count
            else:
                pairs = greedy_pairs
                candidate_comparisons = greedy_comparisons + (hungarian_comparisons if hungarian_pairs is not None else 0)
                restore_assignment_snapshot(progress, greedy_snapshot)
                progress["assignment_method"] = "greedy"
                progress["greedy_accepted"] = len(greedy_pairs)
                progress["hungarian_accepted"] = hungarian_count
        except Exception as exc:
            if isinstance(exc, cancelled_exception):
                raise
            pairs = greedy_pairs
            candidate_comparisons = greedy_comparisons
            restore_assignment_snapshot(progress, greedy_snapshot)
            progress["assignment_method"] = "greedy"
            progress["greedy_accepted"] = len(greedy_pairs)
            progress["hungarian_accepted"] = 0
    else:
        reset_assignment_progress(progress, "greedy")
        pairs, candidate_comparisons = apply_greedy_assignment(
            befores,
            afters,
            be_ph,
            af_ph,
            be_kp,
            be_desc,
            af_kp,
            af_desc,
            be_gray,
            af_gray,
            threshold,
            progress,
            should_cancel,
            cancelled_exception,
            match_start,
            total_images,
        )
        progress["greedy_accepted"] = len(pairs)
        progress["hungarian_accepted"] = 0

    update_matching_metrics(progress, match_start, total_images, candidate_comparisons)
    progress["cache_hits"] = feature_cache.cache_hits
    progress["feature_cache_hits"] = feature_cache.cache_hits
    progress["feature_cache_misses"] = feature_cache.cache_misses
    progress["feature_cache_path"] = str(feature_cache.path)
    progress["image_errors"] = image_errors
    progress["skipped_images"] = len(image_errors)
    feature_cache.save()

    return pairs
