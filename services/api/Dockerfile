FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/api/requirements.txt /app/services/api/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/services/api/requirements.txt

COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY packages /app/packages
COPY services/api /app/services/api
COPY vendor/srt-whiteboard-animation /app/vendor/srt-whiteboard-animation

RUN test -f /app/vendor/srt-whiteboard-animation/scripts/render_stream_whiteboard.py \
    || (echo "Missing whiteboard submodule. Clone with --recurse-submodules or run: git submodule update --init --recursive" >&2; exit 1)

RUN python -m pip install -e /app/packages/whiteboard_engine \
    && mkdir -p /app/data/assets

EXPOSE 8010

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/services/api", "--host", "0.0.0.0", "--port", "8010"]
