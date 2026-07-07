from pathlib import Path
from typing import Dict
import math

import cv2
import numpy as np
from PIL import Image

from .cache import VisionCache


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def blur_score_from_variance(variance: float) -> float:
    low_variance = 500.0
    high_variance = 9000.0
    low_score = 35.0
    high_score = 98.0
    safe_variance = max(float(variance), 1.0)
    score = low_score + (
        (math.log(safe_variance) - math.log(low_variance))
        / (math.log(high_variance) - math.log(low_variance))
    ) * (high_score - low_score)
    return clamp(score)


def quality_rating(overall_score: float) -> str:
    if overall_score >= 85:
        return "Excellent"
    if overall_score >= 70:
        return "Good"
    if overall_score >= 50:
        return "Fair"
    return "Poor"


def score_image(path: Path, cache: VisionCache) -> Dict:
    cached = cache.get(path, "quality")
    if cached and "quality_rating" in cached:
        return cached

    with Image.open(path) as image:
        width, height = image.size
        rgb = image.convert("RGB")

    arr = np.array(rgb)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blur_score = blur_score_from_variance(blur_variance)
    brightness = float(gray.mean())
    brightness_score = clamp(100.0 - abs(brightness - 128.0) / 128.0 * 100.0)
    dark_ratio = float((gray < 35).mean())
    bright_ratio = float((gray > 220).mean())
    exposure_score = clamp(100.0 - ((dark_ratio + bright_ratio) * 100.0))
    megapixels = (width * height) / 1_000_000.0
    resolution_score = clamp((megapixels / 2.0) * 100.0)
    overall = clamp(blur_score * 0.35 + brightness_score * 0.25 + exposure_score * 0.25 + resolution_score * 0.15)

    result = {
        "file": str(path),
        "width": width,
        "height": height,
        "blur_variance": round(blur_variance, 2),
        "blur_score": round(blur_score, 2),
        "brightness": round(brightness, 2),
        "brightness_score": round(brightness_score, 2),
        "exposure_score": round(exposure_score, 2),
        "resolution_score": round(resolution_score, 2),
        "overall_score": round(overall, 2),
        "quality_rating": quality_rating(overall),
        "is_blurry": blur_score < 35,
        "is_dark": brightness < 60,
        "is_overexposed": brightness > 200,
    }
    cache.set(path, "quality", result)
    return result
