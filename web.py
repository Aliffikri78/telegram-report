#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import sys


CANONICAL_WEB = Path(__file__).resolve().parent / "app" / "web.py"

spec = importlib.util.spec_from_file_location(
    "_telegram_report_canonical_web",
    CANONICAL_WEB,
)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load canonical web module: {CANONICAL_WEB}")

canonical_web = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = canonical_web
spec.loader.exec_module(canonical_web)

# Re-export public names for compatibility with any local imports from web.py.
for name, value in vars(canonical_web).items():
    if not name.startswith("_"):
        globals()[name] = value

app = canonical_web.app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
