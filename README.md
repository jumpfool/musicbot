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
     singerbot
   ```

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

4. Run the bot:
   ```bash   python bot.py
   ```

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
| `YOUTUBE_COOKIES` | YouTube cookies (optional) | - |

## Architecture

- `bot.py` - Entry point
- `singerbot/core.py` - Client initialization
- `singerbot/config.py` - Configuration
- `singerbot/state.py` - In-memory state
- `singerbot/handlers.py` - Command handlers
- `singerbot/utils.py` - Helper functions

## License

MIT License - see LICENSE file for details.
