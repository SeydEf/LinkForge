import time

from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..state import ACTIVE_DOWNLOADS
from ..utils import human_size, make_progress_bar


async def download_progress(
    current: int,
    total: int,
    task_id: str,
    status_msg: Message,
    file_name: str,
    start_time: float,
):
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
    size_info = (
        f"{human_size(current)} / {human_size(total)}" if total else human_size(current)
    )

    text = f"⬇️ **Downloading...**\n\n📄 **File:** `{file_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]]
    )
    try:
        await status_msg.edit_text(text, reply_markup=keyboard)
    except MessageNotModified:
        pass


async def batch_download_progress(
    current: int,
    total: int,
    task_id: str,
    status_msg: Message,
    file_name: str,
    start_time: float,
    idx: int,
    total_items: int,
):
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
    size_info = (
        f"{human_size(current)} / {human_size(total)}" if total else human_size(current)
    )

    text = (
        f"📥 **Downloading item {idx} of {total_items}...**\n\n"
        f"📄 **Current File:** `{file_name}`\n"
        f"`{bar}`\n"
        f"📦 **Size:** {size_info}\n"
        f"🚀 **Speed:** {human_size(speed)}/s"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel Batch", callback_data=f"cancel_{task_id}")]]
    )
    try:
        await status_msg.edit_text(text, reply_markup=keyboard)
    except MessageNotModified:
        pass


async def upload_progress(
    current: int,
    total: int,
    task_id: str,
    status_msg: Message,
    file_name: str,
    start_time: float,
):
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
    size_info = (
        f"{human_size(current)} / {human_size(total)}" if total else human_size(current)
    )

    text = f"📤 **Uploading to Telegram...**\n\n📄 **File:** `{file_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
    try:
        await status_msg.edit_text(text)
    except MessageNotModified:
        pass
