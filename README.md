---
title: Media Toolbox
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
private: true
---

# Media Toolbox

Personal media-processing system for Hugging Face Spaces, built per `PLAN.md`.

The CPU Space (`media-toolbox-cpu`) application code lives in **`main/`**
(FastAPI + Gradio + FFmpeg); the `Dockerfile` at the repo root builds it, so
the repository root can be deployed directly as the Docker Space. A GPU Space
(`media-toolbox-gpu`, ZeroGPU: Whisper / Demucs / Real-ESRGAN) is planned next
and will share the same private HF Storage Bucket.

> The YAML frontmatter above is the Hugging Face Space configuration; copy this
> README (or the frontmatter block) into the Space repository root when
> deploying.

## Features (CPU Space)

- **Video**: compress (H.264/H.265/AV1), target file size (two-pass), resize,
  container convert (auto remux/transcode), trim (fast/accurate), FPS, rotate,
  crop (with frame preview), speed, merge video+audio, concatenate, GIF,
  screenshots, remove audio
- **Audio**: extract, convert, compress, sample rate, mono/stereo, normalize
  (simple + EBU R128 two-pass), trim, speed
- **Subtitles**: extract tracks, add track, burn into video
- **Utilities**: make browser-compatible, optimize MP4 for streaming, remove
  metadata, FFprobe media info
- **Advanced**: validated custom FFmpeg arguments with live command preview
- **History**: outputs persist in a private HF Storage Bucket for **24 hours**,
  then download is denied (410) and an hourly cleanup job deletes them

## Architecture

- FastAPI server with the Gradio UI mounted at `/`
- FFmpeg/FFprobe via argument arrays only (never `shell=True`)
- Outputs are verified with FFprobe, then moved into the mounted bucket at
  `/data/media-bucket` under `jobs/<expires_unix>_<job_id>/`
- `main/cleanup/cleanup.py` runs hourly (HF Scheduled Job) and deletes expired
  prefixes; idempotent, supports `--dry-run`

## API

- `GET /_health` — ffmpeg/ffprobe/bucket status
- `GET /api/capabilities` — detected encoders/filters
- `GET /api/jobs` — non-expired job manifests
- `GET /api/jobs/{prefix}` — single manifest
- `GET /api/jobs/{prefix}/download/{file_id}` — download (410 once expired)
- `POST /api/jobs/{prefix}/delete` / `DELETE /api/jobs/{prefix}` — delete now

## Configuration (Space secrets / env vars)

| Variable | Default | Purpose |
|---|---|---|
| `BUCKET_MOUNT` | `/data/media-bucket` | Mounted HF Storage Bucket path |
| `HF_BUCKET_ID` | _(empty)_ | Bucket id, e.g. `user/media-toolbox-temp` (informational) |
| `RETENTION_HOURS` | `24` | Output retention |
| `WORK_DIR` | `/tmp/media-toolbox` | Ephemeral processing directory |
| `MAX_CONCURRENT_CPU_JOBS` | `1` | Encoding concurrency (rest queue) |
| `MIN_FREE_DISK_GB` | `2.0` | Refuse jobs below this free space |
| `MAX_INPUT_SIZE_GB` | `8.0` | Upload size limit |
| `PORT` | `7860` | HTTP port |

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r main/requirements.txt
cd main
python app.py        # or: uvicorn app:app --host 0.0.0.0 --port 7860
```

Without a bucket mounted at `/data/media-bucket`, a local dev bucket under
`$WORK_DIR/bucket` is used automatically.

## Deployment

```bash
# preview the upload set
python main/scripts/deploy_space.py --dry-run

# deploy (uses `hf auth login` token or HF_TOKEN)
python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-cpu --create-bucket
```

The script uploads the git-visible repo contents to the Docker Space and
optionally creates the private Storage Bucket. Then, once, in the Space
settings UI:

1. Attach the bucket at `/data/media-bucket` (read/write).
2. Schedule an `@hourly` HF Job running
   `python main/cleanup/cleanup.py --bucket /data/media-bucket`.

## Security model

- Local browser uploads only; no URL/remote input.
- Advanced mode accepts FFmpeg *arguments* only (validated via `shlex.split`);
  extra inputs, network protocols, pipes, and path escapes are rejected.
- No credentials in source; secrets come from HF Space settings.

## Known limitations

- CPU Basic hardware: one job at a time by default; large files are slow.
- Target-size mode requires a detectable duration.
- Image-based subtitles (PGS) cannot be exported as text.
