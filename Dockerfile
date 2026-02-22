FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

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

RUN useradd --create-home --shell /bin/bash botuser

USER botuser
WORKDIR /home/botuser/app

COPY --chown=botuser:botuser requirements.txt ./

RUN python -m pip install --upgrade pip setuptools \
  && python -m pip install --upgrade wheel \
  && python -m pip install -r requirements.txt

COPY --chown=botuser:botuser . .

ENV PYTGCALL_LOG_LEVEL=warning \
    TZ=UTC


CMD ["python", "bot.py"]

HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python.*bot.py" || exit 1
