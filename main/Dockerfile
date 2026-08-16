FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# FFmpeg (with libass for subtitle burning), certs, and fonts for subtitle rendering.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        fonts-dejavu-core \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# HF Docker Spaces expect the app to run as UID 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

ENV WORK_DIR=/tmp/media-toolbox \
    BUCKET_MOUNT=/data/media-bucket \
    RETENTION_HOURS=24 \
    MAX_CONCURRENT_CPU_JOBS=1 \
    PORT=7860

RUN mkdir -p /tmp/media-toolbox

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
