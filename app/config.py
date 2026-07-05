import os

API_ID = os.environ.get("API_ID", "1")
API_HASH = os.environ.get("API_HASH", "1")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "1")

BASE_URL = os.environ.get("BASE_URL", "https://your.base.url.com")
ADMIN_IDS = [int(x) for x in os.environ.get(
    "ADMIN_IDS", "123456789").split(",") if x.strip()]

DATA_DIR = "data"
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
DB_PATH = os.path.join(DATA_DIR, "database.db")
SESSION_NAME = os.path.join(DATA_DIR, "file_to_link_bot")

PROCESS_MEDIA_TIME = float(os.environ.get("PROCESS_MEDIA_TIME", 3.0))
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", 24))
RETENTION_SEC = RETENTION_HOURS * 3600

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
