try:
    from vision import ai_engine
except ImportError:
    from app.vision import ai_engine


IMPLEMENTED = False
PAIR_MATCH_IMPLEMENTED = True
REPORT_MATCH_IMPLEMENTED = False


def availability():
    info = ai_engine.status()
    if not info["torch_available"]:
        return False, "torch is not installed"
    if not info["cuda_available"]:
        return False, "CUDA is not available"
    if not info["model_available"]:
        return False, "AI matcher model not available"
    if not info["report_match_implemented"]:
        return False, "AI report matcher not implemented yet"
    return True, "AI matcher available"


def status():
    return ai_engine.status()


def device_name():
    return ai_engine.device_name()


def load_model():
    return ai_engine.load_model()


def match_pairs_ai(*args, **kwargs):
    return ai_engine.match(*args, **kwargs)
