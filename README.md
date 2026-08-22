<div align="center">

# 🎬 Media Toolbox

**A browser-based toolkit for practical video and audio conversion, powered by
FFmpeg with optional GPU-assisted AI tools.**

[![CPU Space](https://img.shields.io/badge/CPU_Space-Open_Toolbox-4f46e5?logo=huggingface&logoColor=white)](https://www.mediatoolbox.pp.ua/)
[![GPU Space](https://img.shields.io/badge/GPU_Space-AI_Toolbox-059669?logo=huggingface&logoColor=white)](https://gpu.mediatoolbox.pp.ua/)
[![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-16a34a)](LICENSE)

[CPU app](https://www.mediatoolbox.pp.ua/) ·
[GPU app](https://gpu.mediatoolbox.pp.ua/) ·
[CPU Space](https://huggingface.co/spaces/kaushikpaul/media-toolbox-cpu) ·
[GPU Space](https://huggingface.co/spaces/kaushikpaul/media-toolbox-gpu)

</div>

Media Toolbox handles common media jobs without requiring users to remember
FFmpeg commands. Upload a file once—or fetch it from a supported public URL—and
reuse it across the available tools. The CPU and GPU applications share the
same interface, job format, history, and private Hugging Face Storage Bucket.

## Choose an app

| App | Best for | Runtime | Access |
|---|---|---|---|
| **CPU Toolbox** | Everyday video, audio, subtitle, and container operations | Docker Space on `cpu-basic` | Public |
| **GPU Toolbox** | Every CPU tool plus transcription, stem separation, and AI upscaling | Gradio Space on ZeroGPU | Password protected when credentials are configured |

Each Space accepts only one upload, URL fetch, or conversion at a time. This
keeps large jobs from exhausting the worker. The activity toolbar shows the
current state and provides a password-protected force-cancel action.

## Available tools

### Video

| Tool | What it does |
|---|---|
| Compress | H.264, H.265, or AV1 compression with practical quality presets |
| Target Size | Two-pass encoding toward a requested output size |
| Resize | Change resolution while preserving or controlling aspect ratio |
| Convert | Remux when possible, transcode when necessary |
| Trim / Cut | Fast keyframe trim or accurate re-encoded trim |
| FPS | Change video frame rate |
| Rotate / Flip | Rotate or mirror video |
| Crop | Crop with a source-frame preview |
| Speed | Speed up or slow down video and audio together |
| Merge A+V | Combine a video stream with a separate audio stream |
| Concatenate | Join compatible media files |
| GIF | Create a GIF from a short video segment |
| Screenshot | Export a frame at a selected timestamp |
| Remove Audio | Produce a video-only file |

### Audio, subtitles, and utilities

| Area | Tools |
|---|---|
| **Audio** | Extract, convert, compress, change sample rate, mono/stereo conversion, simple or EBU R128 normalization, trim, and speed |
| **Subtitles** | Extract subtitle tracks, add a subtitle track, or burn subtitles into video |
| **Utilities** | Make browser-compatible, optimize MP4 for streaming, remove metadata, and inspect media with FFprobe |
| **Advanced** | Run validated custom FFmpeg arguments while the application controls all input and output paths |
| **History** | View, download, or manually delete recent results |

Video, Audio, and Subtitles each have a shared source control. Select the media
once, then switch between tools without uploading or downloading it again.
Operations such as Merge, Concatenate, Add Track, and Burn request only their
additional files.

### GPU and AI tools

The GPU Space includes every tool above, followed by:

- **Transcription — Whisper `large-v3-turbo`:** transcription or English
  translation, automatic language detection, segment or word timestamps, and
  TXT, SRT, VTT, and JSON outputs.
- **Stem Separation — Demucs `htdemucs`:** vocals/instrumental or four-stem
  separation with WAV, FLAC, MP3, and optional ZIP output.
- **AI Upscaling — Real-ESRGAN:** General and Anime image models, 2×/4×
  scaling, PNG/WebP/JPG output, and experimental short-video upscaling with the
  original audio muxed back.

GPU work runs only inside ZeroGPU functions. Media preparation and final
FFmpeg muxing remain on the CPU worker. ZeroGPU processing consumes the
visitor's Hugging Face GPU quota.

## Input and download workflow

1. Select a local file or expand **Or fetch from a public URL**.
2. For URL input, paste a public direct-media, Google Drive, OneDrive, or
   SharePoint link and select **Fetch URL**.
3. Wait for FFprobe to validate the source and display its media details.
4. Choose a tool, configure its options, and start the operation.
5. Preview or download the result, or retrieve it later from **History**.

Public cloud-drive links must be downloadable without signing in. Server-side
URL fetching is often the fastest option for large files because the source is
downloaded directly by the Space rather than uploaded through the browser and
the Hugging Face edge.

Browser upload and result-download speeds still depend on the user's connection
and the Hugging Face network path. Downloads support HTTP byte ranges, allowing
clients to resume interrupted transfers.

## Storage and retention

- Uploaded working files stay in the Space's ephemeral work directory.
- Completed outputs are FFprobe-verified before being persisted.
- CPU and GPU results use the shared private bucket layout
  `jobs/<expires_unix>_<job_id>/`.
- Results remain downloadable for **24 hours**. Expired routes return
  `410 Gone`, even if physical cleanup has not run yet.
- An authenticated Google Cloud function removes physical bucket folders older
  than **30 days** once per day.
- Partial, failed, and cancelled outputs are never persisted.

The CPU application is public and its History view is shared. Do not submit
sensitive media to a publicly accessible deployment. The GPU UI can be gated
with `TOOLBOX_USERNAME` and `TOOLBOX_PASSWORD`.

## Security model

- Every input is checked by FFprobe before processing, and every output is
  checked before persistence.
- Remote downloads accept only HTTP(S), ports 80/443, and globally routable
  destinations. Embedded credentials and local, private, reserved, or unsafe
  redirect targets are rejected.
- Remote responses are streamed to a temporary `.part` file with size and disk
  limits. HTML, text, JSON, XML, and invalid media are rejected.
- FFmpeg commands are argument arrays; the application never uses
  `shell=True` or `os.system`.
- Advanced mode accepts validated FFmpeg arguments only. Extra inputs, network
  protocols, pipes, and path escapes are blocked.
- Credentials and tokens are supplied through environment variables, Space
  secrets, or Google Secret Manager—never source code.
- GPU login uses a signed 30-day secure cookie that works in both the direct
  domain and Hugging Face App frame.

## Architecture

```text
Browser / public media URL
            |
            v
    FastAPI + Gradio UI
            |
            v
  Per-Space activity gate (1)
            |
            v
      FFprobe validation
            |
            v
 FFmpeg operation / ZeroGPU model
            |
            v
   FFprobe output verification
            |
            v
 Private HF Storage Bucket
            |
            v
 Expiry-checked range downloads
```

The CPU application is served by FastAPI with Gradio mounted at `/`. The GPU
application reuses the shared configuration, FFprobe, FFmpeg runner, operation
modules, themes, and storage format rather than maintaining copies.

### Repository layout

```text
README.md                   Project guide; no Space frontmatter is committed
gradio_sdk.txt              CPU and GPU Space frontmatter templates
Dockerfile.cpu              CPU Docker image source
requirements.cpu.txt        CPU Python dependencies
requirements.gpu.txt        GPU Python dependencies
packages.gpu.txt            GPU system packages
main/
  app.py                    CPU FastAPI + Gradio entrypoint
  backend/                  Probing, command building, jobs, and HTTP routes
  core/                     Configuration, activity gate, models, URL fetching
  operations/               Shared FFmpeg operation implementations
  ui/                       Shared CPU UI, styling, and shell controls
  gpu/                      ZeroGPU app, models, job manager, and AI tool UI
  cloud_cleanup/            Google Cloud bucket-cleanup function
  cleanup/cleanup.py        Manual mounted-bucket cleanup utility
  scripts/
    deploy_space.py         CPU/GPU Space packaging and deployment
    deploy_cleanup_function.py
                            Cloud function and scheduler deployment
```

## HTTP API

| Method and route | Purpose |
|---|---|
| `GET /_health` | FFmpeg, FFprobe, and bucket health |
| `GET /api/capabilities` | Detected encoders and filters |
| `GET /api/jobs` | Non-expired job manifests |
| `GET /api/jobs/{prefix}` | One job manifest |
| `GET /api/jobs/{prefix}/download/{file_id}` | Expiry-checked, byte-range download |
| `POST /api/jobs/{prefix}/delete` | Delete a job immediately |
| `DELETE /api/jobs/{prefix}` | Delete a job immediately |

## Configuration

### Shared Space variables

| Variable | Default | Purpose |
|---|---:|---|
| `BUCKET_MOUNT` | `/data/media-bucket` | Mounted Hugging Face Storage Bucket path |
| `HF_BUCKET_ID` | Empty | Bucket identifier such as `user/media-toolbox` |
| `RETENTION_HOURS` | `24` | Logical output lifetime |
| `WORK_DIR` | `/tmp/media-toolbox` | Ephemeral upload and processing directory |
| `MAX_CONCURRENT_CPU_JOBS` | `1` | Worker concurrency limit |
| `MIN_FREE_DISK_GB` | `2.0` | Minimum free space required before a job starts |
| `MAX_INPUT_SIZE_GB` | `10.0` | Maximum local browser upload or remote URL input size |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable |
| `FFPROBE_PATH` | `ffprobe` | FFprobe executable |
| `PORT` | `7860` | HTTP server port |
| `TOOLBOX_PASSWORD` | Empty | Force-cancel password; also the GPU login password |

Additional tuning options are defined in
[`main/core/config.py`](main/core/config.py).

### GPU Space variables

| Variable | Default | Purpose |
|---|---:|---|
| `TOOLBOX_USERNAME` | Empty | Login username; authentication is disabled unless both credentials are set |
| `TOOLBOX_PASSWORD` | Empty | Login and force-cancel password |
| `WHISPER_MODEL` | `openai/whisper-large-v3-turbo` | Transcription model |
| `DEMUCS_MODEL` | `htdemucs` | Stem-separation model |
| `ENABLE_DEMUCS` | `true` | Show Stem Separation |
| `ENABLE_REALESRGAN` | `true` | Show AI Upscaling |
| `GPU_VIDEO_MAX_DURATION` | `120` | Maximum upscale video duration in seconds |
| `GPU_VIDEO_MAX_PIXELS` | `2073600` | Maximum upscale input pixels per frame (1080p) |
| `GPU_VIDEO_MAX_FILE_SIZE_GB` | `1.0` | Maximum video-upscale input size |
| `MODEL_CACHE_DIR` | `$WORK_DIR/models` | Model-weight cache directory |
| `CPU_SPACE_URL` | Empty | Optional header link to the CPU application |

## Local development

Create the virtual environment at the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Run the CPU application:

```bash
pip install -r requirements.cpu.txt
cd main
python app.py
```

The app is available at <http://127.0.0.1:7860>. Without a bucket mounted at
`/data/media-bucket`, it creates a development bucket below `$WORK_DIR`.

Run the GPU application from the repository root:

```bash
pip install -r requirements.gpu.txt
python main/gpu/app.py
```

Off ZeroGPU, the GPU decorator is a no-op. AI models therefore use the locally
available PyTorch device and may require substantial memory and disk space.

## Deployment

### Prerequisites

- Python 3.12 and the project dependencies
- The current `hf` CLI, authenticated with `hf auth login`, or `HF_TOKEN`
- Permission to create/update both Spaces and the shared private bucket

> [!IMPORTANT]
> The committed README intentionally has no Hugging Face Space frontmatter.
> Before each deployment, copy the matching CPU or GPU YAML block from
> `gradio_sdk.txt` to the very top of this file. Remove it locally after the
> deployment so the repository continues to hold one neutral README.

### CPU Space

Add the Docker frontmatter from `gradio_sdk.txt`, then run:

```bash
# Inspect the exact package without changing the Space
python main/scripts/deploy_space.py --dry-run

# Create/attach the private bucket on the first deployment
python main/scripts/deploy_space.py \
  --repo-id <username>/media-toolbox-cpu \
  --create-bucket
```

The helper publishes `Dockerfile.cpu` as the Space-root `Dockerfile` and
defaults to `cpu-basic`. Use `--hardware cpu-upgrade` or another compatible
flavor when required.

### GPU Space

Replace the README frontmatter with the Gradio block from `gradio_sdk.txt`,
then run:

```bash
python main/scripts/deploy_space.py --dry-run

python main/scripts/deploy_space.py \
  --repo-id <username>/media-toolbox-gpu \
  --attach-bucket \
  --bucket-id <username>/media-toolbox
```

The helper defaults to `zero-a10g` and publishes `requirements.gpu.txt` and
`packages.gpu.txt` as the root `requirements.txt` and `packages.txt` files
expected by the Gradio builder. ZeroGPU does not support the Docker SDK.

Every deployment mirrors the selected staged package to the target Space and
removes stale remote files. The helper also keeps the Spaces public and
preserves the configured hardware and bucket attachment.

### Daily physical cleanup

The application denies expired downloads after 24 hours. Deploy the separate
authenticated cleanup function to remove bucket folders older than 30 days:

```bash
GCP_PROJECT_ID=<project-id> \
HF_BUCKET_ID=<username>/media-toolbox \
python main/scripts/deploy_cleanup_function.py
```

The Python script deploys a second-generation Cloud Run function and Cloud
Scheduler job in `asia-south1` by default. It stores the Hugging Face token in
Secret Manager, creates a uniquely named temporary GCS source bucket, and
deletes that temporary bucket on success or failure.

| Cleanup variable | Project default | Purpose |
|---|---|---|
| `GCP_PROJECT_ID` | `adept-fountain-349605` | Google Cloud project |
| `GCP_REGION` | `asia-south1` | Function and scheduler region |
| `HF_BUCKET_ID` | `kaushikpaul/media-toolbox` | Bucket to clean |
| `RETENTION_DAYS` | `30` | Physical retention period |
| `CLEANUP_SCHEDULE` | `30 3 * * *` | Daily cron schedule |
| `CLEANUP_TIME_ZONE` | `Asia/Kolkata` | Scheduler time zone |

Set `HF_TOKEN` when creating or rotating the Secret Manager value. If it is
unset, the script reuses an existing secret version or initializes it from the
current `hf auth login` token.

## Verification

This repository intentionally has no test directory. Use compilation checks
and manual end-to-end operations:

```bash
python -m compileall main/app.py main/core main/backend main/operations \
  main/ui main/cleanup main/gpu main/cloud_cleanup

python main/scripts/deploy_space.py --dry-run
curl --fail https://www.mediatoolbox.pp.ua/_health
```

For storage changes, preview cleanup before applying it:

```bash
python main/cleanup/cleanup.py --bucket /data/media-bucket --dry-run
```

## Known limitations

- Large browser transfers are limited by the user's network and the Hugging
  Face edge path; a fixed upload or download duration cannot be guaranteed.
- Target-size encoding requires a detectable source duration.
- Image-based subtitle formats such as PGS cannot be exported as text.
- Video AI upscaling is experimental and intentionally limited to short,
  bounded-resolution inputs.

## License

Media Toolbox is available under the [MIT License](LICENSE).
