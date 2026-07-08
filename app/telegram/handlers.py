import asyncio
import json
import os
import time
import uuid
from urllib.parse import urlparse

from pyrogram import filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from ..config import ADMIN_IDS, BASE_URL, DOWNLOAD_DIR, RETENTION_HOURS, RETENTION_SEC
from ..database import (
    db_add_file,
    db_delete_file,
    db_get_all_files,
    db_get_all_users,
    db_get_file,
    db_get_files_by_owner,
    db_get_path_reference_count,
    db_set_ban_status,
    db_set_password,
    generate_short_code,
    db_get_user_stats,
    db_set_user_quota,
    db_get_user_quota,
    db_get_user_active_storage,
    db_user_exists,
    db_get_user,
    db_search_users,
)
from ..state import ACTIVE_DOWNLOADS, PENDING_PASSWORD, PENDING_URL_CHOICE, USER_BATCHES
from ..utils import cleanup_file, human_size
from .client import admin_only, bot
from .progress import upload_progress
from .uploads import (
    build_result_keyboard,
    download_url_to_path,
    finalize_upload,
    process_media_batch,
    check_quota,
)



@bot.on_message(filters.command("admin_stats") & filters.private)
@admin_only
async def admin_stats_cmd(client, message: Message):
    files = db_get_all_files()
    users = db_get_all_users()

    total_files = len(files)
    total_users = len(users)
    total_downloads = sum(f["downloads"] for f in files)

    unique_paths = set(f["local_path"] for f in files)
    total_bytes = 0
    for path in unique_paths:
        if os.path.exists(path):
            total_bytes += os.path.getsize(path)

    await message.reply_text(
        f"📊 **Global Metrics Overview**\n\n"
        f"👥 **Total Tracked Users:** `{total_users}`\n"
        f"📂 **Active Database Link Rows:** `{total_files}`\n"
        f"💾 **Disk Space Consumed:** `{human_size(total_bytes)}`\n"
        f"📥 **Accumulated Downloads Served:** `{total_downloads}`"
    )


async def send_admin_user_panel(client, chat_id: int, user_id: int, edit_message_id: int = None):
    u = db_get_user(user_id)
    if not u:
        err_text = f"❌ User `{user_id}` does not exist in the database."
        if edit_message_id:
            try:
                await client.edit_message_text(chat_id, edit_message_id, err_text)
            except Exception:
                pass
        else:
            await client.send_message(chat_id, err_text)
        return

    stats = db_get_user_stats(user_id, RETENTION_SEC)
    unique_paths = set(stats["active_paths"])
    active_storage_bytes = 0
    for path in unique_paths:
        if os.path.exists(path):
            active_storage_bytes += os.path.getsize(path)

    quota_bytes = db_get_user_quota(user_id)
    quota_str = human_size(quota_bytes) if quota_bytes > 0 else "Unlimited"

    is_banned = bool(u.get("is_banned"))
    ban_status_str = "🚫 Banned" if is_banned else "✅ Active"

    name_parts = []
    if u.get("first_name"):
        name_parts.append(u["first_name"])
    if u.get("last_name"):
        name_parts.append(u["last_name"])
    full_name = " ".join(name_parts) or "Unknown Name"
    username = f"@{u['username']}" if u.get("username") else "No username"

    panel_text = (
        f"👤 **User Profile & Management**\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"📛 **Name:** {full_name}\n"
        f"💬 **Username:** {username}\n"
        f"🚦 **Status:** {ban_status_str}\n\n"
        f"📊 **Activity Summary:**\n"
        f"🔗 **Active Links:** `{stats['active_links']}`\n"
        f"📂 **Total Links:** `{stats['total_links']}`\n"
        f"💾 **Active Storage:** `{human_size(active_storage_bytes)}` / `{quota_str}`\n"
        f"📥 **Downloads Served:** `{stats['total_downloads']}`\n"
        f"👥 **Unique Downloaders:** `{stats['unique_users']}`"
    )

    ban_btn_text = "✅ Unban User" if is_banned else "🚫 Ban User"
    ban_callback = f"admin_unb_{user_id}" if is_banned else f"admin_ban_{user_id}"

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(ban_btn_text, callback_data=ban_callback)],
        [
            InlineKeyboardButton("➖ 1 GB", callback_data=f"admin_qdec_{user_id}"),
            InlineKeyboardButton("➕ 1 GB", callback_data=f"admin_qinc_{user_id}"),
            InlineKeyboardButton("♾️ Unlimited", callback_data=f"admin_qunl_{user_id}")
        ],
        [
            InlineKeyboardButton("📂 View Active Links", callback_data=f"admin_vlinks_{user_id}"),
            InlineKeyboardButton("🗑️ Delete All Links", callback_data=f"admin_delall_{user_id}")
        ]
    ])

    if edit_message_id:
        try:
            await client.edit_message_text(chat_id, edit_message_id, panel_text, reply_markup=kbd)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await client.send_message(chat_id, panel_text, reply_markup=kbd)
            except Exception:
                pass
    else:
        await client.send_message(chat_id, panel_text, reply_markup=kbd)


@bot.on_message(filters.command("admin_search") & filters.private)
@admin_only
async def admin_search_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_search <username or name>`")

    query = parts[1].strip()
    results = db_search_users(query)

    if not results:
        return await message.reply_text("❌ No users matching that query were found.")

    buttons = []
    for u in results[:20]:
        name_parts = []
        if u.get("first_name"):
            name_parts.append(u["first_name"])
        if u.get("last_name"):
            name_parts.append(u["last_name"])
        name = " ".join(name_parts) or "Unknown Name"
        username_str = f" (@{u['username']})" if u.get("username") else ""
        button_text = f"{name}{username_str} [{u['user_id']}]"
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"admin_view_{u['user_id']}")])

    reply_markup = InlineKeyboardMarkup(buttons)
    await message.reply_text(
        f"🔍 **Search Results for:** `{query}`\nSelect a user below to manage settings:",
        reply_markup=reply_markup
    )


@bot.on_message(filters.private & filters.regex(r"^\d+$"))
@admin_only
async def admin_user_id_msg(client, message: Message):
    user_id = int(message.text.strip())
    await send_admin_user_panel(client, message.chat.id, user_id)


@bot.on_callback_query(filters.regex(r"^admin_view_(.+)$"))
async def admin_view_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)
    target_id = int(callback_query.matches[0].group(1))
    await callback_query.answer()
    await send_admin_user_panel(client, callback_query.message.chat.id, target_id, edit_message_id=callback_query.message.id)


@bot.on_callback_query(filters.regex(r"^admin_(ban|unb)_(.+)$"))
async def admin_ban_unban_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)

    action = callback_query.matches[0].group(1)
    target_id = int(callback_query.matches[0].group(2))

    ban = (action == "ban")
    db_set_ban_status(target_id, ban=ban)

    action_str = "restricted" if ban else "unrestricted"
    await callback_query.answer(f"User {target_id} has been {action_str}.", show_alert=True)
    await send_admin_user_panel(client, callback_query.message.chat.id, target_id, edit_message_id=callback_query.message.id)


@bot.on_callback_query(filters.regex(r"^admin_q(inc|dec|unl)_(.+)$"))
async def admin_quota_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)

    action = callback_query.matches[0].group(1)
    target_id = int(callback_query.matches[0].group(2))

    current_quota = db_get_user_quota(target_id)
    one_gb = 1024 * 1024 * 1024

    if action == "unl":
        db_set_user_quota(target_id, -1)
        await callback_query.answer("Quota set to Unlimited.", show_alert=True)
    elif action == "inc":
        if current_quota == -1:
            await callback_query.answer("User already has unlimited quota.", show_alert=True)
            return
        new_quota = current_quota + one_gb
        db_set_user_quota(target_id, new_quota)
        await callback_query.answer("Quota increased by 1 GB.", show_alert=True)
    elif action == "dec":
        if current_quota == -1:
            await callback_query.answer("Cannot decrease from unlimited. Set a custom quota first.", show_alert=True)
            return
        new_quota = max(one_gb, current_quota - one_gb)
        db_set_user_quota(target_id, new_quota)
        await callback_query.answer("Quota decreased by 1 GB.", show_alert=True)

    await send_admin_user_panel(client, callback_query.message.chat.id, target_id, edit_message_id=callback_query.message.id)


@bot.on_callback_query(filters.regex(r"^admin_vlinks_(.+)$"))
async def admin_view_links_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)

    target_id = int(callback_query.matches[0].group(1))
    files = db_get_files_by_owner(target_id)
    now = time.time()
    active_files = [f for f in files if now - f["upload_time"] <= RETENTION_SEC]

    if not active_files:
        await callback_query.answer("No active links found for this user.", show_alert=True)
        return

    await callback_query.answer()

    response_text = f"📂 **Active Links for User `{target_id}`**:\n\n"
    for f in active_files:
        remaining = RETENTION_SEC - (now - f["upload_time"])
        hrs, mins = int(remaining // 3600), int((remaining % 3600) // 60)
        lock_icon = "🔒" if f["password_hash"] else "🔓"
        response_text += (
            f"📄 `{f['original_name']}`\n"
            f"   Code: `/admin_delete {f['uuid']}`\n"
            f"   Downloads: {f['downloads']} | {lock_icon} | Expires in {hrs}h {mins}m\n"
            f"   🔗 {BASE_URL}/download/{f['uuid']}\n\n"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Profile", callback_data=f"admin_view_{target_id}")]
    ])
    await client.send_message(chat_id=callback_query.message.chat.id, text=response_text[:4096], reply_markup=keyboard, disable_web_page_preview=True)


@bot.on_callback_query(filters.regex(r"^admin_delall_(.+)$"))
async def admin_delall_confirm_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)

    target_id = int(callback_query.matches[0].group(1))
    await callback_query.answer()

    text = f"⚠️ **Confirm Deletion**\n\nAre you sure you want to delete ALL active links for user `{target_id}`? This action is permanent."
    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚨 Yes, Delete All", callback_data=f"admin_delallconf_{target_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"admin_view_{target_id}")
        ]
    ])
    await client.edit_message_text(chat_id=callback_query.message.chat.id, message_id=callback_query.message.id, text=text, reply_markup=kbd)


@bot.on_callback_query(filters.regex(r"^admin_delallconf_(.+)$"))
async def admin_delall_execute_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        return await callback_query.answer("⚠️ Access Denied", show_alert=True)

    target_id = int(callback_query.matches[0].group(1))
    files = db_get_files_by_owner(target_id)

    deleted_count = 0
    for f in files:
        db_delete_file(f["uuid"])
        if db_get_path_reference_count(f["local_path"]) == 0:
            cleanup_file(f["local_path"])
        deleted_count += 1

    await callback_query.answer(f"Successfully deleted {deleted_count} links.", show_alert=True)
    await send_admin_user_panel(client, callback_query.message.chat.id, target_id, edit_message_id=callback_query.message.id)


@bot.on_message(filters.command("admin_ban") & filters.private)
@admin_only
async def admin_ban_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_ban <user_id>`")

    try:
        target_id = int(parts[1].strip())
        db_set_ban_status(target_id, ban=True)
        await message.reply_text(f"🚫 User `{target_id}` has been successfully restricted.")
    except ValueError:
        await message.reply_text("❌ Missing or invalid Target User ID formatting validation.")


@bot.on_message(filters.command("admin_setquota") & filters.private)
@admin_only
async def admin_setquota_cmd(client, message: Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return await message.reply_text("❗ Use format: `/admin_setquota <user_id> <limit_in_MB>` (e.g. 2048 for 2GB, 0 for unlimited)")

    try:
        target_id = int(parts[1].strip())
        limit_mb = int(parts[2].strip())
        if limit_mb < 0:
            return await message.reply_text("❌ Quota limit must be 0 (unlimited) or a positive integer.")

        if not db_user_exists(target_id):
            return await message.reply_text(f"❌ User `{target_id}` does not exist in the database.")

        limit_bytes = -1 if limit_mb == 0 else limit_mb * 1024 * 1024
        db_set_user_quota(target_id, limit_bytes)

        limit_str = f"`{limit_mb} MB`" if limit_mb > 0 else "`Unlimited`"
        await message.reply_text(f"✅ Storage quota for user `{target_id}` updated to {limit_str}.")
    except ValueError:
        await message.reply_text("❌ Invalid input parameters. Ensure both User ID and limit are integers.")



@bot.on_message(filters.command("admin_delete") & filters.private)
@admin_only
async def admin_delete_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_delete <code>`")

    target_uuid = parts[1].strip()
    file_info = db_get_file(target_uuid)
    if not file_info:
        return await message.reply_text("❌ Record identity match reference missing from structural scope.")

    db_delete_file(target_uuid)
    if db_get_path_reference_count(file_info["local_path"]) == 0:
        cleanup_file(file_info["local_path"])

    await message.reply_text(f"🗑️ Row link tracking metadata entry `{target_uuid}` forced offline.")


@bot.on_message(filters.command("admin_broadcast") & filters.private)
@admin_only
async def admin_broadcast_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/admin_broadcast <text content>`")

    broadcast_text = parts[1].strip()
    users = db_get_all_users()

    status = await message.reply_text(f"📣 Initializing system global broadcast dispatching to `{len(users)}` metrics...")
    success_count = 0

    for uid in users:
        try:
            await client.send_message(chat_id=uid, text=f"📢 **System Broadcast Notification**\n\n{broadcast_text}")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            print(f"[!] error accured while sending broadcast to user `{uid}`.")
            pass

    await status.edit_text(f"✅ Dispatch complete. Reached `{success_count}/{len(users)}` destinations successfully.")


@bot.on_message(filters.command("clone") & filters.private)
async def clone_link_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/clone <code>`")

    old_uuid = parts[1].strip()
    file_info = db_get_file(old_uuid)
    if not file_info:
        return await message.reply_text("❌ Linked reference missing or expired.")

    new_uuid = generate_short_code()
    meta_dict = json.loads(
        file_info["metadata"]) if file_info["metadata"] else None

    db_add_file(new_uuid, file_info["original_name"],
                file_info["local_path"], message.from_user.id, meta_dict)
    await message.reply_text(f"✅ **Link Duplicated successfully!**\nNew Pointer:\n{BASE_URL}/download/{new_uuid}")


@bot.on_message(filters.command("removepwd") & filters.private)
async def remove_pwd_cmd(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("❗ Use format: `/removepwd <uuid>`")

    target_uuid = parts[1].strip()
    file_info = db_get_file(target_uuid)
    if not file_info or file_info["owner_id"] != message.from_user.id:
        return await message.reply_text("⚠️ Action execution identity confirmation unauthorized.")

    db_set_password(target_uuid, password=None)
    await message.reply_text("🔓 **Password removed.** Link accessibility state shifted to global public.")


@bot.on_message((filters.command("mylinks") | filters.regex(r"^📂 My Links$")) & filters.private)
async def my_links(client, message: Message):
    files = db_get_files_by_owner(message.from_user.id)
    now = time.time()
    active_files = [f for f in files if now -
                    f["upload_time"] <= RETENTION_SEC]

    if not active_files:
        return await message.reply_text("📭 Active link tracking data clear.")

    await message.reply_text(f"📂 Active link parameters: **{len(active_files)}** entries verified.")
    for f in active_files:
        remaining = RETENTION_SEC - (now - f["upload_time"])
        hrs, mins = int(remaining // 3600), int((remaining % 3600) // 60)
        lock_icon = "🔒 Protected" if f["password_hash"] else "🔓 Public"

        text = f"📄 **{f['original_name']}**\n{lock_icon} • ⬇️ {f['downloads']} downloads\n⏳ Expires in {hrs}h {mins}m\n🔗 {BASE_URL}/download/{f['uuid']}"
        keyboard = build_result_keyboard(
            f['uuid'], is_protected=bool(f["password_hash"]))
        await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)


@bot.on_message((filters.command("summary") | filters.regex(r"^📊 Summary$")) & filters.private)
async def general_summary(client, message: Message):
    user_id = message.from_user.id
    stats = db_get_user_stats(user_id, RETENTION_SEC)

    unique_paths = set(stats["active_paths"])
    total_bytes = 0
    for path in unique_paths:
        if os.path.exists(path):
            total_bytes += os.path.getsize(path)

    quota_bytes = db_get_user_quota(user_id)
    quota_str = human_size(quota_bytes) if quota_bytes > 0 else "Unlimited"

    await message.reply_text(
        f"📊 **Metrics Summary**\n\n"
        f"🔗 **Active Links:** `{stats['active_links']}`\n"
        f"📁 **Total Uploaded Links:** `{stats['total_links']}`\n"
        f"💾 **Active Storage Used:** `{human_size(total_bytes)}` / `{quota_str}`\n"
        f"📥 **Total Downloads Served:** `{stats['total_downloads']}`\n"
        f"👥 **Unique Downloaders:** `{stats['unique_users']}`"
    )


@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    menu_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📂 My Links"), KeyboardButton("📊 Summary")]],
        resize_keyboard=True
    )

    user_name = message.from_user.first_name

    await message.reply_text(
        f"👋 **Welcome {user_name}, to the Direct Link Generator!**\n\n"
        "Send me any file, photo, video, audio, or a **direct URL**.\n\n"
        "📁 Files you send are re-hosted with a link (QR code, download counter, optional password).\n"
        "📦 Multi-file forwards automatically bundle together inside a clean structured Zip package!\n\n"
        "📂 Tap the **My Links** button below to view/delete your active server links, or click **Summary** to check global stats.\n"
        f"🔐 Server-hosted files are kept securely and deleted after {RETENTION_HOURS} hours.",
        reply_markup=menu_keyboard
    )


@bot.on_callback_query(filters.regex(r"^cancel_(.*)$"))
async def handle_cancellation(client, callback_query):
    task_id = callback_query.matches[0].group(1)
    if task_id in ACTIVE_DOWNLOADS:
        ACTIVE_DOWNLOADS[task_id]["cancelled"] = True
        await callback_query.answer("🛑 Cancelling active routines...", show_alert=True)
    else:
        await callback_query.answer("⚠️ Task reference not found or completed.", show_alert=True)


@bot.on_callback_query(filters.regex(r"^setpwd_(.+)$"))
async def ask_password(client, callback_query):
    file_uuid = callback_query.matches[0].group(1)
    file_info = db_get_file(file_uuid)
    if not file_info or file_info["owner_id"] != callback_query.from_user.id:
        return await callback_query.answer("⚠️ Action unauthorized.", show_alert=True)

    PENDING_PASSWORD[callback_query.from_user.id] = {
        "uuid": file_uuid,
        "chat_id": callback_query.message.chat.id,
        "message_id": callback_query.message.id
    }
    await callback_query.answer()
    await callback_query.message.reply_text("🔑 Send me the password configuration string for this link payload, or send /cancel.")


@bot.on_callback_query(filters.regex(r"^rmpwd_(.+)$"))
async def remove_password_callback(client, callback_query):
    file_uuid = callback_query.matches[0].group(1)
    file_info = db_get_file(file_uuid)
    if not file_info or file_info["owner_id"] != callback_query.from_user.id:
        return await callback_query.answer("⚠️ Action unauthorized.", show_alert=True)

    db_set_password(file_uuid, password=None)
    await callback_query.answer("🔓 Password protection disabled!", show_alert=True)

    download_link = f"{BASE_URL}/download/{file_uuid}"
    success_text = (
        f"✅ **File Successfully Uploaded!**\n\n"
        f"📦 **Name:** `{file_info['original_name']}`\n"
        f"🔗 **Link:** {download_link}\n\n"
        f"⏳ This link will automatically expire in {RETENTION_HOURS} hours."
    )
    keyboard = build_result_keyboard(file_uuid, is_protected=False)

    try:
        await callback_query.message.edit_caption(caption=success_text, reply_markup=keyboard)
    except Exception:
        try:
            await callback_query.message.edit_text(text=success_text, reply_markup=keyboard, disable_web_page_preview=True)
        except Exception:
            pass


@bot.on_callback_query(filters.regex(r"^delete_(.+)$"))
async def handle_user_delete_callback(client, callback_query):
    file_uuid = callback_query.matches[0].group(1)
    file_info = db_get_file(file_uuid)
    if not file_info:
        return await callback_query.answer("⚠️ File already deleted or not found.", show_alert=True)

    if file_info["owner_id"] != callback_query.from_user.id:
        return await callback_query.answer("⚠️ Action unauthorized.", show_alert=True)

    db_delete_file(file_uuid)
    if db_get_path_reference_count(file_info["local_path"]) == 0:
        cleanup_file(file_info["local_path"])

    await callback_query.answer("🗑️ File deleted successfully!", show_alert=True)
    try:
        await callback_query.message.delete()
    except Exception:
        try:
            await callback_query.message.edit_text("🗑️ **This file link has been deleted and is no longer active.**")
        except Exception:
            pass



@bot.on_message(filters.private & filters.text & filters.create(lambda _, __, m: m.from_user.id in PENDING_PASSWORD), group=-1)
async def set_password_handler(client, message: Message):
    user_id = message.from_user.id
    data = PENDING_PASSWORD.pop(user_id)
    file_uuid = data["uuid"]
    orig_chat_id = data["chat_id"]
    orig_msg_id = data["message_id"]

    if message.text.strip().lower() == "/cancel":
        return await message.reply_text("❌ Operation cancelled.")

    password = message.text.strip()
    if len(password) < 4:
        PENDING_PASSWORD[user_id] = data
        return await message.reply_text("⚠️ Threshold requirements dictate minimum length of 4 parameters. Try again.")

    db_set_password(file_uuid, password)
    try:
        await message.delete()
    except Exception:
        pass

    await message.reply_text("🔒 **Encryption key active.** Link access validation wall enabled.")

    file_info = db_get_file(file_uuid)
    if file_info:
        download_link = f"{BASE_URL}/download/{file_uuid}"
        success_text = (
            f"✅ **File Successfully Uploaded!**\n\n"
            f"📦 **Name:** `{file_info['original_name']}`\n"
            f"🔗 **Link:** {download_link}\n\n"
            f"⏳ This link will automatically expire in {RETENTION_HOURS} hours."
        )
        keyboard = build_result_keyboard(file_uuid, is_protected=True)
        try:
            await client.edit_message_caption(chat_id=orig_chat_id, message_id=orig_msg_id, caption=success_text, reply_markup=keyboard)
        except Exception:
            try:
                await client.edit_message_text(chat_id=orig_chat_id, message_id=orig_msg_id, text=success_text, reply_markup=keyboard, disable_web_page_preview=True)
            except Exception:
                pass


@bot.on_message(filters.private & filters.text & filters.regex(r"^https?://"))
async def handle_direct_url(client, message: Message):
    url = message.text.strip()
    choice_id = uuid.uuid4().hex[:10]
    parsed = urlparse(url)
    original_name = os.path.basename(
        parsed.path) or f"download_{choice_id}.bin"
    PENDING_URL_CHOICE[choice_id] = {
        "url": url, "original_name": original_name}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload to Telegram",
                              callback_data=f"urltg_{choice_id}")],
        [InlineKeyboardButton("🌐 Host on Server",
                              callback_data=f"urlhost_{choice_id}")],
        [InlineKeyboardButton(
            "🛑 Cancel", callback_data=f"urlcancel_{choice_id}")]
    ])
    await message.reply_text(f"🔗 **Link received:**\n`{url}`\n\nHow would you like this delivered?", reply_markup=keyboard, disable_web_page_preview=True)


@bot.on_callback_query(filters.regex(r"^urlcancel_(.+)$"))
async def cancel_url_choice(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    PENDING_URL_CHOICE.pop(choice_id, None)
    await callback_query.answer("Cancelled.")
    try:
        await callback_query.message.delete()
    except Exception:
        pass


@bot.on_callback_query(filters.regex(r"^urltg_(.+)$"))
async def handle_url_choice_telegram(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    choice = PENDING_URL_CHOICE.pop(choice_id, None)
    if not choice:
        return await callback_query.answer("⚠️ Request state parameter expired.", show_alert=True)

    await callback_query.answer()
    url, original_name = choice["url"], choice["original_name"]
    task_id = uuid.uuid4().hex[:10]
    download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{original_name}")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

    status_msg = await callback_query.message.edit_text(f"⏳ **Preparing URL Download...**\n🔗 `{url}`", reply_markup=keyboard, disable_web_page_preview=True)
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}

    try:
        await download_url_to_path(url, download_path, task_id, status_msg, original_name, keyboard, "Downloading for Telegram upload")
        if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
            raise Exception("Cancelled by user")

        ACTIVE_DOWNLOADS[task_id]["last_update"] = 0.0
        await status_msg.edit_text(f"📤 **Preparing Upload...**\n📄 `{original_name}`")
        await client.send_document(
            chat_id=callback_query.message.chat.id, document=download_path, file_name=original_name,
            caption=f"✅ `{original_name}`", progress=upload_progress, progress_args=(task_id, status_msg, original_name, time.time())
        )
        await status_msg.delete()
    except Exception as e:
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text(f"🛑 **Cancelled**\n📄 `{original_name}`")
        else:
            await status_msg.edit_text(f"❌ **Failed**\n📄 `{original_name}`\n\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)
        cleanup_file(download_path)


@bot.on_callback_query(filters.regex(r"^urlhost_(.+)$"))
async def handle_url_choice_host(client, callback_query):
    choice_id = callback_query.matches[0].group(1)
    choice = PENDING_URL_CHOICE.pop(choice_id, None)
    if not choice:
        return await callback_query.answer("⚠️ Request entry timeout expiration context.", show_alert=True)

    await callback_query.answer()
    url, original_name = choice["url"], choice["original_name"]

    import aiohttp
    content_length = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=True) as resp:
                content_length = int(resp.headers.get('content-length', 0))
    except Exception:
        pass

    allowed, quota, used = check_quota(callback_query.from_user.id, content_length)
    if not allowed:
        return await callback_query.message.edit_text(
            f"❌ **Storage Quota Exceeded!**\n\n"
            f"📁 URL File size: `{human_size(content_length)}`\n"
            f"💾 Current storage used: `{human_size(used)}` / `{human_size(quota)}`\n\n"
            f"Please wait for your active links to expire or delete some files.",
            disable_web_page_preview=True
        )

    task_id = uuid.uuid4().hex[:10]
    download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{original_name}")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{task_id}")]])

    status_msg = await callback_query.message.edit_text(f"⏳ **Preparing URL Download...**\n🔗 `{url}`", reply_markup=keyboard, disable_web_page_preview=True)
    ACTIVE_DOWNLOADS[task_id] = {"cancelled": False, "last_update": 0.0}

    try:
        await download_url_to_path(url, download_path, task_id, status_msg, original_name, keyboard, "Downloading for server hosting")
        if ACTIVE_DOWNLOADS.get(task_id, {}).get("cancelled"):
            raise Exception("Cancelled by user")

        if os.path.exists(download_path):
            actual_size = os.path.getsize(download_path)
            allowed, quota, used = check_quota(callback_query.from_user.id, max(0, actual_size - content_length))
            if not allowed:
                raise Exception(f"Quota exceeded: actual size {human_size(actual_size)} exceeds remaining storage limit")

        await finalize_upload(status_msg, original_name, download_path, callback_query.from_user.id)
    except Exception as e:
        cleanup_file(download_path)
        if "cancelled by user" in str(e).lower():
            await status_msg.edit_text(f"🛑 **Download Cancelled**\n📄 `{original_name}`")
        elif "quota exceeded" in str(e).lower():
            await status_msg.edit_text(f"❌ **Quota Exceeded**\n\n`{str(e)}`")
        else:
            await status_msg.edit_text(f"❌ **Download Failed**\n📄 `{original_name}`\n\n`{str(e)}`")
    finally:
        ACTIVE_DOWNLOADS.pop(task_id, None)


@bot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.voice | filters.photo | filters.animation | filters.video_note | filters.sticker))
async def handle_media_routing(client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in USER_BATCHES:
        USER_BATCHES[user_id] = {
            "messages": [],
            "task": asyncio.create_task(process_media_batch(user_id, chat_id))
        }

    USER_BATCHES[user_id]["messages"].append(message)
