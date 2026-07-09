import asyncio
from functools import wraps

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.types import Message

from ..config import ADMIN_IDS, API_HASH, API_ID, BOT_TOKEN, SESSION_NAME
from ..database import db_add_user, db_is_banned

bot = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def admin_only(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        if message.from_user.id not in ADMIN_IDS:
            await message.reply_text(
                "⚠️ **Access Denied:** Administrator authorization required."
            )
            return
        return await func(client, message, *args, **kwargs)

    return wrapper


@bot.on_message(group=-2)
async def check_ban_and_register(client, message: Message):
    if message.from_user:
        db_add_user(
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
        if db_is_banned(message.from_user.id):
            await message.reply_text(
                "🚫 Your access to this bot has been restricted by an administrator."
            )
            message.stop_propagation()
