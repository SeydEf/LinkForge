import hashlib
import mimetypes
import os
from io import BytesIO

import qrcode
from PIL import Image


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


def extract_file_metadata(local_path: str, incoming_duration=None) -> dict:
    meta = {
        "mime_type": "application/octet-stream",
        "md5": "",
        "sha256": "",
        "dimensions": None,
        "duration": incoming_duration,
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
