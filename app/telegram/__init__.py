from .client import bot
from . import handlers  # noqa: F401  (registers @bot.on_message / on_callback_query handlers)

__all__ = ["bot"]