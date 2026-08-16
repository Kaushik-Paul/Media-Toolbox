# Media Toolbox

Personal media-processing system hosted on Hugging Face Spaces.

**Live: <https://www.mediatoolbox.pp.ua/>** — both Spaces are also directly
reachable on Hugging Face (`media-toolbox-cpu` and `media-toolbox-gpu`).

Two applications share one repository and one private HF Storage Bucket:

- **CPU Space** (`media-toolbox-cpu`, Docker SDK) — general FFmpeg operations.
  Entrypoint: `main/app.py` (FastAPI + Gradio + FFmpeg).
- **GPU Space** (`media-toolbox-gpu`, Gradio SDK + ZeroGPU) — Whisper, Demucs,
  Real-ESRGAN. Entrypoint: `main/gpu/app.py`.

Both use the same manifest schema, bucket layout (`jobs/<expires>_<job_id>/`),
and 24-hour job history.

Space infrastructure stays at the repository root: `Dockerfile.cpu`,
`requirements.cpu.txt`, `requirements.gpu.txt`, and `packages.gpu.txt`.
`main/scripts/deploy_space.py` stages an SDK-specific package and maps those
files to the root names the chosen HF builder expects.

> **Deploying requires Space frontmatter.** This README intentionally carries
> none; `main/scripts/deploy_space.py` reads `sdk:` from the YAML frontmatter
> at the top of the root README and exits without it. Before deploying, paste
> the matching block from **`gradio_sdk.txt`** (Docker for the CPU Space,
> Gradio for the GPU Space) above the title, deploy, then remove it again.

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

Video, Audio, and Subtitle sections each have a shared session upload above
their subtools. Upload a source once, then switch operations without uploading
it again; Merge, Concatenate, Add Track, and Burn ask only for their additional
inputs.

## Features (GPU Space, ZeroGPU)

- **AI Transcription** (Whisper large-v3-turbo): transcribe or translate to
  English, 22 languages + auto-detect, segment or word-level timestamps,
  outputs TXT + SRT + VTT + JSON
- **Stem Separation** (Demucs htdemucs): vocals + instrumental or full 4-stem
  split, WAV/FLAC/MP3 output with optional ZIP
- **AI Upscaling** (Real-ESRGAN): images (General / Anime models, 2x/4x,
  PNG/WebP/JPG) and short videos (experimental: chunked frame upscaling with
  the original audio muxed back)
- GPU work runs only inside `@spaces.GPU` functions with dynamic durations;
  FFmpeg pre/post-processing stays on the CPU worker
- Same 24-hour bucket history as the CPU Space; an optional header link jumps
  back to it (`CPU_SPACE_URL`)

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

Shared:

| Variable | Default | Purpose |
|---|---|---|
| `BUCKET_MOUNT` | `/data/media-bucket` | Mounted HF Storage Bucket path |
| `HF_BUCKET_ID` | _(empty)_ | Bucket id, e.g. `user/media-toolbox` (informational) |
| `RETENTION_HOURS` | `24` | Output retention |
| `WORK_DIR` | `/tmp/media-toolbox` | Ephemeral processing directory |
| `MAX_CONCURRENT_CPU_JOBS` | `1` | Encoding concurrency (rest queue) |
| `MIN_FREE_DISK_GB` | `2.0` | Refuse jobs below this free space |
| `MAX_INPUT_SIZE_GB` | `8.0` | Upload size limit |
| `PORT` | `7860` | HTTP port |

GPU Space only:

| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL` | `openai/whisper-large-v3-turbo` | Transcription model |
| `DEMUCS_MODEL` | `htdemucs` | Stem separation model |
| `ENABLE_DEMUCS` | `true` | Enable the Stem Separation tool |
| `ENABLE_REALESRGAN` | `true` | Enable the AI Upscaling tool |
| `GPU_VIDEO_MAX_DURATION` | `120` | Video upscale length limit (seconds) |
| `GPU_VIDEO_MAX_PIXELS` | `2073600` | Video upscale resolution limit (1080p) |
| `GPU_VIDEO_MAX_FILE_SIZE_GB` | `1.0` | Video upscale input size limit |
| `CPU_SPACE_URL` | _(empty)_ | Header link back to the CPU Space |
| `MODEL_CACHE_DIR` | `$WORK_DIR/models` | Model weight cache directory |

## Local development

CPU Space:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.cpu.txt
cd main
python app.py        # or: uvicorn app:app --host 0.0.0.0 --port 7860
```

Without a bucket mounted at `/data/media-bucket`, a local dev bucket under
`$WORK_DIR/bucket` is used automatically.

GPU Space (models run on CPU locally; `@spaces.GPU` is a no-op off ZeroGPU):

```bash
pip install -r requirements.gpu.txt
python main/gpu/app.py
```

## Deployment

Deploying requires the matching YAML frontmatter at the top of this README —
the script refuses to run without it. Paste the Docker block from
`gradio_sdk.txt`, deploy the CPU Space, then remove it:

```bash
# preview the staged package
python main/scripts/deploy_space.py --dry-run

# deploy (uses `hf auth login` token or HF_TOKEN)
python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-cpu --create-bucket
```

The script creates and keeps both application Spaces public, stages an
SDK-specific package from the git-visible repository contents, and optionally
creates the shared Storage Bucket as private. For CPU it publishes
`Dockerfile.cpu` as `Dockerfile`; its inferred hardware is `cpu-basic` (use
`--hardware cpu-upgrade` if wanted). Each deployment makes the remote Space
repository an exact mirror of the selected package, removing stale files from
an older layout or SDK deployment.

For the ZeroGPU Space, swap in the Gradio frontmatter (both blocks are stashed
in `gradio_sdk.txt`) before deploying — remove it again afterwards:

```yaml
---
title: Media GPU Toolbox
emoji: 🎬
colorFrom: green
colorTo: indigo
sdk: gradio
app_file: main/gpu/app.py
python_version: 3.12.12
---
```

```bash
python main/scripts/deploy_space.py --dry-run
python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-gpu \
  --attach-bucket --bucket-id <user>/media-toolbox
```

For `sdk: gradio`, the script infers `zero-a10g`. ZeroGPU does not support the
Docker SDK, so `Dockerfile.cpu` is the only Dockerfile source; the staged GPU
Space contains no Dockerfile. The deploy helper publishes
`requirements.gpu.txt` / `packages.gpu.txt` as root `requirements.txt` /
`packages.txt`, which are the filenames the Gradio builder consumes.

Because the Spaces are public and V1 has no user accounts, any visitor can use
the tools and view the shared 24-hour job history exposed by the application.
The underlying bucket itself remains private.

ZeroGPU usage draws from each visitor's daily quota (free accounts get little,
PRO gets more), so the UI shows a quota banner and requests short dynamic
durations for better queue priority.

After deploying:

1. Attach the same bucket read/write at `/data/media-bucket`. Both deploy
   commands above do this automatically (`--create-bucket` for CPU,
   `--attach-bucket` for GPU).
2. Create exactly one hourly cleanup job, not one per Space. The job needs both
   the CPU Space repository (read-only, for the script) and the bucket
   (read/write):

```bash
hf jobs scheduled run \
  --name media-toolbox-cleanup \
  --volume hf://spaces/<user>/media-toolbox-cpu:/workspace:ro \
  --volume hf://buckets/<user>/media-toolbox:/data/media-bucket \
  @hourly python:3.12-slim \
  python /workspace/main/cleanup/cleanup.py --bucket /data/media-bucket
```

## Security model

- Local browser uploads only; no URL/remote input.
- Advanced mode accepts FFmpeg *arguments* only (validated via `shlex.split`);
  extra inputs, network protocols, pipes, and path escapes are rejected.
- No credentials in source; secrets come from HF Space settings.

## Known limitations

- CPU Basic hardware: one job at a time by default; large files are slow.
- Target-size mode requires a detectable duration.
- Image-based subtitles (PGS) cannot be exported as text.
