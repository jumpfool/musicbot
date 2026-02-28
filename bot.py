import os
from singerbot.core import app, user, calls, logger
from pyrogram import idle
import singerbot.handlers


async def main():
    sc_ids = os.getenv("SOUNDCLOUD_CLIENT_IDS", "")
    if sc_ids:
        count = len([c for c in sc_ids.split(",") if c.strip()])
        logger.info(f"SoundCloud client IDs configured: {count}")
    else:
        logger.warning("SOUNDCLOUD_CLIENT_IDS is not set — SoundCloud features will not work")

    await app.start()
    await user.start()
    await calls.start()
    logger.info("live")
    await idle()


if __name__ == "__main__":
    app.run(main())
