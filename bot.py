from musicbot.core import app, user, calls, logger
from pyrogram import idle
import musicbot.handlers

async def main():
    await app.start()
    await user.start()
    await calls.start()
    logger.info("live")
    await idle()

if __name__ == "__main__":
    app.run(main())
