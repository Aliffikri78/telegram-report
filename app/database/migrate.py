from app.database.db import conn
c=conn.cursor();c.execute("CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,name TEXT,status TEXT)");conn.commit()
