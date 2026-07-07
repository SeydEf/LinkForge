import asyncio
import os
import time
import uuid
import zipfile

import aiohttp
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import BASE_URL, DOWNLOAD_DIR, PROCESS_MEDIA_TIME, RETENTION_HOURS, RETENTION_SEC
from ..database import (
    db_add_file,
    generate_short_code,
    db_get_user_quota,
    db_get_user_active_storage,
)

from ..state import ACTIVE_DOWNLOADS, USER_BATCHES
from ..utils import cleanup_file, extract_file_metadata, get_media, human_size, make_progress_bar, make_qr_bytes
from .client import bot
from .progress import batch_download_progress, download_progress, upload_progress


def build_result_keyboard(file_uuid: str, is_protected: bool) -> InlineKeyboardMarkup:
    if is_protected:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔓 Remove Password Protection", callback_data=f"rmpwd_{file_uuid}")],
            [InlineKeyboardButton("📊 View Stats Data",
                                  url=f"{BASE_URL}/stats/{file_uuid}")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Set Security Password",
                                  callback_data=f"setpwd_{file_uuid}")],
            [InlineKeyboardButton("📊 View Stats Data",
                                  url=f"{BASE_URL}/stats/{file_uuid}")]
        ])


async def finalize_upload(status_msg: Message, original_name: str, local_path: str, owner_id: int, incoming_duration=None):
    file_uuid = generate_short_code()
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


def check_quota(user_id: int, incoming_bytes: int) -> tuple[bool, int, int]:
    quota = db_get_user_quota(user_id)
    used = db_get_user_active_storage(user_id, RETENTION_SEC)
    
    if quota != -1 and used + incoming_bytes > quota:
        return False, quota, used
    return True, quota, used


async def process_media_batch(user_id: int, chat_id: int):
    await asyncio.sleep(PROCESS_MEDIA_TIME)
    batch = USER_BATCHES.pop(user_id, None)
    if not batch or not batch["messages"]:
        return

    messages = batch["messages"]
    if len(messages) == 1:
        await handle_single_media_processing(messages[0])
        return

    incoming_bytes = 0
    for msg in messages:
        _, media = get_media(msg)
        if media:
            incoming_bytes += getattr(media, "file_size", 0) or 0

    allowed, quota, used = check_quota(user_id, incoming_bytes)
    quota_str = human_size(quota) if quota > 0 else "Unlimited"
    if not allowed:
        await bot.send_message(
            chat_id,
            f"❌ **Storage Quota Exceeded for Batch!**\n\n"
            f"📁 Batch size: `{human_size(incoming_bytes)}`\n"
            f"💾 Current storage used: `{human_size(used)}` / `{quota_str}`\n\n"
            f"Please wait for your active links to expire or delete some files."
        )
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
            f_name = getattr(media, "file_name",
                             None) or f"item_{idx}_{task_id}.bin"
            if m_type == "photo" and not f_name.endswith(".jpg"):
                f_name = f"photo_{idx}_{task_id}.jpg"

            ACTIVE_DOWNLOADS[task_id]["last_update"] = 0.0
            item_start_time = time.time()

            d_path = os.path.join(
                DOWNLOAD_DIR, f"part_{idx}_{uuid.uuid4().hex[:6]}_{f_name}")
            actual_path = await bot.download_media(
                msg, file_name=d_path, progress=batch_download_progress,
                progress_args=(task_id, status_msg, f_name,
                                item_start_time, idx, len(messages))
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

    incoming_bytes = getattr(media, "file_size", 0) or 0
    allowed, quota, used = check_quota(message.from_user.id, incoming_bytes)
    if not allowed:
        await message.reply_text(
            f"❌ **Storage Quota Exceeded!**\n\n"
            f"📁 File size: `{human_size(incoming_bytes)}`\n"
            f"💾 Current storage used: `{human_size(used)}` / `{human_size(quota)}`\n\n"
            f"Please wait for your active links to expire or delete some files."
        )
        return

    task_id = uuid.uuid4().hex[:10]
    file_name = getattr(media, "file_name",
                        None) or f"{media_type}_{task_id}.bin"
    if media_type == "photo" and not file_name.endswith(".jpg"):
        file_name = f"photo_{task_id}.jpg"
    elif media_type == "voice" and not file_name.endswith(".ogg"):
        file_name = f"voice_{task_id}.ogg"

    unique_filename = f"{task_id}_{file_name}"
    download_path = os.path.join(DOWNLOAD_DIR, unique_filename)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

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
                        bar = make_progress_bar(
                            current_size, total_size) if total_size else "[------------] ?%"
                        size_info = f"{human_size(current_size)} / {human_size(total_size)}" if total_size else human_size(
                            current_size)
                        text = f"⬇️ **{label}...**\n\n📄 **File:** `{original_name}`\n`{bar}`\n📦 **Size:** {size_info}\n🚀 **Speed:** {human_size(speed)}/s"
                        try:
                            await status_msg.edit_text(text, reply_markup=keyboard)
                        except MessageNotModified:
                            pass
