import json
import os
import sqlite3
import threading
import time

from werkzeug.security import generate_password_hash

from ..config import DB_PATH, DEFAULT_USER_STORAGE_LIMIT

db_lock = threading.Lock()
_conn = None


def get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=30000")
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db():
    with db_lock:
        conn = get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                uuid TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                local_path TEXT NOT NULL,
                upload_time REAL NOT NULL,
                owner_id INTEGER,
                downloads INTEGER NOT NULL DEFAULT 0,
                password_hash TEXT,
                metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_uuid TEXT NOT NULL,
                timestamp REAL NOT NULL,
                ip TEXT,
                user_agent TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_banned INTEGER DEFAULT 0
            )
        """)
        
        try:
            conn.execute("ALTER TABLE users ADD COLUMN storage_limit_bytes INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        for col in ["username", "first_name", "last_name"]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass

        conn.commit()


def db_add_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute(
            "UPDATE users SET username = ?, first_name = ?, last_name = ? WHERE user_id = ?",
            (username, first_name, last_name, user_id)
        )
        conn.commit()


def db_get_user(user_id: int) -> dict:
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def db_search_users(query: str) -> list[dict]:
    with db_lock:
        conn = get_conn()
        like_query = f"%{query}%"
        rows = conn.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? OR last_name LIKE ?",
            (like_query, like_query, like_query)
        ).fetchall()
        return [dict(r) for r in rows]


def db_is_banned(user_id: int) -> bool:
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row["is_banned"]) if row else False


def db_user_exists(user_id: int) -> bool:
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None



def db_set_ban_status(user_id: int, ban: bool):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        conn.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if ban else 0, user_id)
        )
        conn.commit()



def db_get_all_users():
    with db_lock:
        conn = get_conn()
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [r["user_id"] for r in rows]


def db_add_file(file_uuid: str, original_name: str, local_path: str, owner_id: int, metadata: dict = None):
    meta_str = json.dumps(metadata) if metadata else None
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO files (uuid, original_name, local_path, upload_time, owner_id, downloads, metadata) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (file_uuid, original_name, local_path, time.time(), owner_id, meta_str)
        )
        conn.commit()


def db_get_file(file_uuid: str):
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM files WHERE uuid = ?", (file_uuid,)).fetchone()
        return dict(row) if row else None


def db_get_files_by_owner(owner_id: int):
    with db_lock:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM files WHERE owner_id = ? ORDER BY upload_time DESC", (
                owner_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def db_get_all_files():
    with db_lock:
        conn = get_conn()
        rows = conn.execute("SELECT * FROM files").fetchall()
        return [dict(r) for r in rows]


def db_increment_downloads(file_uuid: str):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE files SET downloads = downloads + 1 WHERE uuid = ?", (file_uuid,))
        conn.commit()


def db_set_password(file_uuid: str, password: str):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE files SET password_hash = ? WHERE uuid = ?",
            (generate_password_hash(password) if password else None, file_uuid)
        )
        conn.commit()


def db_delete_file(file_uuid: str):
    with db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM files WHERE uuid = ?", (file_uuid,))
        conn.execute("DELETE FROM analytics WHERE file_uuid = ?", (file_uuid,))
        conn.commit()


def db_get_path_reference_count(local_path: str) -> int:
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE local_path = ?", (local_path,)).fetchone()
        return row["cnt"] if row else 0


def db_log_download(file_uuid: str, ip: str, user_agent: str):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO analytics (file_uuid, timestamp, ip, user_agent) VALUES (?, ?, ?, ?)",
            (file_uuid, time.time(), ip, user_agent)
        )
        conn.commit()


def db_get_analytics(file_uuid: str, limit: int = 50):
    with db_lock:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM analytics WHERE file_uuid = ? ORDER BY timestamp DESC LIMIT ?",
            (file_uuid, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def generate_short_code() -> str:
    import random
    import string
    while True:
        code = "".join(random.choices(string.ascii_letters, k=3))
        if not db_get_file(code):
            return code


def db_get_user_stats(owner_id: int, retention_sec: float) -> dict:
    with db_lock:
        conn = get_conn()
        now = time.time()
        cutoff = now - retention_sec

        row_total = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE owner_id = ?", (owner_id,)).fetchone()
        total_links = row_total["cnt"] if row_total else 0

        row_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE owner_id = ? AND upload_time >= ?", (owner_id, cutoff)).fetchone()
        active_links = row_active["cnt"] if row_active else 0

        row_downloads = conn.execute(
            "SELECT SUM(downloads) as dl FROM files WHERE owner_id = ?", (owner_id,)).fetchone()
        total_downloads = row_downloads["dl"] if row_downloads["dl"] is not None else 0

        row_ips = conn.execute(
            "SELECT COUNT(DISTINCT ip) as unique_ips FROM analytics "
            "WHERE file_uuid IN (SELECT uuid FROM files WHERE owner_id = ?)", (owner_id,)
        ).fetchone()
        unique_users = row_ips["unique_ips"] if row_ips else 0

        rows_paths = conn.execute(
            "SELECT local_path FROM files WHERE owner_id = ? AND upload_time >= ?", (owner_id, cutoff)).fetchall()
        active_paths = [r["local_path"] for r in rows_paths]

        return {
            "total_links": total_links,
            "active_links": active_links,
            "total_downloads": total_downloads,
            "unique_users": unique_users,
            "active_paths": active_paths
        }


def db_set_user_quota(user_id: int, limit_bytes: int):
    with db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        conn.execute(
            "UPDATE users SET storage_limit_bytes = ? WHERE user_id = ?",
            (limit_bytes, user_id)
        )
        conn.commit()


def db_get_user_quota(user_id: int) -> int:
    with db_lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT storage_limit_bytes FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return DEFAULT_USER_STORAGE_LIMIT
    val = row["storage_limit_bytes"]
    if val is None or val == 0:
        return DEFAULT_USER_STORAGE_LIMIT
    if val == -1:
        return -1
    return val



def db_get_user_active_storage(user_id: int, retention_sec: float) -> int:
    cutoff = time.time() - retention_sec
    with db_lock:
        conn = get_conn()
        rows = conn.execute(
            "SELECT DISTINCT local_path FROM files "
            "WHERE owner_id = ? AND upload_time >= ?",
            (user_id, cutoff)
        ).fetchall()
    total = 0
    for r in rows:
        path = r["local_path"]
        if os.path.exists(path):
            total += os.path.getsize(path)
    return total

