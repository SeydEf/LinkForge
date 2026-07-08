import os
import threading
import time

from .config import RETENTION_SEC, START_WEB_THREAD
from .database import db_delete_file, db_get_all_files, db_get_path_reference_count, init_db
from .telegram import bot
from .utils import cleanup_file
from .web import run_flask


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

    if START_WEB_THREAD:
        print("Exposing web download port bindings...")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
    else:
        print("Skipping background web server thread (running via WSGI).")

    print("Establishing interface polling execution frameworks...")
    bot.run()


if __name__ == "__main__":
    main()
