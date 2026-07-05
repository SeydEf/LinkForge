import os


def get_media(message):
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
