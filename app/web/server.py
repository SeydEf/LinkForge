import json
import os
import time

from flask import Flask, abort, render_template_string, request, send_file
from werkzeug.security import check_password_hash

from ..config import RETENTION_SEC
from ..database import db_get_analytics, db_get_file, db_increment_downloads, db_log_download
from ..utils import extract_file_metadata
from .templates import PASSWORD_FORM, STATS_TEMPLATE

flask_app = Flask(__name__)


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

    meta = json.loads(file_info["metadata"]) if file_info["metadata"] else extract_file_metadata(
        file_info["local_path"])
    raw_logs = db_get_analytics(file_uuid, limit=50)
    logs = []
    for log in raw_logs:
        entry = dict(log)
        entry["readable_time"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(log["timestamp"]))
        logs.append(entry)

    return render_template_string(STATS_TEMPLATE, file=file_info, logs=logs, meta=meta)


def run_flask():
    flask_app.run(host="0.0.0.0", port=5000, use_reloader=False)
