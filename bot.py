#!/usr/bin/env python3
import asyncio
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

import yt_dlp
from pyrogram import Client, filters, idle
from pyrogram.errors import UserNotParticipant
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pytgcalls import PyTgCalls
from pytgcalls.exceptions import GroupCallNotFound
from pytgcalls.types import AudioPiped, Update
from pytgcalls.types.input_stream.quality import HighQualityAudio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "23550251"))
API_HASH = os.getenv("API_HASH", "202fae900aea35b58c36f0ef2e291d61")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8504147648:AAF48lpywTHMR8T1x3xn13KW2lLubKQ9nus")
SESSION = os.getenv(
    "SESSION",
    "AgFnWSsAGAeBARNzh2MxA10ZupJWM0w2BghrluYpDrdhPfcMjLzZUT-FM7Sr6xxZtB10E8dYrHJY6wwgJMadgGOTJgV8Ta7KSxvkK33L1PAxnSBhJiYx0BF5JBK9ZM50c7exYdEdtsHtKBlLLw3dt_iJpK0s1cVMLWcTI0n9sROVO0_d-tHNVtE_a0kVI2gCnZnopUKq0EPh3E0M8mJBMflpJ8QC1ho3sLgmf44IMGQmJRgFtJbjM6Vp20JbLc5aQXVNq8HkGBCGTDfJtuARBsKWNnih910dzuhaAxfU6DS4OrD-jTXOr58Z0DmwrTDKHX89ZJzGfF5mN8AyusSh3X1CJ2wdSAAAAAHQBWNOAA",
)
LOG_GROUP = int(os.getenv("LOG_GROUP", "-1003387540146"))

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
calls = PyTgCalls(user)

queues = {}
active = {}
downloads_dir = "/tmp/music_cache"
os.makedirs(downloads_dir, exist_ok=True)

ban_users = {1938311809}
admin_id = int(os.getenv("ADMIN_ID", "7314932244"))


def is_banned(uid):
    try:
        return int(uid) in ban_users
    except:
        return False


def format_duration(sec):
    if not sec:
        return "live"
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def clean_artist(title, uploader):
    patterns = [r"^(.+?)\s*[-–—]\s*(.+)$", r"^(.+?)\s*[:|]\s*(.+)$"]
    for p in patterns:
        match = re.match(p, title)
        if match:
            return re.sub(
                r"\s*(official|video|audio).*$", "", match.group(1), flags=re.IGNORECASE
            ).strip()
    if uploader:
        return re.sub(
            r"\s*(music|vevo|official).*$", "", uploader, flags=re.IGNORECASE
        ).strip()
    return "unknown"


def download_audio(q):
    opts = {
        "format": "bestaudio",
        "outtmpl": f"{downloads_dir}/%(id)s.%(ext)s",
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
    }
    search = f"ytsearch:{q}" if not q.startswith("http") else q
    with yt_dlp.YoutubeDL(opts) as ydl:
        i = ydl.extract_info(search, download=True)
        if "entries" in i:
            i = i["entries"][0]
        filename = ydl.prepare_filename(i).rsplit(".", 1)[0] + ".mp3"
        return {
            "file": filename,
            "title": i.get("title", "unknown"),
            "artist": clean_artist(i.get("title", ""), i.get("uploader", "")),
            "duration": i.get("duration", 0),
            "thumb": i.get("thumbnail")
            or "https://telegra.ph/file/2f7debf856695e0a17296.png",
            "webpage": i.get("webpage_url", ""),
        }


def search_youtube(q):
    opts = {"format": "bestaudio", "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        i = ydl.extract_info(f"ytsearch5:{q}", download=False)
        results = []
        for entry in i.get("entries", []):
            results.append(
                {
                    "title": entry.get("title", "unknown"),
                    "duration": format_duration(entry.get("duration", 0)),
                }
            )
        return results


async def ensure_assistant_joined(cid):
    try:
        await user.get_chat_member(cid, "me")
        return True
    except UserNotParticipant:
        try:
            link = await app.export_chat_invite_link(cid)
            await user.join_chat(link)
            await asyncio.sleep(2)
            return True
        except:
            return False
    except:
        return False


async def send_now_playing(cid, song, queue_list):
    caption = (
        "**now playing**\n\n"
        f"**song :** {song['title']}\n"
        f"**artist :** {song['artist']}\n"
        f"**duration :** {format_duration(song['duration'])}\n\n"
    )

    if queue_list:
        caption += "**up next:**\n\n"
        for i, s in enumerate(queue_list[:5], 1):
            caption += f"{i}. {s['title']}\n"
        if len(queue_list) > 5:
            caption += f"\n_plus {len(queue_list) - 5} more_"

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("pause", callback_data="pause"),
                InlineKeyboardButton("resume", callback_data="resume"),
            ],
            [
                InlineKeyboardButton("skip", callback_data="skip"),
                InlineKeyboardButton("stop", callback_data="end"),
            ],
        ]
    )

    try:
        if song.get("thumb"):
            await app.send_photo(
                cid, song["thumb"], caption=caption, reply_markup=buttons
            )
        else:
            await app.send_photo(
                cid,
                "https://telegra.ph/file/2f7debf856695e0a17296.png",
                caption=caption,
                reply_markup=buttons,
            )
    except Exception as e:
        logger.warning(f"Photo send failed: {e}, using text")
        await app.send_message(cid, caption, reply_markup=buttons)


async def play_next(cid):
    if cid not in queues or not queues[cid]:
        logger.info(f"Queue empty in {cid}")
        return

    s = queues[cid].pop(0)
    try:
        stream = AudioPiped(s["file"], HighQualityAudio())
        await calls.change_stream(cid, stream)
        active[cid] = s
        await send_now_playing(cid, s, queues.get(cid, []))
        logger.info(f"Playing: {s['title']}")
    except Exception as e:
        logger.error(f"play next error: {e}")
        await play_next(cid)


@app.on_callback_query()
async def callback_handler(_, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else None
    if uid and is_banned(uid):
        await query.answer()
        return
    data = query.data
    cid = query.message.chat.id
    name = query.from_user.first_name.lower() if query.from_user else "unknown"

    if data == "pause":
        try:
            await calls.pause_stream(cid)
            await query.answer("paused", show_alert=False)
            await app.send_message(cid, f"{name} paused")
        except:
            await query.answer("cant pause", show_alert=True)

    elif data == "resume":
        try:
            await calls.resume_stream(cid)
            await query.answer("resumed", show_alert=False)
            await app.send_message(cid, f"{name} resumed")
        except:
            await query.answer("cant resume", show_alert=True)

    elif data == "skip":
        if cid in active:
            await query.answer("skipping", show_alert=False)
            await app.send_message(cid, f"{name} skipped")
            await play_next(cid)
        else:
            await query.answer("nothing playing", show_alert=True)

    elif data == "end":
        try:
            await calls.leave_group_call(cid)
            if cid in queues:
                queues[cid].clear()
            if cid in active:
                del active[cid]
            await query.answer("stopped", show_alert=False)
            await query.message.edit_caption("**stopped**")
            await app.send_message(cid, f"{name} stopped")
        except:
            await query.answer("not in call", show_alert=True)


@app.on_message(filters.command("start"))
async def start(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "add to group", url="https://t.me/MUSlCXBOT?startgroup=true"
                )
            ],
            [
                InlineKeyboardButton("commands", callback_data="help"),
                InlineKeyboardButton("owner", url="https://t.me/Vclub_Tech"),
            ],
        ]
    )

    text = (
        "**япи door music**\n\n"
        "пошол нахуй\n\n"
        "**гавно:**\n"
        "качество хуйня (320kbps)\n"
        "ракета илонмаск гавно\n"
        "пошол ты\n"
        "твою бабку ебнет молнией\n"
        "сам сосу\n\n"
        "**cumанды:**\n"
        "1 добавить в групу\n"
        "2 дать админку и акк инвайтнуть\n"
        "3 start voice chat\n"
        "4 send /play [song name]\n\n"
        "**ты даун:**\n"
        "• /play [song] - сын шлюхи сам узнай\n"
        "• /skip - скип\n"
        "• /pause - пауз\n"
        "• /resume - резюме\n"
        "• /stop - стоп мне неприятно\n"
        "• /queue - квеуе\n\n"
        "сосал да"
    )

    try:
        await m.reply_photo(
            "https://telegra.ph/file/2f7debf856695e0a17296.png",
            caption=text,
            reply_markup=buttons,
        )
    except:
        await m.reply(text, reply_markup=buttons)


@app.on_callback_query(filters.regex("help"))
async def help_cb(_, q: CallbackQuery):
    uid = q.from_user.id if q.from_user else None
    if uid and is_banned(uid):
        await q.answer()
        return
    await q.answer()
    help_text = (
        "help guide\n\n"
        "**sosi:**\n"
        "`/play [song or link]`\n"
        "gandon: /play shape of you\n\n"
        "**controls:**\n"
        "`/pause` - pause\n"
        "`/resume` - resume\n"
        "`/skip` - skip\n"
        "`/stop` or `/end` - stop\n\n"
        "**queue:**\n"
        "`/queue` - view queue\n\n"
        "**tips:**\n"
        "• use youtube links\n"
        "• use inline buttons\n"
        "• bot stays in call\n\n"
        "contact: @jumpfool"
    )
    await q.message.reply(help_text)


@app.on_message(filters.command("ban") & filters.user(admin_id))
async def ban_handler(_, m: Message):
    if len(m.command) < 2:
        return
    try:
        target = m.command[1]
        user_obj = await app.get_users(target)
        ban_users.add(user_obj.id)
        await m.reply(f"banned {user_obj.id}")
    except Exception as e:
        await m.reply(f"error: {str(e).lower()}")


@app.on_message(filters.command("unban") & filters.user(admin_id))
async def unban_handler(_, m: Message):
    if len(m.command) < 2:
        return
    try:
        target = m.command[1]
        user_obj = await app.get_users(target)
        if user_obj.id in ban_users:
            ban_users.remove(user_obj.id)
            await m.reply(f"unbanned {user_obj.id}")
        else:
            await m.reply("not banned")
    except Exception as e:
        await m.reply(f"error: {str(e).lower()}")


@app.on_message(filters.command("search"))
async def search_handler(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    if len(m.command) < 2:
        return await m.reply("usage: `/search [song]`")

    query = m.text.split(None, 1)[1]
    msg = await m.reply("**searching...**")

    try:
        results = await asyncio.to_thread(search_youtube, query)
        if not results:
            return await msg.edit("no results found")

        text = "**search results**\n\n"
        for i, res in enumerate(results, 1):
            text += f"{i}. {res['title'].lower()} ({res['duration']})\n"
        await msg.edit(text)
    except Exception as e:
        await msg.edit(f"error: {str(e).lower()}")


@app.on_message(filters.command("play"))
async def play(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return

    parts = m.text.split(None, 2)
    if len(parts) < 2:
        return await m.reply("usage: `/play [group_id] [song]` or `/play [song]`")

    if parts[1].startswith(("-", "@")):
        try:
            target_chat = await app.get_chat(parts[1])
            cid = target_chat.id
            if len(parts) < 3:
                return await m.reply("usage: `/play [group_id] [song]`")
            q = parts[2]
        except:
            cid = m.chat.id
            q = m.text.split(None, 1)[1]
    else:
        cid = m.chat.id
        q = m.text.split(None, 1)[1]

    msg = await m.reply("**searching...**")

    try:
        try:
            target_chat = await app.get_chat(cid)
            if target_chat.type in ["group", "supergroup"]:
                if not await ensure_assistant_joined(cid):
                    return await msg.edit("bot needs admin to invite assistant")
        except:
            if cid != m.chat.id:
                return await msg.edit("bot is not in that group or id is wrong")

        await msg.edit("**downloading...**")
        song = await asyncio.to_thread(download_audio, q)

        if cid not in queues:
            queues[cid] = []

        if cid not in active:
            try:
                stream = AudioPiped(song["file"], HighQualityAudio())
                await calls.join_group_call(cid, stream)
                active[cid] = song
                await msg.delete()
                await send_now_playing(cid, song, [])
                logger.info(f"Started: {song['title']}")
            except GroupCallNotFound:
                await msg.edit("**no active voice chat found**")
            except Exception as e:
                logger.error(f"play error: {e}")
                await msg.edit(f"error: {str(e).lower()}")
        else:
            queues[cid].append(song)
            await msg.edit(
                f"queued: {song['title'][:50].lower()}\nposition: {len(queues[cid])}"
            )
    except Exception as e:
        logger.error(f"Command error: {e}")
        await msg.edit(f"❌ {str(e)[:100]}")


@app.on_message(filters.command("skip"))
async def skip(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    cid = m.chat.id
    if uid == admin_id and len(m.command) > 1:
        try:
            target_chat = await app.get_chat(m.command[1])
            cid = target_chat.id
        except:
            pass
    if cid in active:
        await m.reply("**skipped**")
        await play_next(cid)
    else:
        await m.reply("not playing")


@app.on_message(filters.command("pause"))
async def pause(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    cid = m.chat.id
    if uid == admin_id and len(m.command) > 1:
        try:
            target_chat = await app.get_chat(m.command[1])
            cid = target_chat.id
        except:
            pass
    try:
        await calls.pause_stream(cid)
        await m.reply("**paused**")
    except:
        await m.reply("not playing")


@app.on_message(filters.command("resume"))
async def resume(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    cid = m.chat.id
    if uid == admin_id and len(m.command) > 1:
        try:
            target_chat = await app.get_chat(m.command[1])
            cid = target_chat.id
        except:
            pass
    try:
        await calls.resume_stream(cid)
        await m.reply("**resumed**")
    except:
        await m.reply("not paused")


@app.on_message(filters.command(["stop", "end"]))
async def stop(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    cid = m.chat.id
    if uid == admin_id and len(m.command) > 1:
        try:
            target_chat = await app.get_chat(m.command[1])
            cid = target_chat.id
        except:
            pass
    try:
        await calls.leave_group_call(cid)
        if cid in queues:
            queues[cid].clear()
        if cid in active:
            del active[cid]
        await m.reply("**stopped**")
    except:
        await m.reply("not in call")


@app.on_message(filters.command("queue"))
async def queue(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return
    cid = m.chat.id
    if uid == admin_id and len(m.command) > 1:
        try:
            target_chat = await app.get_chat(m.command[1])
            cid = target_chat.id
        except:
            pass
    if cid not in active:
        return await m.reply("nothing playing")
    text = "**queue**\n\n"
    if cid in queues and queues[cid]:
        for i, s in enumerate(queues[cid], 1):
            text += f"{i}. {s['title'].lower()}\n"
    else:
        text += "empty"
    await m.reply(text)


@calls.on_stream_end()
async def on_end(_, u: Update):
    logger.info(f"Stream ended in {u.chat_id}")
    await play_next(u.chat_id)


async def main():
    await app.start()
    await user.start()
    await calls.start()
    logger.info("🎵 LIVE!")
    await idle()


if __name__ == "__main__":
    app.run(main())
