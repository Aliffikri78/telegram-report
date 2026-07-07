#!/usr/bin/env python3
from flask import Flask, jsonify, request

try:
    from vision import ai_engine
except ImportError:
    from app.vision import ai_engine


app = Flask(__name__)


def error_result(message):
    return {
        "confidence": 0.0,
        "matches": 0,
        "keypoints_before": 0,
        "keypoints_after": 0,
        "processing_time_ms": 0.0,
        "error": message,
    }


@app.get("/health")
def health():
    return jsonify(ok=True, ai=ai_engine.status())


@app.post("/match-pair")
def match_pair():
    data = request.get_json(silent=True) or {}
    before_path = data.get("before_path")
    after_path = data.get("after_path")

    if not before_path or not after_path:
        return jsonify(error_result("before_path and after_path are required")), 400

    return jsonify(ai_engine.match(before_path, after_path))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False, threaded=True)
