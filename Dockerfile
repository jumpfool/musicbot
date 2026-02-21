# Use an official lightweight Python image
FROM python:3.11-slim

# Keep Python output unbuffered (helpful for logging)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install system dependencies and Node.js (>=15)
# - curl/gnupg/ca-certificates: needed to add NodeSource repo securely
# - ffmpeg: required by yt-dlp for audio extraction/processing
# - libopus: required for voice libraries (pytgcalls / libopus)
# - build-essential, gcc, python3-dev: in case any requirements need compiling (kept for pip installs)
# - git: sometimes needed if a dependency is installed from git
# - procps: provides pgrep used by healthchecks
# We install Node.js using NodeSource (setup_18.x -> Node 18 LTS, >=15 requirement satisfied).
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       gnupg \
       dirmngr \
       apt-transport-https \
       ffmpeg \
       libopus0 \
       libopus-dev \
       build-essential \
       gcc \
       python3-dev \
       git \
       procps \
  && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the bot AFTER system packages and node are installed
RUN useradd --create-home --shell /bin/bash botuser

# Switch to non-root user and set the working directory
USER botuser
WORKDIR /home/botuser/app

# Copy and install Python dependencies first (leverage Docker layer caching)
# The project root includes requirements.txt
COPY --chown=botuser:botuser requirements.txt ./

# Use pip to install; adjust flags to speed up and reduce image size.
RUN python -m pip install --upgrade pip setuptools \
  && python -m pip install --upgrade wheel \
  && python -m pip install -r requirements.txt

# Copy application code (do not include secrets like .env)
COPY --chown=botuser:botuser . .

# Environment defaults (can be overridden at runtime)
ENV PYTGCALL_LOG_LEVEL=warning \
    TZ=UTC

# Expose no ports by default — the bot connects to Telegram, not listens.
# If your bot exposes a webserver for healthchecks/webhooks, uncomment and change:
# EXPOSE 8080

# Run the bot. Change to the correct entry point if your main file is different.
CMD ["python", "bot.py"]

# Healthcheck (optional) - checks that the Python process is running
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python.*bot.py" || exit 1

# Notes:
# - Node.js >= 15 is installed (this Dockerfile installs Node 18 LTS).
# - If you want to shrink the final image, we can do a multi-stage build that
#   removes build tools after Python packages are installed. I intentionally
#   left build tools in place in case native builds are required at runtime.
# - Provide your .env at runtime (do NOT bake secrets into the image)
#   Example run:
#     docker build -t musicbot:latest -f MUSICBOT/Dockerfile .
#     docker run --rm --env-file .env -v /path/to/sessions:/home/botuser/app/sessions musicbot:latest
