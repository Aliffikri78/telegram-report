PAIR_MATCH_IMPLEMENTED = True
REPORT_MATCH_IMPLEMENTED = False

try:
    from vision import ai_service_client
except ImportError:
    from app.vision import ai_service_client


def status():
    configured = bool(ai_service_client.service_url())
    return {
        "torch_available": False,
        "torch_version": None,
        "cuda_version": None,
        "cuda_available": False,
        "gpu_detected": False,
        "gpu_name": None,
        "total_gpu_memory_mb": None,
        "current_device": None,
        "model_available": configured,
        "model_loaded": False,
        "model_error": None if configured else "AI service unavailable",
        "implemented": REPORT_MATCH_IMPLEMENTED,
        "pair_match_implemented": PAIR_MATCH_IMPLEMENTED,
        "report_match_implemented": REPORT_MATCH_IMPLEMENTED,
        "service_url": ai_service_client.service_url() or None,
    }


def is_available():
    return False


def device_name():
    return "AI service" if ai_service_client.service_url() else "cpu"


def load_model():
    return None


def health():
    return ai_service_client.health()


def match(before_image, after_image):
    return ai_service_client.match_pair(before_image, after_image)
