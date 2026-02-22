FROM python:3.11-slim

# Basic environment
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    TZ=UTC \
    PYTGCALL_LOG_LEVEL=warning \
    TMPDIR=/tmp/music_cache

WORKDIR /app

# Install system deps, Node.js, ffmpeg, build tools, and create bot user + cache dir
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
  && rm -rf /var/lib/apt/lists/* \
  \
  # create an unprivileged user and a writable cache directory for the bot
  && useradd --create-home --shell /bin/bash botuser \
  && mkdir -p /tmp/music_cache \
  && chown -R botuser:botuser /tmp/music_cache \
  && chmod 700 /tmp/music_cache

# Switch to non-root user
USER botuser
WORKDIR /home/botuser/app

# Copy and install Python dependencies
COPY --chown=botuser:botuser requirements.txt ./

RUN python -m pip install --upgrade pip setuptools \
  && python -m pip install --upgrade wheel \
  && python -m pip install -r requirements.txt

# Copy application code (kept as non-root)
COPY --chown=botuser:botuser . .

# Ensure TMPDIR environment is set for the runtime (redundant but explicit)
ENV TMPDIR=/tmp/music_cache

CMD ["python", "bot.py"]

# Simple healthcheck to ensure the bot process is running
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python.*bot.py" || exit 1
