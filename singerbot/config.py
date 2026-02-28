import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "23550251"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION = os.getenv("SESSION", "")
LOG_GROUP = int(os.getenv("LOG_GROUP", "-1003387540146"))
RADIO_BATCH = int(os.getenv("RADIO_BATCH", "25"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DOWNLOADS_DIR = "/tmp/singerbot_cache"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Cookies file location - can be overridden with COOKIES_FILE or YOUTUBE_COOKIES env var
_DEFAULT_COOKIES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")
COOKIES_FILE = os.getenv("COOKIES_FILE") or os.getenv("YOUTUBE_COOKIES") or _DEFAULT_COOKIES_PATH

# YouTube extraction options
# JS runtime for yt-dlp (deno, node, etc.) - set to "node" if deno is not available
YOUTUBE_JS_RUNTIME = os.getenv("YOUTUBE_JS_RUNTIME", "node")
