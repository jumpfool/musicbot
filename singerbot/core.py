import logging
from pyrogram import Client, errors as pyrogram_errors
from singerbot.config import API_ID, API_HASH, BOT_TOKEN, SESSION

if not hasattr(pyrogram_errors, "GroupcallForbidden") and hasattr(
    pyrogram_errors, "GroupCallForbidden"
):
    pyrogram_errors.GroupcallForbidden = pyrogram_errors.GroupCallForbidden

from pytgcalls import PyTgCalls

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
calls = PyTgCalls(user)
