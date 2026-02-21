# Use an official lightweight Python image
FROM python:3.11-slim

# Keep Python output unbuffered (helpful for logging)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install system dependencies needed for audio / building extensions
# - ffmpeg: required by yt-dlp for audio extraction/processing
# - libopus: required for voice libraries (pytgcalls / libopus)
# - build-essential, gcc, python3-dev: in case any requirements need compiling
# - git: sometimes needed if a dependency is installed from git
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       ffmpeg \
       libopus0 \
       libopus-dev \
       build-essential \
       gcc \
       python3-dev \
       git \
       ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the bot
RUN useradd --create-home --shell /bin/bash botuser
USER botuser
WORKDIR /home/botuser/app

# Copy and install Python dependencies first (leverage Docker layer caching)
# The project root includes requirements.txt
COPY --chown=botuser:botuser requirements.txt ./

# Use pip to install; adjust flags to speed up and reduce image size.
RUN python -m pip install --upgrade pip \
  && python -m pip install --upgrade setuptools \
  && python -m pip install -r requirements.txt

# Copy application code
# We intentionally don't copy secrets like .env (user should provide at runtime)
COPY --chown=botuser:botuser . .

# Expose no ports by default — the bot connects to Telegram, not listened ports.
# If your bot exposes a webserver for healthchecks/webhooks, uncomment and change:
# EXPOSE 8080

# Default environment variables (can be overridden at runtime)
ENV PYTGCALL_LOG_LEVEL=warning \
    TZ=UTC

# Recommended non-root runtime and working directory already set.
# Run the bot. Change to the correct entry point if your main file is different.
# This assumes `bot.py` is the entrypoint in the project root (as in this repo).
CMD ["python", "bot.py"]

# Healthcheck (optional) - this only checks that the Python process is running;
# for more advanced checks, implement an HTTP health endpoint in your bot.
# Note: healthcheck runs as root in many runtimes; you can remove it if undesired.
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python.*bot.py" || exit 1

# Notes:
# - Provide your .env at runtime (do NOT bake secrets into the image)
#   Example run:
#     docker build -t musicbot:latest -f MUSICBOT/Dockerfile .
#     docker run --rm --env-file .env -v /path/to/sessions:/home/botuser/app/sessions musicbot:latest
#
# - If you need additional system libs (e.g. libsodium), add them to apt-get install.
# - If any Python requirement provides prebuilt wheels for your platform, you can
#   remove build tools (build-essential, gcc, python3-dev) after installation to shrink image—
#   but that requires an extra multi-stage build. If you'd like that, I can provide it.
