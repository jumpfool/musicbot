from singerbot.core import app, user, calls, logger
from pyrogram import idle
import singerbot.handlers
from singerbot.config import COOKIES_FILE
import os

async def main():
    # Log cookies file status on startup
    if COOKIES_FILE:
        logger.info(f"Checking for cookies file at: {COOKIES_FILE}")
        if os.path.exists(COOKIES_FILE):
            if os.access(COOKIES_FILE, os.R_OK):
                file_size = os.path.getsize(COOKIES_FILE)
                logger.info(f"✓ Cookies file found and accessible (size: {file_size} bytes)")
                logger.info(f"  Path: {os.path.abspath(COOKIES_FILE)}")
            else:
                logger.warning(f"✗ Cookies file exists but is not readable: {COOKIES_FILE}")
        else:
            logger.warning(f"✗ Cookies file not found: {COOKIES_FILE}")
            logger.warning(f"  YouTube may block requests without cookies. See cookies.txt.example for setup instructions.")
    else:
        logger.warning("COOKIES_FILE path is not configured")

    await app.start()
    await user.start()
    await calls.start()
    logger.info("live")
    await idle()

if __name__ == "__main__":
    app.run(main())
