# musicbot 🦀

a modular telegram music bot using pyrogram and pytgcalls.

## features
- 🎵 stream audio from youtube in voice chats
- ⚡️ concurrent background downloading
- 🎚 playback speed control (speedup/slowed)
- 📻 radio mode: fetch similar tracks based on current playback
- 🛡 admin controls: ban/unban users

## installation

### local
1. clone the repo:
   ```bash
   git clone https://github.com/jumpfool/musicbot.git
   cd musicbot
   ```
2. install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. create a `.env` file with your credentials:
   ```env
   API_ID=your_api_id
   API_HASH=your_api_hash
   BOT_TOKEN=your_bot_token
   SESSION=your_string_session
   ADMIN_ID=your_id
   ```
4. run the bot:
   ```bash
   python bot.py
   ```

### docker
```bash
docker build -t musicbot .
docker run -d --env-file .env musicbot
```

## commands
- `/play [song name or link]` - play a song
- `/skip` - skip current track
- `/pause` / `/resume` / `/stop` - playback control
- `/queue` - view current queue
- `/nowplaying` - show current track with progress
- `/radio [n]` - add `n` similar tracks to queue
- `/speedup` / `/slowed` / `/restore` - change playback speed (admin only)
- `/ban` / `/unban` - manage users (admin only)
