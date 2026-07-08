import threading
import time

from .config import RETENTION_SEC
from .database import db_delete_file, db_get_all_files, db_get_path_reference_count, init_db
from .telegram import bot
from .utils import cleanup_file


def cleanup_expired_files():
    """Background loop: every hour, purge database rows (and orphaned files
    on disk) whose retention window has elapsed."""
    while True:
        time.sleep(3600)
        print("[Cleanup] Running reference check on expiration routines...")
        current_time = time.time()
        files = db_get_all_files()
        expired = [f for f in files if current_time -
                   f["upload_time"] > RETENTION_SEC]

        for f in expired:
            db_delete_file(f["uuid"])
            if db_get_path_reference_count(f["local_path"]) == 0:
                cleanup_file(f["local_path"])

        if expired:
            print(
                f"[Cleanup] Cleared {len(expired)} database file references.")


def main():
    print("Initializing structural tracking entities...")
    init_db()

    print("Spawning automated file clean lifecycle manager...")
    cleanup_thread = threading.Thread(
        target=cleanup_expired_files, daemon=True)
    cleanup_thread.start()

    print("Establishing interface polling execution frameworks...")
    bot.run()


if __name__ == "__main__":
    main()
