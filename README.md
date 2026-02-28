# SingerBot

SingerBot is a Telegram bot that streams audio from YouTube into Telegram voice chats. It provides a complete music streaming solution for group voice calls with queue management, playback controls, and audio effects.

## Features

- Stream music from YouTube URLs or search queries
- Queue management with support for multiple chats
- Playback controls: play, pause, resume, skip, stop
- Radio mode - automatically queues similar tracks based on the current song
- Audio effects: speedup (1.2x), slowed (0.85x), and restore to normal speed
- User banning system for access control
- Cross-chat administration - control playback in other groups
- Inline keyboard controls for easy interaction
- Search functionality for finding YouTube videos

## Requirements

- Python 3.11
- Telegram API credentials (API_ID, API_HASH)
- Bot token from @BotFather
- FFmpeg
- YouTube cookies (optional, for rate-limited environments)

## Installation

### Using Docker (Recommended)

1. Build the Docker image:
   ```bash
   docker build -t singerbot .
   ```

2. Run the container:
   ```bash
   docker run -d --name singerbot \
     --env-file .env \
     -v /path/to/cache:/tmp/singerbot_cache \
     -v $(pwd)/cookies.txt:/app/cookies.txt \
     singerbot
   ```

   Note: The cookies.txt volume mount is optional and only needed for YouTube access in rate-limited environments.

### Manual Installation

1. Install system dependencies:
   ```bash
   apt-get update
   apt-get install -y ffmpeg python3.11 python3-pip
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables in `.env`:
   ```
   API_ID=your_api_id
   API_HASH=your_api_hash
   BOT_TOKEN=your_bot_token
   SESSION=your_session_string
   ADMIN_ID=your_user_id
   ```

4. (Optional but recommended) Configure YouTube cookies:
   - Place a `cookies.txt` file in the project root, or
   - Set `COOKIES_FILE=/path/to/cookies.txt` in `.env`

5. Run the bot:
   ```bash
   python bot.py
   ```

   The bot will log the cookies file status on startup. If you see "✗ Cookies file not found", YouTube may block your requests.

## Getting Telegram Credentials

1. **API ID and Hash**: Get from [my.telegram.org](https://my.telegram.org)
2. **Bot Token**: Create via [@BotFather](https://t.me/BotFather)
3. **Session String**: Run `python generate_session.py` and follow the prompts

## Commands

| Command | Description |
|---------|-------------|
| `/play [song]` | Play a song by name or URL |
| `/skip` | Skip the current track |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/stop` | Stop playback and clear queue |
| `/queue` | View the current queue |
| `/search [query]` | Search for songs on YouTube |
| `/radio` | Toggle radio mode (auto-queue similar tracks) |
| `/speedup` | Speed up playback (admin only) |
| `/slowed` | Slow down playback (admin only) |
| `/restore` | Restore normal playback speed (admin only) |
| `/ban [user]` | Ban a user from using the bot (admin only) |
| `/unban [user]` | Unban a user (admin only) |

## Configuration

The following environment variables can be configured:

| Variable | Description | Default |
|----------|-------------|---------|
| `API_ID` | Telegram API ID | - |
| `API_HASH` | Telegram API Hash | - |
| `BOT_TOKEN` | Telegram Bot Token | - |
| `SESSION` | User session string | - |
| `ADMIN_ID` | Admin user ID | - |
| `LOG_GROUP` | Log group chat ID | - |
| `RADIO_BATCH` | Number of tracks to fetch in radio mode | 25 |
| `COOKIES_FILE` | Path to YouTube cookies.txt file | `./cookies.txt` |
| `YOUTUBE_COOKIES` | Alternative name for cookies file path | `./cookies.txt` |
| `YOUTUBE_CLIENT` | yt-dlp player client (`mweb`, `tv_simply`, `tv`, `web`, `android`, `default`, …) | `mweb` |
| `YOUTUBE_PO_TOKEN` | PO Token for YouTube requests (advanced, format: `CLIENT.TYPE+TOKEN`) | - |
| `YOUTUBE_JS_RUNTIME` | JS runtime for yt-dlp signature solving (`node`, `deno`, `node:/path`, or JSON dict) | `node` |

### YouTube Authentication

YouTube aggressively blocks server-side requests without proper authentication. If you see errors like **"Sign in to confirm you're not a bot"** or **"Signature solving failed"**, you need to configure cookies. The bot uses the `mweb` player client by default, which works well with cookies.

#### Cookies (required for most deployments)

**Option 1: Place cookies.txt in project root (easiest)**
```bash
# 1. Install "Get cookies.txt LOCALLY" browser extension
# 2. Open a private/incognito window and log into YouTube
# 3. Navigate to https://www.youtube.com/robots.txt in the same tab
# 4. Export cookies using the extension, then close the incognito window
# 5. Save the content as cookies.txt in the project root
# 6. Restart the bot
```

> **Important:** Export cookies from an incognito window that you immediately close afterwards. YouTube rotates cookies on open tabs, which can invalidate exported cookies. See the [yt-dlp cookie guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies) for details.

**Option 2: Use environment variable**
```bash
# In .env file
COOKIES_FILE=/path/to/your/cookies.txt
# or
YOUTUBE_COOKIES=/path/to/your/cookies.txt
```

**Option 3: Docker volume mount**
```bash
docker run -d --name singerbot \
  --env-file .env \
  -v $(pwd)/cookies.txt:/app/cookies.txt \
  singerbot
```

The bot will log the cookies file status on startup:
- ✓ Cookies file found and accessible
- ✗ Cookies file not found (YouTube may block requests)

See `cookies.txt.example` for more details on the cookie format.

#### Player Client

The `YOUTUBE_CLIENT` env var controls which yt-dlp player client is used. The default `mweb` works with cookies and without a JS runtime. **Do not use `tv_simply` or `tv_downgraded` when cookies are present** — these clients do not support cookies and yt-dlp will skip them entirely, leaving no usable audio formats. The bot will automatically switch these to `mweb` when a cookies file is detected. Set to `default` to let yt-dlp choose automatically (requires Node.js or Deno).

#### JavaScript Runtime

The Docker image includes Node.js, which yt-dlp uses to solve YouTube's JS signature challenges. This is configured via `YOUTUBE_JS_RUNTIME=node` (the default). If you need a custom path, set `YOUTUBE_JS_RUNTIME=node:/path/to/node` or pass a JSON dict like `{"node": {"path": "/path/to/node"}}`. If Node.js is not available in your environment, set `YOUTUBE_JS_RUNTIME=` (empty) to disable it — the `mweb` client will still work without JS.

## Architecture

- `bot.py` - Entry point
- `singerbot/core.py` - Client initialization
- `singerbot/config.py` - Configuration
- `singerbot/state.py` - In-memory state
- `singerbot/handlers.py` - Command handlers
- `singerbot/utils.py` - Helper functions

## Troubleshooting

### "Sign in to confirm you're not a bot" Error

This error means YouTube has detected automated access and requires authentication. To fix:

1. **Ensure cookies are configured** - See the [YouTube Authentication](#youtube-authentication) section
2. **Update your cookies** - Cookies expire periodically; re-export them from your browser
3. **Use a fresh YouTube account** - Some accounts may have restrictions
4. **Try incognito method** - Export cookies from an incognito window and close it immediately
5. **Check yt-dlp version** - Run `pip show yt-dlp` and update if outdated: `pip install -U yt-dlp`

### "Signature solving failed" / "n challenge solving failed"

This indicates the JavaScript runtime isn't working correctly:

1. **Verify Node.js is installed** - Run `node --version`
2. **Check YOUTUBE_JS_RUNTIME** - Should be `node` or a valid path
3. **Use mweb client** - Set `YOUTUBE_CLIENT=mweb` (works without JS runtime)

### "Only images are available" Error

The video might not have audio or the format isn't available:

1. **Check the video** - Verify it has audio on YouTube's website
2. **Try a different video** - Some videos are audio-only streams
3. **Check client compatibility** - Try `YOUTUBE_CLIENT=web_safari`

### Downloads Are Slow or Failing

1. **Use cookies** - Most reliable fix for rate limiting
2. **Check your IP** - Your server IP might be flagged by YouTube
3. **Consider a proxy** - Configure yt-dlp proxy options if needed
4. **Reduce concurrent downloads** - Limit radio mode batch size with `RADIO_BATCH=10`

## License

MIT License - see LICENSE file for details.
