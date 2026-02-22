import logging
from pyrogram import Client
from pytgcalls import PyTgCalls
from musicbot.config import API_ID, API_HASH, BOT_TOKEN, SESSION

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
calls = PyTgCalls(user)
