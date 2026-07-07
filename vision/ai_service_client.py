import json
import os
import urllib.error
import urllib.request


DEFAULT_RESULT = {
    "confidence": 0.0,
    "matches": 0,
    "keypoints_before": 0,
    "keypoints_after": 0,
    "processing_time_ms": 0.0,
}


def service_url():
    return os.getenv("AI_SERVICE_URL", "").rstrip("/")


def map_path(path):
    value = str(path)
    mapping = os.getenv("AI_PATH_MAP", "/data/photos=/app/data/photos")
    source, _, target = mapping.partition("=")
    if source and target and value.startswith(source):
        return target + value[len(source):]
    return value


def _error_result(message):
    return {**DEFAULT_RESULT, "error": message}


def _read_error_body(exc):
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    if not body:
        return ""
    try:
        data = json.loads(body)
        return data.get("error") or body
    except json.JSONDecodeError:
        return body


def health():
    base = service_url()
    if not base:
        return {"ok": False, "error": "AI service unavailable"}

    timeout = float(os.getenv("AI_SERVICE_HEALTH_TIMEOUT", "5"))
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _read_error_body(exc)
        message = f"AI service HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail}"
        return {"ok": False, "error": message}
    except Exception as exc:
        return {"ok": False, "error": f"AI service unavailable: {exc}"}


def match_pair(before_path, after_path):
    base = service_url()
    if not base:
        return _error_result("AI service unavailable")

    payload = json.dumps(
        {
            "before_path": map_path(before_path),
            "after_path": map_path(after_path),
        }
    ).encode("utf-8")
    timeout = float(os.getenv("AI_SERVICE_TIMEOUT", "120"))
    request = urllib.request.Request(
        f"{base}/match-pair",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _read_error_body(exc)
        message = f"AI service HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail}"
        return _error_result(message)
    except Exception as exc:
        return _error_result(f"AI service unavailable: {exc}")
