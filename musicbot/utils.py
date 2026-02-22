import asyncio
import logging
import os
import re
import uuid
import shlex
import time
import yt_dlp

from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pytgcalls.exceptions import GroupCallNotFound
from pytgcalls.types import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio

from musicbot.config import DOWNLOADS_DIR, RADIO_BATCH
from musicbot.state import queues, active, ban_users
from musicbot.core import app, user, calls, logger

def video_id_from_url(url: str):
    if not url:
        return None
    m = re.search(r"(?:v=|youtu\.be/|/watch\?v=)([0-9A-Za-z_-]{11})", url)
    if m:
        return m.group(1)
    m2 = re.search(r"([0-9A-Za-z_-]{11})", url)
    return m2.group(1) if m2 else None

def fetch_radio_ids(video_id: str, max_items: int = RADIO_BATCH):
    if not video_id:
        return []
    radio_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(radio_url, download=False)
            ids = []
            for e in info.get("entries", [])[:max_items]:
                vid = e.get("id") or e.get("url")
                if not vid:
                    continue
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
        "outtmpl": f"{DOWNLOADS_DIR}/%(id)s.%(ext)s",
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
            "is_pending": False
        }

async def process_radio_batch(items):
    """
    Background worker to download items 2 at a time.
    Updates the dictionaries in-place.
    """
    sem = asyncio.Semaphore(2)

    async def worker(item):
        async with sem:
            try:
                # If item is cancelled or no longer needed, we could check here
                info = await asyncio.to_thread(download_audio, item['url'])
                item.update(info)
                item['is_pending'] = False
            except Exception as e:
                logger.error(f"Failed to download pending radio item {item['url']}: {e}")
                item['title'] = "Error loading track"
                item['is_pending'] = False
                item['file'] = None

    tasks = [worker(i) for i in items]
    await asyncio.gather(*tasks)

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
        f"**artist :** {song.get('artist', 'unknown')}\n"
        f"**duration :** {format_duration(song.get('duration', 0))}\n\n"
    )
    if queue_list:
        caption += "**up next:**\n\n"
        for i, s in enumerate(queue_list[:5], 1):
            t = s.get('title', 'loading...')
            caption += f"{i}. {t}\n"
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
    return os.path.join(DOWNLOADS_DIR, f"{name}_{suffix}_{uniq}{ext}")

def get_current_orig_position(state: dict) -> float:
    base = state.get("base_orig_offset", 0.0)
    stream_start = state.get("stream_start_time", time.time())
    play_factor = state.get("play_factor", 1.0)
    if state.get("paused"):
        paused_at = state.get("paused_at", stream_start)
        elapsed = max(0.0, paused_at - stream_start)
    else:
        elapsed = max(0.0, time.time() - stream_start)
    return base + (elapsed * float(play_factor))

async def _run_ffmpeg_transform_seek_orig(orig_path: str, out_path: str, factor: float, seek: float, timeout: int = 120):
    atempo = max(0.5, min(2.0, factor))
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

def _init_active_state_for_song(song: dict):
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
    
    # Peek first item
    s = queues[cid][0]
    
    # Wait if pending
    while s.get("is_pending"):
        await asyncio.sleep(1)
        # Re-check queue existence in case cleared
        if cid not in queues or not queues[cid]:
            return
        # If item removed/changed, update ref
        s = queues[cid][0]

    # Pop ready item
    s = queues[cid].pop(0)

    # Check if failed
    if not s.get("file"):
        logger.warning(f"Skipping failed/pending item in {cid}")
        await play_next(cid)
        return

    try:
        state = _init_active_state_for_song(s)
        stream = AudioPiped(state["file"], HighQualityAudio())
        await calls.change_stream(cid, stream)
        active[cid] = state
        await send_now_playing(cid, state, queues.get(cid, []))
        logger.info(f"Playing: {state['title']}")
    except Exception as e:
        logger.error(f"play next error: {e}")
        await play_next(cid)
