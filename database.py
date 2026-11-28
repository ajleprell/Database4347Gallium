import os
import sqlite3

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")

def connect(db_path: str = DB_FILE) -> sqlite3.Connection:
    """Connect to the SQLite library database with foreign key support enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
