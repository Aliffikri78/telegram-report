import sqlite3
from app.core.config import DB
DB.parent.mkdir(parents=True,exist_ok=True)
conn=sqlite3.connect(DB)
