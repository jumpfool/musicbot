#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import uuid
import shlex
import time

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
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION = os.getenv("SESSION", "")
LOG_GROUP = int(os.getenv("LOG_GROUP", "-1003387540146"))

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
calls = PyTgCalls(user)

queues = {}
active = {}
downloads_dir = "/tmp/music_cache"
os.makedirs(downloads_dir, exist_ok=True)

# Radio mode: set of chat ids where radio mode is enabled.
# When enabled, the bot will fetch YouTube "Radio/Mix" playlists
# based on the currently playing video and enqueue similar tracks.
radio_mode = set()
# How many video ids to fetch per radio batch (can be tuned via env)
RADIO_BATCH = int(os.getenv("RADIO_BATCH", "25"))

ban_users = set()
admin_id = int(os.getenv("ADMIN_ID", "0"))


def video_id_from_url(url: str):
    """
    Extract a 11-char YouTube video id from common URL forms.
    Returns None if not found.
    """
    if not url:
        return None
    # common patterns: v=VIDEO_ID, youtu.be/VIDEO_ID, /watch?v=VIDEO_ID
    m = re.search(r"(?:v=|youtu\\.be/|/watch\\?v=)([0-9A-Za-z_-]{11})", url)
    if m:
        return m.group(1)
    # fallback: try to find any 11-char candidate
    m2 = re.search(r"([0-9A-Za-z_-]{11})", url)
    return m2.group(1) if m2 else None


def fetch_radio_ids(video_id: str, max_items: int = RADIO_BATCH):
    """
    Given a seed YouTube video_id, construct the YouTube 'Radio/Mix' URL:
    https://www.youtube.com/watch?v=VIDEO_ID&list=RDVIDEO_ID
    Use yt-dlp in 'flat' mode to retrieve up to `max_items` video ids.
    Returns a list of video ids (strings).
    """
    if not video_id:
        return []
    radio_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(radio_url, download=False)
            ids = []
            for e in info.get("entries", [])[:max_items]:
                # entry could have 'id' or 'url'
                vid = e.get("id") or e.get("url")
                if not vid:
                    continue
                # normalize to 11-char id if possible
                if len(vid) == 11:
                    ids.append(vid)
                else:
                    maybe = video_id_from_url(vid)
                    if maybe:
                        ids.append(maybe)
            return ids
    except Exception as exc:
        logger.error(f"fetch_radio_ids failed for seed {video_id}: {exc}")
        return []


async def ensure_radio_filled(cid):
    """
    Ensure that if radio_mode is enabled for `cid`, its queue has some upcoming tracks.
    This will fetch a batch of similar video ids based on the current playing item's webpage
    and download them (using existing download_audio) and append to queues[cid].
    """
    if cid not in radio_mode:
        return
    # ensure queue exists
    if cid not in queues:
        queues[cid] = []

    # if there are already enough queued items, do nothing
    if len(queues[cid]) >= 5:
        return

    # decide seed video id: prefer the currently active song's webpage url
    seed_vid = None
    if cid in active:
        seed_vid = video_id_from_url(active[cid].get("webpage"))
    # otherwise, if there's a queued item, use its webpage
    if not seed_vid and queues[cid]:
        seed_vid = video_id_from_url(queues[cid][0].get("webpage"))

    if not seed_vid:
        return

    try:
        ids = await asyncio.to_thread(fetch_radio_ids, seed_vid, RADIO_BATCH)
        # download sequentially (could be optimized to parallel later)
        for vid in ids:
            # small safety cap to avoid infinite growth
            if len(queues[cid]) >= 200:
                break
            try:
                url = f"https://www.youtube.com/watch?v={vid}"
                song = await asyncio.to_thread(download_audio, url)
                queues[cid].append(song)
            except Exception as e:
                logger.warning(f"radio download failed for {vid}: {e}")
    except Exception as e:
        logger.error(f"ensure_radio_filled error for {cid}: {e}")


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
        return re.sub(r"\s*(music|vevo|official).*$", "", uploader, flags=re.IGNORECASE).strip()
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
            "thumb": i.get("thumbnail") or "https://telegra.ph/file/2f7debf856695e0a17296.png",
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
            [InlineKeyboardButton("pause", callback_data="pause"), InlineKeyboardButton("resume", callback_data="resume")],
            [InlineKeyboardButton("skip", callback_data="skip"), InlineKeyboardButton("stop", callback_data="end")],
        ]
    )

    try:
        if song.get("thumb"):
            await app.send_photo(cid, song["thumb"], caption=caption, reply_markup=buttons)
        else:
            await app.send_photo(cid, "https://telegra.ph/file/2f7debf856695e0a17296.png", caption=caption, reply_markup=buttons)
    except Exception as e:
        logger.warning(f"Photo send failed: {e}, using text")
        await app.send_message(cid, caption, reply_markup=buttons)


def _make_transformed_filename(src: str, suffix: str):
    base = os.path.basename(src)
    name, ext = os.path.splitext(base)
    uniq = uuid.uuid4().hex[:8]
    return os.path.join(downloads_dir, f"{name}_{suffix}_{uniq}{ext}")


# Core helper: compute current position (seconds) relative to original file
def get_current_orig_position(state: dict) -> float:
    """
    state: active[cid] dict with keys:
      - base_orig_offset: float seconds into original mapping to start of current file
      - stream_start_time: epoch when current file started
      - paused: bool
      - paused_at: epoch if paused
      - play_factor: current playback speed factor applied to this stream (1.0 = normal)
    Returns a float number of seconds (>=0) of how many seconds into the original file we are currently.
    """
    base = state.get("base_orig_offset", 0.0)
    stream_start = state.get("stream_start_time", time.time())
    play_factor = state.get("play_factor", 1.0)
    if state.get("paused"):
        paused_at = state.get("paused_at", stream_start)
        elapsed = max(0.0, paused_at - stream_start)
    else:
        elapsed = max(0.0, time.time() - stream_start)
    # elapsed is seconds of the current file; to convert to seconds in the original file,
    # multiply by the play_factor. For example, playing 10s on a 1.2x file maps to 12s in original.
    return base + (elapsed * float(play_factor))


# FFmpeg transform function that seeks in the original file and applies pitch+tempo change.
async def _run_ffmpeg_transform_seek_orig(orig_path: str, out_path: str, factor: float, seek: float, timeout: int = 120):
    """
    Creates out_path by taking orig_path starting at `seek` seconds, applying pitch+tempo shift (factor).
    factor>1 speeds up/pitches up, factor<1 slows/pitches down.
    We use: -ss <seek> -i orig_path -af "asetrate=44100*factor,aresample=44100,atempo=atempo" out_path
    """
    # clamp atempo to ffmpeg's 0.5-2.0 range; if factor outside that range the atempo will be clamped.
    atempo = max(0.5, min(2.0, factor))
    # asetrate will shift pitch (and change sample rate), then we resample back to 44100 and apply atempo to adjust speed.
    asetrate_expr = f"asetrate=44100*{factor}"
    af_filter = f"{asetrate_expr},aresample=44100,atempo={atempo}"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(float(seek)),
        "-i",
        orig_path,
        "-af",
        af_filter,
        "-vn",
        out_path,
    ]
    logger.info(f"Running ffmpeg: {' '.join(shlex.quote(p) for p in cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise Exception("ffmpeg timed out")
        if proc.returncode != 0:
            err = stderr.decode(errors="ignore").strip()
            raise Exception(f"ffmpeg error: {err}")
        return out_path
    except Exception as e:
        logger.error(f"FFmpeg transform failed: {e}")
        raise


# Ensure active state initialization when we start playing a track
def _init_active_state_for_song(song: dict):
    """
    song is the dict returned by download_audio (with 'file','title', etc.)
    Returns a new state dict to be stored in active[cid].
    Fields:
      - orig_file: path to original full file (do not overwrite)
      - file: current file path being played (initially same as orig_file)
      - base_orig_offset: which second in orig_file corresponds to the start of `file` (initially 0)
      - stream_start_time: epoch when current file started
      - paused: bool
      - play_factor: playback speed factor applied to the current file relative to the original
    """
    return {
        "orig_file": song["file"],
        "file": song["file"],
        "title": song.get("title", "unknown"),
        "artist": song.get("artist", "unknown"),
        "duration": song.get("duration", 0),
        "thumb": song.get("thumb"),
        "webpage": song.get("webpage"),
        "base_orig_offset": 0.0,
        "stream_start_time": time.time(),
        "paused": False,
        "paused_at": None,
        "play_factor": 1.0,
    }


async def play_next(cid):
    if cid not in queues or not queues[cid]:
        logger.info(f"Queue empty in {cid}")
        if cid in active:
            del active[cid]
        return

    s = queues[cid].pop(0)
    try:
        # initialize state
        state = _init_active_state_for_song(s)
        stream = AudioPiped(state["file"], HighQualityAudio())
        await calls.change_stream(cid, stream)
        active[cid] = state
        await send_now_playing(cid, state, queues.get(cid, []))
        logger.info(f"Playing: {state['title']}")
        # If radio mode is enabled for this chat, asynchronously ensure the queue is topped up.
        # We await here to keep behavior deterministic; it downloads a batch if needed.
        try:
            await ensure_radio_filled(cid)
        except Exception as e:
            logger.warning(f"ensure_radio_filled failed in play_next for {cid}: {e}")
    except Exception as e:
        logger.error(f"play next error: {e}")
        await play_next(cid)


@app.on_callback_query()
async def callback_handler(_, query: CallbackQuery):
    # Determine user id from the callback. Prefer query.from_user, fall back to the original message sender.
    uid = None
    if query.from_user:
        uid = query.from_user.id
    elif query.message and getattr(query.message, "from_user", None):
        uid = query.message.from_user.id

    # If the user is banned, return an alert so they know they cannot interact.
    if uid and is_banned(uid):
        try:
            await query.answer("You are banned and cannot use this bot.", show_alert=True)
        except:
            # If alert fails for any reason, at least acknowledge the callback silently.
            try:
                await query.answer()
            except:
                pass
        return

    data = query.data
    cid = query.message.chat.id
    name = query.from_user.first_name.lower() if query.from_user else "unknown"

    if data == "pause":
        try:
            await calls.pause_stream(cid)
            # update internal state
            if cid in active and not active[cid].get("paused"):
                active[cid]["paused"] = True
                active[cid]["paused_at"] = time.time()
            await query.answer("paused", show_alert=False)
            await app.send_message(cid, f"{name} paused")
        except:
            await query.answer("cant pause", show_alert=True)

    elif data == "resume":
        try:
            await calls.resume_stream(cid)
            # update internal state
            if cid in active and active[cid].get("paused"):
                paused_at = active[cid].pop("paused_at", None)
                if paused_at:
                    elapsed = max(0.0, paused_at - active[cid].get("stream_start_time", paused_at))
                    # resume so that stream_start_time reflects that elapsed time has already been consumed
                    active[cid]["stream_start_time"] = time.time() - elapsed
                active[cid]["paused"] = False
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
            [InlineKeyboardButton("add to group", url="https://t.me/MUSlCXBOT?startgroup=true")],
            [InlineKeyboardButton("commands", callback_data="help"), InlineKeyboardButton("owner", url="https://t.me/Vclub_Tech")],
        ]
    )

    text = (
        "**music bot**\n\n"
        "use /play to start streaming.\n\n"
        "commands:\n"
        "• /play [song]\n"
        "• /skip\n"
        "• /pause\n"
        "• /resume\n"
        "• /stop\n"
        "• /queue\n"
        "• /speedup (admin)\n"
        "• /slowed (admin)\n"
        "• /radio - toggle radio mode (auto-queue similar tracks)\n"
    )

    try:
        await m.reply_photo("https://telegra.ph/file/2f7debf856695e0a17296.png", caption=text, reply_markup=buttons)
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
        "`/play [song or link]`\n"
        "`/skip` - skip\n"
        "`/pause` - pause\n"
        "`/resume` - resume\n"
        "`/stop` - stop\n"
        "`/queue` - view queue\n"
        "`/speedup` - pitch up & speed up (admin only)\n"
        "`/slowed` - pitch down & slow down (admin only)\n"
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
                # initialize state so we can track seamless offsets
                state = _init_active_state_for_song(song)
                stream = AudioPiped(state["file"], HighQualityAudio())
                try:
                    # try joining; some backends may raise if the assistant is already in the call.
                    await calls.join_group_call(cid, stream)
                except Exception as e:
                    # If the join failed because we're already in the call, switch the stream instead.
                    # Check the exception message for common phrases; if it's a different error, re-raise.
                    msg_err = str(e).lower()
                    if "already joined" in msg_err or "already in group call" in msg_err or "already joined into group call" in msg_err:
                        logger.info(f"Assistant already in call for {cid}, using change_stream to start playback")
                        await calls.change_stream(cid, stream)
                    else:
                        raise
                active[cid] = state
                await msg.delete()
                await send_now_playing(cid, state, [])
                logger.info(f"Started: {state['title']}")
            except GroupCallNotFound:
                await msg.edit("**no active voice chat found**")
            except Exception as e:
                logger.error(f"play error: {e}")
                await msg.edit(f"error: {str(e).lower()}")
        else:
            queues[cid].append(song)
            await msg.edit(f"queued: {song['title'][:50].lower()}\nposition: {len(queues[cid])}")
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
        # update internal state
        if cid in active and not active[cid].get("paused"):
            active[cid]["paused"] = True
            active[cid]["paused_at"] = time.time()
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
        # update internal state
        if cid in active and active[cid].get("paused"):
            paused_at = active[cid].pop("paused_at", None)
            if paused_at is not None:
                elapsed = max(0.0, paused_at - active[cid].get("stream_start_time", paused_at))
                # set stream_start_time such that elapsed is preserved
                active[cid]["stream_start_time"] = time.time() - elapsed
            active[cid]["paused"] = False
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


@app.on_message(filters.command("radio"))
async def radio_handler(_, m: Message):
    """
    toggle radio mode for the chat. when enabled, similar tracks will be fetched
    and appended to the queue immediately. usage: /radio
    admins can target a specific group by providing its username/id as the first argument.
    all user-visible text is lowercase.
    """
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return

    parts = m.text.split(None, 1)
    cid = m.chat.id
    # allow admin to toggle radio for a specified target chat (first arg)
    if len(parts) > 1 and uid == admin_id:
        try:
            target = await app.get_chat(parts[1])
            cid = target.id
        except Exception:
            # if parsing target fails, keep using current chat
            pass

    # if already enabled -> disable
    if cid in radio_mode:
        radio_mode.remove(cid)
        await m.reply("radio disabled for this chat")
        return

    # enable radio and immediately seed the queue with similar tracks.
    radio_mode.add(cid)
    # create or ensure queue exists
    queues.setdefault(cid, [])

    # send initial progress message (lowercase)
    progress_msg = await m.reply("radio: fetching similar tracks...")

    # determine seed video id: prefer active track, then first queued item
    seed_vid = None
    if cid in active:
        seed_vid = video_id_from_url(active[cid].get("webpage"))
    if not seed_vid and queues.get(cid):
        seed_vid = video_id_from_url(queues[cid][0].get("webpage"))

    if not seed_vid:
        # nothing to seed from; disable radio and inform user
        radio_mode.discard(cid)
        await progress_msg.edit("cannot enable radio: no reference youtube track found. start playing a youtube song first.")
        return

    try:
        # fetch a batch of candidate ids
        ids = await asyncio.to_thread(fetch_radio_ids, seed_vid, RADIO_BATCH)
        if not ids:
            radio_mode.discard(cid)
            await progress_msg.edit("radio: no similar tracks found")
            return

        added_titles = []
        total = len(ids)

        for idx, vid in enumerate(ids, 1):
            # if user disabled radio mid-seed, stop
            if cid not in radio_mode:
                break

            # basic dedupe: skip if same id already in queue (by webpage/id)
            skip = False
            for q in queues.get(cid, []):
                wp = (q.get("webpage") or "")
                if wp.endswith(vid) or vid in wp:
                    skip = True
                    break
            if skip:
                # update progress message to reflect skip
                await progress_msg.edit(f"radio: added {len(added_titles)}/{total} (skipping duplicate)\n\n" + ("\n".join(added_titles[-10:]) or ""))
                continue

            url = f"https://www.youtube.com/watch?v={vid}"
            try:
                song = await asyncio.to_thread(download_audio, url)
                queues[cid].append(song)
                title_lower = (song.get("title") or "unknown").lower()
                added_titles.append(title_lower)
                # show last up to 10 added titles
                last_list = "\n".join(f"{i}. {t}" for i, t in enumerate(added_titles[-10:], start=max(1, len(added_titles)-9)))
                await progress_msg.edit(f"radio: added {len(added_titles)}/{total}\n\n{last_list}")
            except Exception as e:
                logger.warning(f"radio download failed for {vid}: {e}")
                # still update progress so user sees ongoing activity
                await progress_msg.edit(f"radio: added {len(added_titles)}/{total} (errors may have occurred)\n\n" + ("\n".join(added_titles[-10:]) or ""))

            # small breathing pause to avoid hammering
            await asyncio.sleep(1)

        # final message after seeding
        if cid in radio_mode:
            if added_titles:
                await progress_msg.edit(f"radio enabled — added {len(added_titles)} tracks to queue")
            else:
                await progress_msg.edit("radio enabled — no tracks were added")
        else:
            await progress_msg.edit("radio disabled during seeding")

    except Exception as e:
        radio_mode.discard(cid)
        logger.error(f"radio_handler seed failed: {e}")
        try:
            await progress_msg.edit("radio failed to fetch tracks")
        except:
            pass

@calls.on_stream_end()
async def on_end(_, u: Update):
    logger.info(f"Stream ended in {u.chat_id}")
    await play_next(u.chat_id)


# -- speedup & slowed (seamless transforms) ---------------------------------
# Both commands are admin-only and will transform from the current playback position
# (calculated relative to the original downloaded file). The transformed file is
# produced by seeking into the original full file and outputting the remainder with
# applied pitch/tempo filters, then switching the stream. This makes the switch
# continue from the same logical time in the track (i.e. seamless).


@app.on_message(filters.command("speedup") & filters.user(admin_id))
async def speedup_handler(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return

    parts = m.text.split(None, 2)
    cid = m.chat.id
    # allow admin to target a group as first arg (username or -100... id)
    if len(parts) > 1:
        maybe = parts[1]
        if maybe.startswith(("-", "@")) or maybe.lstrip("-").isdigit():
            try:
                target = await app.get_chat(maybe)
                cid = target.id
            except:
                # treat it as non-chat arg
                pass

    if cid not in active:
        return await m.reply("nothing is playing in the target chat")

    notice = await m.reply("processing speedup... please wait (this may take a few seconds)")

    try:
        state = active[cid]
        # compute position in original file
        cur_pos = get_current_orig_position(state)
        orig = state.get("orig_file")
        if not orig or not os.path.exists(orig):
            await notice.delete()
            return await m.reply("original file not available for seamless transform")

        out = _make_transformed_filename(orig, "speedup")
        factor = 1.2  # 20% faster & pitched up
        await _run_ffmpeg_transform_seek_orig(orig, out, factor, seek=cur_pos, timeout=180)

        # switch stream to transformed file (which begins at original cur_pos)
        stream = AudioPiped(out, HighQualityAudio())
        await calls.change_stream(cid, stream)

        # update state: new file maps to the same orig offset (base_orig_offset = cur_pos)
        state["file"] = out
        state["base_orig_offset"] = float(cur_pos)
        state["stream_start_time"] = time.time()
        state["paused"] = False
        state["play_factor"] = float(factor)
        state["title"] = f"{state.get('title','unknown')} (speedup)"
        await notice.delete()

        # mention if replied to
        if m.reply_to_message and m.reply_to_message.from_user:
            ru = m.reply_to_message.from_user
            mention = f"[{ru.first_name}](tg://user?id={ru.id})"
            await m.reply(f"{mention} sped up", parse_mode="markdown")
    else:
        await m.reply("speedup applied")
        logger.info(f"Applied speedup in {cid}: {out} (seek {cur_pos}s)")
    except Exception as e:
        try:
            await notice.delete()
        except:
            pass
        logger.error(f"speedup failed: {e}")
        await m.reply(f"error applying speedup: {e}")


@app.on_message(filters.command("slowed") & filters.user(admin_id))
async def slowed_handler(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return

    parts = m.text.split(None, 2)
    cid = m.chat.id
    if len(parts) > 1:
        maybe = parts[1]
        if maybe.startswith(("-", "@")) or maybe.lstrip("-").isdigit():
            try:
                target = await app.get_chat(maybe)
                cid = target.id
            except:
                pass

    if cid not in active:
        return await m.reply("nothing is playing in the target chat")

    notice = await m.reply("processing slowed... please wait (this may take a few seconds)")

    try:
        state = active[cid]
        cur_pos = get_current_orig_position(state)
        orig = state.get("orig_file")
        if not orig or not os.path.exists(orig):
            await notice.delete()
            return await m.reply("original file not available for seamless transform")

        out = _make_transformed_filename(orig, "slowed")
        factor = 0.85  # 15% slower & pitched down
        await _run_ffmpeg_transform_seek_orig(orig, out, factor, seek=cur_pos, timeout=180)

        stream = AudioPiped(out, HighQualityAudio())
        await calls.change_stream(cid, stream)

        state["file"] = out
        state["base_orig_offset"] = float(cur_pos)
        state["stream_start_time"] = time.time()
        state["paused"] = False
        state["play_factor"] = float(factor)
        state["title"] = f"{state.get('title','unknown')} (slowed)"
        await notice.delete()

        if m.reply_to_message and m.reply_to_message.from_user:
            ru = m.reply_to_message.from_user
            mention = f"[{ru.first_name}](tg://user?id={ru.id})"
            await m.reply(f"{mention} slowed", parse_mode="markdown")
        else:
            await m.reply("slowed applied")
        logger.info(f"Applied slowed in {cid}: {out} (seek {cur_pos}s)")
    except Exception as e:
        try:
            await notice.delete()
        except:
            pass
        logger.error(f"slowed failed: {e}")
        await m.reply(f"error applying slowed: {e}")


# -- restore command (admin only) -------------------------------------------
# Produces a normal-speed stream starting at the current logical position
# by seeking into the original file and producing a normal-speed remainder,
# then switching the stream seamlessly.
@app.on_message(filters.command("restore") & filters.user(admin_id))
async def restore_handler(_, m: Message):
    uid = m.from_user.id if m.from_user else None
    if uid and is_banned(uid):
        return

    parts = m.text.split(None, 2)
    cid = m.chat.id
    # allow admin to target a group as first arg (username or -100... id)
    if len(parts) > 1:
        maybe = parts[1]
        if maybe.startswith(("-", "@")) or maybe.lstrip("-").isdigit():
            try:
                target = await app.get_chat(maybe)
                cid = target.id
            except:
                pass

    if cid not in active:
        return await m.reply("nothing is playing in the target chat")

    notice = await m.reply("restoring normal speed... please wait (this may take a few seconds)")

    try:
        state = active[cid]
        # compute position in original file
        cur_pos = get_current_orig_position(state)
        orig = state.get("orig_file")
        if not orig or not os.path.exists(orig):
            try:
                await notice.delete()
            except:
                pass
            return await m.reply("original file not available for restore")

        out = _make_transformed_filename(orig, "restored")
        factor = 1.0  # normal speed / pitch
        # use the seek-transform helper to create a normal-speed remainder starting at cur_pos
        await _run_ffmpeg_transform_seek_orig(orig, out, factor, seek=cur_pos, timeout=180)

        # switch stream to transformed file which begins at the logical current position
        stream = AudioPiped(out, HighQualityAudio())
        await calls.change_stream(cid, stream)

        # update state: new file maps to the same orig offset (base_orig_offset = cur_pos)
        state["file"] = out
        state["base_orig_offset"] = float(cur_pos)
        state["stream_start_time"] = time.time()
        state["paused"] = False
        state["play_factor"] = float(factor)
        # reset title to base title (strip known suffixes) and mark restored
        base_title = state.get("title", "unknown").split(" (")[0]
        state["title"] = f"{base_title} (restored)"
        try:
            await notice.delete()
        except:
            pass

        # mention if replied to
        if m.reply_to_message and m.reply_to_message.from_user:
            ru = m.reply_to_message.from_user
            mention = f"[{ru.first_name}](tg://user?id={ru.id})"
            await m.reply(f"{mention} restored to normal speed", parse_mode="markdown")
        else:
            await m.reply("restored to normal speed")
        logger.info(f"Restored normal speed in {cid}: {out} (seek {cur_pos}s)")
    except Exception as e:
        try:
            await notice.delete()
        except:
            pass
        logger.error(f"restore failed: {e}")
        await m.reply(f"error restoring: {e}")


async def main():
    await app.start()
    await user.start()
    await calls.start()
    logger.info("live")
    await idle()


if __name__ == "__main__":
    app.run(main())
