import os
import time
import uuid
import sqlite3
import threading
import json
import hashlib
import mimetypes
import zipfile
import asyncio
from io import BytesIO
from urllib.parse import urlparse
from functools import wraps

import aiohttp
import qrcode
from PIL import Image
from flask import Flask, send_file, abort, request, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pyrogram.errors import MessageNotModified

API_ID = os.environ.get("API_ID", "YOUR_API_ID")
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

BASE_URL = os.environ.get("BASE_URL", "https://your.base.url.com")

ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "YOUR_ADMIN_ID").split(",") if x.strip()]

DATA_DIR = "data"
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
DB_PATH = os.path.join(DATA_DIR, "database.db")
SESSION_NAME = os.path.join(DATA_DIR, "file_to_link_bot")
PROCESS_MEDIA_TIME = float(os.environ.get("PROCESS_MEDIA_TIME", 3.0))
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", 24))
RETENTION_SEC = RETENTION_HOURS * 3600

db_lock = threading.Lock()
flask_app = Flask(__name__)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

ACTIVE_DOWNLOADS = {}
PENDING_PASSWORD = {}
USER_BATCHES = {}
PENDING_URL_CHOICE = {}

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
            
        conn.commit()

def db_add_user(user_id: int):
    with db_lock:
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()

def db_is_banned(user_id: int) -> bool:
    with db_lock:
        conn = get_conn()
        row = conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row["is_banned"]) if row else False

def db_set_ban_status(user_id: int, ban: bool):
    with db_lock:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO users (user_id, is_banned) VALUES (?, ?)", (user_id, 1 if ban else 0))
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
        row = conn.execute("SELECT * FROM files WHERE uuid = ?", (file_uuid,)).fetchone()
        return dict(row) if row else None

def db_get_files_by_owner(owner_id: int):
    with db_lock:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM files WHERE owner_id = ? ORDER BY upload_time DESC", (owner_id,)
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
        conn.execute("UPDATE files SET downloads = downloads + 1 WHERE uuid = ?", (file_uuid,))
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

def db_get_path_reference_count(local_path: str) -> int:
    with db_lock:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE local_path = ?", (local_path,)).fetchone()
        return row["cnt"] if row else 0

def admin_only(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        if message.from_user.id not in ADMIN_IDS:
            await message.reply_text("⚠️ **Access Denied:** Administrator authorization required.")
            return
        return await func(client, message, *args, **kwargs)
    return wrapper

@bot.on_message(group=-2)
async def check_ban_and_register(client, message: Message):
    if message.from_user:
        db_add_user(message.from_user.id)
        if db_is_banned(message.from_user.id):
            await message.reply_text("🚫 Your access to this bot has been restricted by an administrator.")
            message.stop_propagation()

def make_progress_bar(current: int, total: int, length: int = 12) -> str:
    if total == 0:
        return "[------------] 0%"
    filled = int(length * current / total)
    bar = "█" * filled + "░" * (length - filled)
    pct = current * 100 // total
    return f"[{bar}] {pct}%"

def human_size(size_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

def get_media(message: Message):
    for attr in ("document", "video", "audio", "voice", "photo", "animation", "video_note", "sticker"):
        media = getattr(message, attr, None)
        if media:
            return attr, media
    return None, None

def cleanup_file(filepath: str):
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"[Cleanup Error] Failed to delete {filepath}: {e}")

def make_qr_bytes(data: str) -> BytesIO:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    buf.name = "qrcode.png"
    img.save(buf, "PNG")
    buf.seek(0)
    return buf

def build_result_keyboard(file_uuid: str, is_protected: bool) -> InlineKeyboardMarkup:
    if is_protected:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 Remove Password Protection", callback_data=f"rmpwd_{file_uuid}")],
            [InlineKeyboardButton("📊 View Stats Data", url=f"{BASE_URL}/stats/{file_uuid}")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Set Security Password", callback_data=f"setpwd_{file_uuid}")],
            [InlineKeyboardButton("📊 View Stats Data", url=f"{BASE_URL}/stats/{file_uuid}")]
        ])

def extract_file_metadata(local_path: str, incoming_duration=None) -> dict:
    meta = {
        "mime_type": "application/octet-stream",
        "md5": "",
        "sha256": "",
        "dimensions": None,
        "duration": incoming_duration
    }
    if not os.path.exists(local_path):
        return meta

    mime, _ = mimetypes.guess_type(local_path)
    if mime:
        meta["mime_type"] = mime

    md5_hash = hashlib.md5()
    sha256_hash = hashlib.sha256()
    try:
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                md5_hash.update(chunk)
                sha256_hash.update(chunk)
        meta["md5"] = md5_hash.hexdigest()
        meta["sha256"] = sha256_hash.hexdigest()
    except Exception as e:
        print(f"[Meta Hash Error] {e}")

    if meta["mime_type"].startswith("image/"):
        try:
            with Image.open(local_path) as img:
                meta["dimensions"] = f"{img.width}x{img.height}"
        except Exception:
            pass

    return meta

async def finalize_upload(status_msg: Message, original_name: str, local_path: str, owner_id: int, incoming_duration=None):
    file_uuid = str(uuid.uuid4())
    metadata = extract_file_metadata(local_path, incoming_duration)
    db_add_file(file_uuid, original_name, local_path, owner_id, metadata)

    download_link = f"{BASE_URL}/download/{file_uuid}"
    success_text = (
        f"✅ **File Successfully Uploaded!**\n\n"
        f"📦 **Name:** `{original_name}`\n"
        f"🔗 **Link:** {download_link}\n\n"
        f"⏳ This link will automatically expire in {RETENTION_HOURS} hours."
    )
    keyboard = build_result_keyboard(file_uuid, is_protected=False)

    try:
        qr_buf = make_qr_bytes(download_link)
        await status_msg.delete()
        await bot.send_photo(chat_id=status_msg.chat.id, photo=qr_buf, caption=success_text, reply_markup=keyboard)
    except Exception as e:
        print(f"[QR Error] Fallback to text: {e}")
        try:
            await status_msg.edit_text(success_text, disable_web_page_preview=True, reply_markup=keyboard)
        except Exception:
            await bot.send_message(chat_id=status_msg.chat.id, text=success_text, disable_web_page_preview=True, reply_markup=keyboard)
    return file_uuid

def cleanup_expired_files():
    while True:
        time.sleep(3600)
        print("[Cleanup] Running reference check on expiration routines...")
        current_time = time.time()
        files = db_get_all_files()
        expired = [f for f in files if current_time - f["upload_time"] > RETENTION_SEC]

        for f in expired:
            db_delete_file(f["uuid"])
            if db_get_path_reference_count(f["local_path"]) == 0:
                cleanup_file(f["local_path"])

        if expired:
            print(f"[Cleanup] Cleared {len(expired)} database file references.")

PASSWORD_FORM = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Password required</title>
  <style>
    body { font-family: sans-serif; display: flex; align-items: center; justify-content: center;
           height: 100vh; margin: 0; background: #f4f4f7; color: #1f2937; transition: background 0.3s, color 0.3s; }
    .box { background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); text-align: center; }
    input { padding: 0.6rem; border: 1px solid #ccc; border-radius: 6px; width: 220px; margin-top: 1rem; background: inherit; color: inherit; }
    button { padding: 0.6rem 1.2rem; margin-left: 0.5rem; border: none; border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }
    .error { color: #dc2626; margin-top: 0.75rem; }
    @media (prefers-color-scheme: dark) {
      body { background: #1a1a2e; color: #e0e0e0; }
      .box { background: #16213e; box-shadow: 0 2px 12px rgba(0,0,0,0.3); }
      input { border-color: #4b5563; }
    }
  </style>
</head>
<body>
  <div class="box">
    <h2>🔒 This file is password protected</h2>
    <form method="post">
      <input type="password" name="password" placeholder="Enter password" autofocus>
      <button type="submit">Unlock</button>
    </form>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>
</body>
</html>
"""

STATS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stats — {{ file.original_name }}</title>
  <style>
    body { font-family: sans-serif; background: #f4f4f7; color: #1f2937; margin: 0; padding: 2rem; transition: background 0.3s, color 0.3s; }
    .card { background: #fff; padding: 1.5rem; border-radius: 12px; max-width: 700px; margin: 0 auto 1.5rem; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    h2, h3 { margin-top: 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
    th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #eee; font-size: 0.9rem; word-break: break-all; }
    .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.8rem; }
    .locked { background: #fef3c7; color: #92400e; }
    .public { background: #dcfce7; color: #166534; }
    ul { padding-left: 1.2rem; margin: 0.5rem 0; }
    li { font-size: 0.9rem; margin-bottom: 0.25rem; }
    @media (prefers-color-scheme: dark) {
      body { background: #1a1a2e; color: #e0e0e0; }
      .card { background: #16213e; box-shadow: 0 2px 12px rgba(0,0,0,0.3); }
      th, td { border-bottom-color: #2e3440; }
    }
  </style>
</head>
<body>
  <div class="card">
    <h2>📄 {{ file.original_name }}</h2>
    <span class="badge {{ 'locked' if file.password_hash else 'public' }}">
      {{ '🔒 Protected' if file.password_hash else '🔓 Public' }}
    </span>
    <p>⬇️ <strong>{{ file.downloads }}</strong> total downloads</p>
  </div>
  
  <div class="card">
    <h3>📊 File Technical Metadata</h3>
    <ul>
      <li><strong>MIME Type:</strong> {{ meta.mime_type }}</li>
      {% if meta.md5 %}<li><strong>MD5 Hash:</strong> <code>{{ meta.md5 }}</code></li>{% endif %}
      {% if meta.sha256 %}<li><strong>SHA256 Hash:</strong> <code>{{ meta.sha256 }}</code></li>{% endif %}
      {% if meta.dimensions %}<li><strong>Dimensions:</strong> {{ meta.dimensions }}</li>{% endif %}
      {% if meta.duration %}<li><strong>Duration:</strong> {{ meta.duration }} seconds</li>{% endif %}
    </ul>
  </div>

  <div class="card">
    <h3>Recent download activity</h3>
    {% if logs %}
    <table>
      <tr><th>Time</th><th>IP</th><th>User Agent</th></tr>
      {% for log in logs %}
      <tr>
        <td>{{ log.readable_time }}</td>
        <td>{{ log.ip }}</td>
        <td>{{ log.user_agent[:60] }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p>No downloads recorded yet.</p>
    {% endif %}
  </div>
</body>
</html>
"""

@flask_app.route('/download/<file_uuid>', methods=['GET', 'POST'])
def download_file(file_uuid):
    file_info = db_get_file(file_uuid)
    if not file_info:
        abort(404, description="File not found or has expired.")

    if time.time() - file_info["upload_time"] > RETENTION_SEC:
        abort(410, description="This link has expired.")

    if not os.path.exists(file_info["local_path"]):
        abort(404, description="File missing from server.")

    if file_info["password_hash"]:
        if request.method == "POST":
            supplied = request.form.get("password", "")
            if not check_password_hash(file_info["password_hash"], supplied):
                return render_template_string(PASSWORD_FORM, error="Incorrect password."), 401
        else:
            return render_template_string(PASSWORD_FORM, error=None)

    db_log_download(
        file_uuid,
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent", "unknown")
    )
    db_increment_downloads(file_uuid)

    return send_file(
        file_info["local_path"],
        as_attachment=True,
        download_name=file_info.get("original_name", "downloaded_file"),
        conditional=True
    )

@flask_app.route('/stats/<file_uuid>')
def file_stats(file_uuid):
    file_info = db_get_file(file_uuid)
    if not file_info:
        abort(404, description="File not found or has expired.")

    meta = json.loads(file_info["metadata"]) if file_info["metadata"] else extract_file_metadata(file_info["local_path"])
    raw_logs = db_get_analytics(file_uuid, limit=50)
    logs = []
    for log in raw_logs:
        entry = dict(log)
        entry["readable_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log["timestamp"]))
        logs.append(entry)

    return render_template_string(STATS_TEMPLATE, file=file_info, logs=logs, meta=meta)

def run_flask():
    flask_app.run(host="0.0.0.0", port=5000, use_reloader=False)

async def download_progress(current: int, total: int, task_id: str, status_msg: Message, file_name: str, start_time: float):
    active = ACTIVE_DOWNLOADS.get(task_id)
    if not active or active.get("cancelled"):
        raise Exception("Cancelled by user")

    now = time.time()
    if now - active["last_update"] < 2.0 and current < total:
        return
    active["last_update"] = now

    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    bar = make_progress_bar(current, total)
    size_info = f"{human_size(current)} / {human_size(total)}" if total else human_size(current)

    text = f"⬇️ **Downloading...**\n\n📄 **File:** `{file_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])
    try:
        await status_msg.edit_text(text, reply_markup=keyboard)
    except MessageNotModified:
        pass

async def batch_download_progress(current: int, total: int, task_id: str, status_msg: Message, file_name: str, start_time: float, idx: int, total_items: int):
    active = ACTIVE_DOWNLOADS.get(task_id)
    if not active or active.get("cancelled"):
        raise Exception("Cancelled by user")

    now = time.time()
    if now - active["last_update"] < 2.0 and current < total:
        return
    active["last_update"] = now

    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    bar = make_progress_bar(current, total)
    size_info = f"{human_size(current)} / {human_size(total)}" if total else human_size(current)

    text = (
        f"📥 **Downloading item {idx} of {total_items}...**\n\n"
        f"📄 **Current File:** `{file_name}`\n"
        f"`{bar}`\n"
        f"📦 **Size:** {size_info}\n"
        f"🚀 **Speed:** {human_size(speed)}/s"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Batch", callback_data=f"cancel_{task_id}")]])
    try:
        await status_msg.edit_text(text, reply_markup=keyboard)
    except MessageNotModified:
        pass

async def upload_progress(current: int, total: int, task_id: str, status_msg: Message, file_name: str, start_time: float):
    active = ACTIVE_DOWNLOADS.get(task_id)
    if active and active.get("cancelled"):
        raise Exception("Cancelled by user")

    now = time.time()
    if active and now - active.get("last_update", 0.0) < 2.0 and current < total:
        return
    if active:
        active["last_update"] = now

    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    bar = make_progress_bar(current, total)
    size_info = f"{human_size(current)} / {human_size(total)}" if total else human_size(current)

    text = f"📤 **Uploading to Telegram...**\n\n📄 **File:** `{file_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
    try:
        await status_msg.edit_text(text)
    except MessageNotModified:
        pass

async def process_media_batch(user_id: int, chat_id: int):
    await asyncio.sleep(PROCESS_MEDIA_TIME)
    batch = USER_BATCHES.pop(user_id, None)
    if not batch or not batch["messages"]:
        return

    messages = batch["messages"]
    if len(messages) == 1:
        await handle_single_media_processing(messages[0])
        return

    status_msg = await bot.send_message(chat_id, f"📦 **Processing batch package of {len(messages)} items into zip...**")
    task_id = uuid.uuid4().hex[:10]
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}
    
    zip_filename = f"batch_{task_id}.zip"
    zip_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{zip_filename}")
    downloaded_paths = []

    try:
        for idx, msg in enumerate(messages, 1):
            m_type, media = get_media(msg)
            f_name = getattr(media, "file_name", None) or f"item_{idx}_{task_id}.bin"
            if m_type == "photo" and not f_name.endswith(".jpg"):
                f_name = f"photo_{idx}_{task_id}.jpg"

            ACTIVE_DOWNLOADS[task_id]["last_update"] = 0.0
            item_start_time = time.time()
            
            d_path = os.path.join(DOWNLOAD_DIR, f"part_{idx}_{uuid.uuid4().hex[:6]}_{f_name}")
            actual_path = await bot.download_media(
                msg, file_name=d_path, progress=batch_download_progress,
                progress_args=(task_id, status_msg, f_name, item_start_time, idx, len(messages))
            )
            
            if actual_path and os.path.exists(actual_path):
                downloaded_paths.append((actual_path, f_name))
            
            if ACTIVE_DOWNLOADS[task_id].get("cancelled"):
                raise Exception("Cancelled by user")

        await status_msg.edit_text("🤐 **Archiving batch contents into system Zip file...**")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for path, name in downloaded_paths:
                zipf.write(path, arcname=name)

        await finalize_upload(status_msg, zip_filename, zip_path, user_id)

    except Exception as e:
        cleanup_file(zip_path)
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text("🛑 Batch operation aborted by user request.")
        else:
            await status_msg.edit_text(f"❌ **Batch compression collapsed:**\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)
        for path, _ in downloaded_paths:
            cleanup_file(path)

async def handle_single_media_processing(message: Message):
    media_type, media = get_media(message)
    if not media:
        return

    task_id = uuid.uuid4().hex[:10]
    file_name = getattr(media, "file_name", None) or f"{media_type}_{task_id}.bin"
    if media_type == "photo" and not file_name.endswith(".jpg"):
        file_name = f"photo_{task_id}.jpg"
    elif media_type == "voice" and not file_name.endswith(".ogg"):
        file_name = f"voice_{task_id}.ogg"

    unique_filename = f"{task_id}_{file_name}"
    download_path = os.path.join(DOWNLOAD_DIR, unique_filename)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

    status_msg = await message.reply_text(f"⏳ **Preparing Download...**\n📄 `{file_name}`", reply_markup=keyboard)
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}

    try:
        file_path = await bot.download_media(
            message, file_name=download_path, progress=download_progress,
            progress_args=(task_id, status_msg, file_name, time.time())
        )
        if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
            raise Exception("Cancelled by user")

        duration = getattr(media, "duration", None)
        await finalize_upload(status_msg, file_name, file_path, message.from_user.id, duration)

    except Exception as e:
        cleanup_file(download_path)
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text(f"🛑 **Download Cancelled**\n📄 `{file_name}`")
        else:
            await status_msg.edit_text(f"❌ **Download Failed**\n📄 `{file_name}`\n\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)

@bot.on_message(filters.command("admin_stats") & filters.private)
@admin_only
async def admin_stats_cmd(client, message: Message):
    files = db_get_all_files()
    users = db_get_all_users()
    
    total_files = len(files)
    total_users = len(users)
    total_downloads = sum(f["downloads"] for f in files)
    
    unique_paths = set(f["local_path"] for f in files)
    total_bytes = 0
    for path in unique_paths:
        if os.path.exists(path):
            total_bytes += os.path.getsize(path)

    await message.reply_text(
        f"📊 **Global Instance Metrics Overview**\n\n"
        f"👥 **Total Tracked Users:** `{total_users}`\n"
        f"📂 **Active Database Link Rows:** `{total_files}`\n"
        f"💾 **Disk Space Consumed:** `{human_size(total_bytes)}`\n"
        f"📥 **Accumulated Downloads Served:** `{total_downloads}`"
    )

@bot.on_message(filters.command("admin_ban") & filters.private)
@admin_only
async def admin_ban_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_ban <user_id>`")
    
    try:
        target_id = int(parts[1].strip())
        db_set_ban_status(target_id, ban=True)
        await message.reply_text(f"🚫 User `{target_id}` has been successfully restricted.")
    except ValueError:
        await message.reply_text("❌ Missing or invalid Target User ID formatting validation.")

@bot.on_message(filters.command("admin_delete") & filters.private)
@admin_only
async def admin_delete_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_delete <uuid>`")
    
    target_uuid = parts[1].strip()
    file_info = db_get_file(target_uuid)
    if not file_info:
        return await message.reply_text("❌ Record identity match reference missing from structural scope.")
    
    db_delete_file(target_uuid)
    if db_get_path_reference_count(file_info["local_path"]) == 0:
        cleanup_file(file_info["local_path"])
        
    await message.reply_text(f"🗑️ Row link tracking metadata entry `{target_uuid}` forced offline.")

@bot.on_message(filters.command("admin_broadcast") & filters.private)
@admin_only
async def admin_broadcast_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_broadcast <text content>`")
    
    broadcast_text = parts[1].strip()
    users = db_get_all_users()
    
    status = await message.reply_text(f"📣 Initializing system global broadcast dispatching to `{len(users)}` metrics...")
    success_count = 0
    
    for uid in users:
        try:
            await client.send_message(chat_id=uid, text=f"📢 **System Broadcast Notification**\n\n{broadcast_text}")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
            
    await status.edit_text(f"✅ Dispatch complete. Reached `{success_count}/{len(users)}` destinations successfully.")

@bot.on_message(filters.command("clone") & filters.private)
async def clone_link_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/clone <uuid>`")
        
    old_uuid = parts[1].strip()
    file_info = db_get_file(old_uuid)
    if not file_info:
        return await message.reply_text("❌ Linked reference missing or expired.")
        
    new_uuid = str(uuid.uuid4())
    meta_dict = json.loads(file_info["metadata"]) if file_info["metadata"] else None
    
    db_add_file(new_uuid, file_info["original_name"], file_info["local_path"], message.from_user.id, meta_dict)
    await message.reply_text(f"✅ **Link Duplicated successfully!**\nNew Pointer:\n{BASE_URL}/download/{new_uuid}")

@bot.on_message(filters.command("removepwd") & filters.private)
async def remove_pwd_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/removepwd <uuid>`")
        
    target_uuid = parts[1].strip()
    file_info = db_get_file(target_uuid)
    if not file_info or file_info["owner_id"] != message.from_user.id:
        return await message.reply_text("⚠️ Action execution identity confirmation unauthorized.")
        
    db_set_password(target_uuid, password=None)
    await message.reply_text("🔓 **Password removed.** Link accessibility state shifted to global public.")

@bot.on_callback_query(filters.regex(r"^cancel_(.*)$"))
async def handle_cancellation(client, callback_query):
    task_id = callback_query.matches[0].group(1)
    if task_id in ACTIVE_DOWNLOADS:
        ACTIVE_DOWNLOADS[task_id]["cancelled"] = True
        await callback_query.answer("🛑 Cancelling active routines...", show_alert=True)
    else:
        await callback_query.answer("⚠️ Task reference not found or completed.", show_alert=True)

@bot.on_callback_query(filters.regex(r"^setpwd_(.+)$"))
async def ask_password(client, callback_query):
    file_uuid = callback_query.matches[0].group(1)
    file_info = db_get_file(file_uuid)
    if not file_info or file_info["owner_id"] != callback_query.from_user.id:
        return await callback_query.answer("⚠️ Action unauthorized.", show_alert=True)

    PENDING_PASSWORD[callback_query.from_user.id] = {
        "uuid": file_uuid,
        "chat_id": callback_query.message.chat.id,
        "message_id": callback_query.message.id
    }
    await callback_query.answer()
    await callback_query.message.reply_text("🔑 Send me the password configuration string for this link payload, or send /cancel.")

@bot.on_callback_query(filters.regex(r"^rmpwd_(.+)$"))
async def remove_password_callback(client, callback_query):
    file_uuid = callback_query.matches[0].group(1)
    file_info = db_get_file(file_uuid)
    if not file_info or file_info["owner_id"] != callback_query.from_user.id:
        return await callback_query.answer("⚠️ Action unauthorized.", show_alert=True)

    db_set_password(file_uuid, password=None)
    await callback_query.answer("🔓 Password protection disabled!", show_alert=True)

    download_link = f"{BASE_URL}/download/{file_uuid}"
    success_text = (
        f"✅ **File Successfully Uploaded!**\n\n"
        f"📦 **Name:** `{file_info['original_name']}`\n"
        f"🔗 **Link:** {download_link}\n\n"
        f"⏳ This link will automatically expire in {RETENTION_HOURS} hours."
    )
    keyboard = build_result_keyboard(file_uuid, is_protected=False)
    
    try:
        await callback_query.message.edit_caption(caption=success_text, reply_markup=keyboard)
    except Exception:
        try:
            await callback_query.message.edit_text(text=success_text, reply_markup=keyboard, disable_web_page_preview=True)
        except Exception:
            pass

@bot.on_message(filters.private & filters.text & filters.create(lambda _, __, m: m.from_user.id in PENDING_PASSWORD), group=-1)
async def set_password_handler(client, message: Message):
    user_id = message.from_user.id
    data = PENDING_PASSWORD.pop(user_id)
    file_uuid = data["uuid"]
    orig_chat_id = data["chat_id"]
    orig_msg_id = data["message_id"]

    if message.text.strip().lower() == "/cancel":
        return await message.reply_text("❌ Operation cancelled.")

    password = message.text.strip()
    if len(password) < 4:
        PENDING_PASSWORD[user_id] = data
        return await message.reply_text("⚠️ Threshold requirements dictate minimum length of 4 parameters. Try again.")

    db_set_password(file_uuid, password)
    try:
        await message.delete()
    except Exception:
        pass
    
    await message.reply_text("🔒 **Encryption key active.** Link access validation wall enabled.")

    file_info = db_get_file(file_uuid)
    if file_info:
        download_link = f"{BASE_URL}/download/{file_uuid}"
        success_text = (
            f"✅ **File Successfully Uploaded!**\n\n"
            f"📦 **Name:** `{file_info['original_name']}`\n"
            f"🔗 **Link:** {download_link}\n\n"
            f"⏳ This link will automatically expire in {RETENTION_HOURS} hours."
        )
        keyboard = build_result_keyboard(file_uuid, is_protected=True)
        try:
            await client.edit_message_caption(chat_id=orig_chat_id, message_id=orig_msg_id, caption=success_text, reply_markup=keyboard)
        except Exception:
            try:
                await client.edit_message_text(chat_id=orig_chat_id, message_id=orig_msg_id, text=success_text, reply_markup=keyboard, disable_web_page_preview=True)
            except Exception:
                pass

@bot.on_message((filters.command("mylinks") | filters.regex(r"^📂 My Links$")) & filters.private)
async def my_links(client, message: Message):
    files = db_get_files_by_owner(message.from_user.id)
    now = time.time()
    active_files = [f for f in files if now - f["upload_time"] <= RETENTION_SEC]

    if not active_files:
        return await message.reply_text("📭 Active link tracking data clear.")

    await message.reply_text(f"📂 Active link parameters: **{len(active_files)}** entries verified.")
    for f in active_files:
        remaining = RETENTION_SEC - (now - f["upload_time"])
        hrs, mins = int(remaining // 3600), int((remaining % 3600) // 60)
        lock_icon = "🔒 Protected" if f["password_hash"] else "🔓 Public"

        text = f"📄 **{f['original_name']}**\n{lock_icon} • ⬇️ {f['downloads']} downloads\n⏳ Expires in {hrs}h {mins}m\n🔗 {BASE_URL}/download/{f['uuid']}"
        keyboard = build_result_keyboard(f['uuid'], is_protected=bool(f["password_hash"]))
        await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)

async def download_url_to_path(url: str, download_path: str, task_id: str, status_msg: Message, original_name: str, keyboard: InlineKeyboardMarkup, label: str):
    start_time = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            current_size = 0
            with open(download_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(65536):
                    if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
                        raise Exception("Cancelled by user")
                    f.write(chunk)
                    current_size += len(chunk)
                    now = time.time()
                    if now - ACTIVE_DOWNLOADS[task_id]["last_update"] >= 2.0 or current_size == total_size:
                        ACTIVE_DOWNLOADS[task_id]["last_update"] = now
                        elapsed = now - start_time
                        speed = current_size / elapsed if elapsed > 0 else 0
                        bar = make_progress_bar(current_size, total_size) if total_size else "[------------] ?%"
                        size_info = f"{human_size(current_size)} / {human_size(total_size)}" if total_size else human_size(current_size)
                        text = f"⬇️ **{label}...**\n\n📄 **File:** `{original_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
                        try:
                            await status_msg.edit_text(text, reply_markup=keyboard)
                        except MessageNotModified:
                            pass

@bot.on_message(filters.private & filters.text & filters.regex(r"^https?://"))
async def handle_direct_url(client, message: Message):
    url = message.text.strip()
    choice_id = uuid.uuid4().hex[:10]
    parsed = urlparse(url)
    original_name = os.path.basename(parsed.path) or f"download_{choice_id}.bin"
    PENDING_URL_CHOICE[choice_id] = {"url": url, "original_name": original_name}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload to Telegram", callback_data=f"urltg_{choice_id}")],
        [InlineKeyboardButton("🌐 Host on Server", callback_data=f"urlhost_{choice_id}")],
        [InlineKeyboardButton("🛑 Cancel", callback_data=f"urlcancel_{choice_id}")]
    ])
    await message.reply_text(f"🔗 **Link received:**\n`{url}`\n\nHow would you like this delivered?", reply_markup=keyboard, disable_web_page_preview=True)

@bot.on_callback_query(filters.regex(r"^urlcancel_(.+)$"))
async def cancel_url_choice(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    PENDING_URL_CHOICE.pop(choice_id, None)
    await callback_query.answer("Cancelled.")
    try:
        await callback_query.message.delete()
    except Exception:
        pass

@bot.on_callback_query(filters.regex(r"^urltg_(.+)$"))
async def handle_url_choice_telegram(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    choice = PENDING_URL_CHOICE.pop(choice_id, None)
    if not choice:
        return await callback_query.answer("⚠️ Request state parameter expired.", show_alert=True)

    await callback_query.answer()
    url, original_name = choice["url"], choice["original_name"]
    task_id = uuid.uuid4().hex[:10]
    download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{original_name}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

    status_msg = await callback_query.message.edit_text(f"⏳ **Preparing URL Download...**\n🔗 `{url}`", reply_markup=keyboard, disable_web_page_preview=True)
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}

    try:
        await download_url_to_path(url, download_path, task_id, status_msg, original_name, keyboard, "Downloading for Telegram upload")
        if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
            raise Exception("Cancelled by user")

        ACTIVE_DOWNLOADS[task_id]["last_update"] = 0.0
        await status_msg.edit_text(f"📤 **Preparing Upload...**\n📄 `{original_name}`")
        await client.send_document(
            chat_id=callback_query.message.chat.id, document=download_path, file_name=original_name,
            caption=f"✅ `{original_name}`", progress=upload_progress, progress_args=(task_id, status_msg, original_name, time.time())
        )
        await status_msg.delete()
    except Exception as e:
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text(f"🛑 **Cancelled**\n📄 `{original_name}`")
        else:
            await status_msg.edit_text(f"❌ **Failed**\n📄 `{original_name}`\n\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)
        cleanup_file(download_path)

@bot.on_callback_query(filters.regex(r"^urlhost_(.+)$"))
async def handle_url_choice_host(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    choice = PENDING_URL_CHOICE.pop(choice_id, None)
    if not choice:
        return await callback_query.answer("⚠️ Request entry timeout expiration context.", show_alert=True)

    await callback_query.answer()
    url, original_name = choice["url"], choice["original_name"]
    task_id = uuid.uuid4().hex[:10]
    download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{original_name}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

    status_msg = await callback_query.message.edit_text(f"⏳ **Preparing URL Download...**\n🔗 `{url}`", reply_markup=keyboard, disable_web_page_preview=True)
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}

    try:
        await download_url_to_path(url, download_path, task_id, status_msg, original_name, keyboard, "Downloading for server hosting")
        if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
            raise Exception("Cancelled by user")
        await finalize_upload(status_msg, original_name, download_path, callback_query.from_user.id)
    except Exception as e:
        cleanup_file(download_path)
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text(f"🛑 **Download Cancelled**\n📄 `{original_name}`")
        else:
            await status_msg.edit_text(f"❌ **Download Failed**\n📄 `{original_name}`\n\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)

@bot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.voice | filters.photo | filters.animation | filters.video_note | filters.sticker))
async def handle_media_routing(client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in USER_BATCHES:
        USER_BATCHES[user_id] = {
            "messages": [],
            "task": asyncio.create_task(process_media_batch(user_id, chat_id))
        }
    
    USER_BATCHES[user_id]["messages"].append(message)

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    menu_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📂 My Links")]],
        resize_keyboard=True
    )

    user_name = message.from_user.first_name

    await message.reply_text(
        f"👋 **Welcome {user_name}, to the Direct Link Generator!**\n\n"
        "Send me any file, photo, video, audio, or a **direct URL**.\n\n"
        "📁 Files you send are re-hosted with a link (QR code, download counter, optional password).\n"
        "📦 Multi-file forwards automatically bundle together inside a clean structured Zip package!\n\n"
        "📂 Tap the **My Links** button below to view, track, or delete your active server links.\n"
        f"🔐 Server-hosted files are kept securely and deleted after {RETENTION_HOURS} hours.",
        reply_markup=menu_keyboard
    )

if __name__ == "__main__":
    print("Initializing structural tracking entities...")
    init_db()

    print("Spawning automated file clean lifecycle manager...")
    cleanup_thread = threading.Thread(target=cleanup_expired_files, daemon=True)
    cleanup_thread.start()

    print("Exposing web download port bindings...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print("Establishing interface polling execution frameworks...")
    bot.run()