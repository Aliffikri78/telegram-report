import time
from pathlib import Path


_extractor = None
_matcher = None
_device = None
_load_error = None


def _imports():
    import torch
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import load_image, rbd

    return torch, LightGlue, SuperPoint, load_image, rbd


def status():
    try:
        torch, _, _, _, _ = _imports()
        cuda_available = bool(torch.cuda.is_available())
        return {
            "available": cuda_available,
            "loaded": _extractor is not None and _matcher is not None,
            "device": torch.cuda.get_device_name(0) if cuda_available else "cpu",
            "error": _load_error,
        }
    except Exception as exc:
        return {
            "available": False,
            "loaded": False,
            "device": "cpu",
            "error": str(exc),
        }


def load_model():
    global _extractor, _matcher, _device, _load_error
    if _extractor is not None and _matcher is not None:
        return _extractor, _matcher, _device

    try:
        torch, LightGlue, SuperPoint, _, _ = _imports()
        _device = "cuda" if torch.cuda.is_available() else "cpu"
        _extractor = SuperPoint(max_num_keypoints=2048).eval().to(_device)
        _matcher = LightGlue(features="superpoint").eval().to(_device)
        _load_error = None
        return _extractor, _matcher, _device
    except Exception as exc:
        _extractor = None
        _matcher = None
        _device = None
        _load_error = str(exc)
        raise


def match(before_image, after_image):
    start = time.perf_counter()
    torch, _, _, load_image, rbd = _imports()
    extractor, matcher, device = load_model()

    try:
        with torch.inference_mode():
            image0 = load_image(str(Path(before_image))).to(device)
            image1 = load_image(str(Path(after_image))).to(device)
            feats0 = extractor.extract(image0)
            feats1 = extractor.extract(image1)
            matches01 = matcher({"image0": feats0, "image1": feats1})
            feats0, feats1, matches01 = [rbd(x) for x in [feats0, feats1, matches01]]
            matches = matches01["matches"]
            scores = matches01.get("scores")
            match_count = int(matches.shape[0])
            confidence = float(scores.mean().item()) if scores is not None and match_count else 0.0
            return {
                "confidence": confidence,
                "matches": match_count,
                "keypoints_before": int(feats0["keypoints"].shape[0]),
                "keypoints_after": int(feats1["keypoints"].shape[0]),
                "processing_time_ms": round((time.perf_counter() - start) * 1000, 2),
            }
    except Exception:
        raise
