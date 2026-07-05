from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]

if os.name == "nt" and not os.getenv("IN_DOCKER"):
    DEFAULT_DATA = Path(os.getenv("LOCALAPPDATA", Path.home())) / "fieldreport"
else:
    DEFAULT_DATA = Path("/data")

DATA = Path(os.getenv("FIELDREPORT_DATA_ROOT", os.getenv("DATA_ROOT", str(DEFAULT_DATA)))).expanduser().resolve()
PHOTOS = Path(os.getenv("SAVE_ROOT", str(DATA / "photos"))).expanduser().resolve()
REPORTS = Path(os.getenv("REPORTS_ROOT", str(DATA / "reports"))).expanduser().resolve()
DB = Path(os.getenv("FIELDREPORT_DB", str(DATA / "database" / "fieldreport.db"))).expanduser().resolve()
