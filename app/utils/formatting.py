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
