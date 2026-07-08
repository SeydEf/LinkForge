import os


def get_int(name: str, default: int) -> int:
    """Safely read an integer environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_int_list(name: str, default: list[int]) -> list[int]:
    value = os.environ.get(name)
    if not value:
        return default

    result = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            pass

    return result or default


API_ID = get_int("API_ID", "1")
API_HASH = os.environ.get("API_HASH", "1")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "1")

BASE_URL = os.environ.get("BASE_URL", "https://your.base.url.com")
ADMIN_IDS = get_int_list("ADMIN_IDS", [123456789])

DATA_DIR = "data"
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
DB_PATH = os.path.join(DATA_DIR, "database.db")
SESSION_NAME = os.path.join(DATA_DIR, "file_to_link_bot")

PROCESS_MEDIA_TIME = float(os.environ.get("PROCESS_MEDIA_TIME", 3.0))
RETENTION_HOURS = get_int("RETENTION_HOURS", 24)
RETENTION_SEC = RETENTION_HOURS * 3600

DEFAULT_USER_STORAGE_LIMIT = get_int("USER_STORAGE_LIMIT_MB", 2048) * 1024 * 1024

START_WEB_THREAD = os.environ.get("START_WEB_THREAD", "false")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
