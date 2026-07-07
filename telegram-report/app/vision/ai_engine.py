_torch = None
_model = None
_model_error = None
PAIR_MATCH_IMPLEMENTED = True
REPORT_MATCH_IMPLEMENTED = False

try:
    from vision import lightglue_matcher
except ImportError:
    from app.vision import lightglue_matcher


def _import_torch():
    global _torch
    if _torch is False:
        return None
    if _torch is not None:
        return _torch
    try:
        import torch
    except Exception:
        _torch = False
        return None
    _torch = torch
    return _torch


def status():
    torch = _import_torch()
    torch_available = torch is not None
    cuda_available = False
    gpu_name = None
    torch_version = None
    cuda_version = None
    total_gpu_memory_mb = None
    current_device = None
    if torch_available:
        try:
            torch_version = torch.__version__
            cuda_version = torch.version.cuda
            cuda_available = bool(torch.cuda.is_available())
            if cuda_available:
                current_device = int(torch.cuda.current_device())
                gpu_name = torch.cuda.get_device_name(current_device)
                props = torch.cuda.get_device_properties(current_device)
                total_gpu_memory_mb = int(props.total_memory / (1024 * 1024))
        except Exception:
            cuda_available = False
            gpu_name = None
    lightglue_status = lightglue_matcher.status()
    return {
        "torch_available": torch_available,
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "cuda_available": cuda_available,
        "gpu_detected": bool(gpu_name),
        "gpu_name": gpu_name,
        "total_gpu_memory_mb": total_gpu_memory_mb,
        "current_device": current_device,
        "model_available": bool(lightglue_status.get("available")),
        "model_loaded": bool(lightglue_status.get("loaded")),
        "model_error": lightglue_status.get("error") or _model_error,
        "implemented": REPORT_MATCH_IMPLEMENTED,
        "pair_match_implemented": PAIR_MATCH_IMPLEMENTED,
        "report_match_implemented": REPORT_MATCH_IMPLEMENTED,
    }


def is_available():
    info = status()
    return bool(
        info["torch_available"]
        and info["cuda_available"]
        and info["model_available"]
        and info["report_match_implemented"]
    )


def device_name():
    info = status()
    return info["gpu_name"] or "cpu"


def load_model():
    global _model, _model_error
    try:
        _model = lightglue_matcher.load_model()
        _model_error = None
        return _model
    except Exception as exc:
        _model = None
        _model_error = str(exc)
        return None


def match(before_image, after_image):
    try:
        return lightglue_matcher.match(before_image, after_image)
    except Exception as exc:
        return {
            "confidence": 0.0,
            "matches": 0,
            "keypoints_before": 0,
            "keypoints_after": 0,
            "processing_time_ms": 0.0,
            "error": str(exc),
        }
