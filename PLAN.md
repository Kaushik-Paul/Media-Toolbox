# Media Toolbox — Complete Build Specification

## 1. Project objective

Build a private personal media-processing system hosted entirely on Hugging Face consisting of:

```text
media-toolbox-cpu
    ↓
General FFmpeg operations

media-toolbox-gpu
    ↓
AI/GPU media operations

media-toolbox
    ↓
Shared private HF Storage Bucket

cleanup scheduled job
    ↓
Delete files after 24 hours
```

The user should be able to upload a video/audio/image, perform an operation, preview the result when possible, download it, and access recently generated outputs for up to **24 hours**.

After 24 hours:

```text
download access = immediately denied

physical file = deleted by next cleanup run
```

This distinction is important. The cleanup job can run hourly, but the application itself must check `expires_at` before allowing downloads. That gives a true logical 24-hour expiry instead of "somewhere between 24 and 25 hours."

Hugging Face Storage Buckets are appropriate here because they are mutable object storage rather than Git-backed repositories, support deletion, can be private, and can be mounted read/write into Spaces. ([Hugging Face][2])

---

# 2. High-level architecture

Build two completely separate HF Spaces sharing one storage bucket.

```text
                           USER
                            │
                ┌───────────┴───────────┐
                │                       │
                ▼                       ▼
       ┌────────────────┐      ┌─────────────────┐
       │ CPU TOOLBOX    │      │ AI TOOLBOX     │
       │                │      │                 │
       │ Docker Space   │      │ Gradio Space   │
       │ CPU Basic      │      │ ZeroGPU         │
       │                │      │                 │
       │ FFmpeg         │      │ Whisper         │
       │ FFprobe        │      │ Demucs          │
       │ FastAPI        │      │ Real-ESRGAN     │
       │ Gradio         │      │ FFmpeg helpers  │
       └───────┬────────┘      └────────┬────────┘
               │                        │
               └────────────┬───────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ PRIVATE HF BUCKET   │
                  │ media-toolbox      │
                 │                     │
                 │ jobs/...            │
                 └──────────┬──────────┘
                            │
                            ▼
                  Hourly HF Scheduled Job
                            │
                   delete expired jobs
```

Do **not** use ZeroGPU for normal FFmpeg transcoding. ZeroGPU is designed around GPU functions using `@spaces.GPU`, is Gradio-only, and allocates/relinquishes GPU resources around decorated functions. ([Hugging Face][3])

CPU FFmpeg and GPU AI processing should therefore remain separate.

---

# 3. Source repository structure

Develop everything initially as one monorepo.

**Implemented + required combined layout.** All application logic lives under
`main/`: the CPU application and shared modules remain directly under it, and
the ZeroGPU application lives under `main/gpu/`. The repository root contains
the single README plus deployment infrastructure for both targets. README
frontmatter selects which Space is staged and deployed.

```text
media-toolbox/
│
├── README.md            # root README; carries HF Space YAML frontmatter
├── .gitignore
├── .dockerignore
├── Dockerfile.cpu       # only Dockerfile source; deployed as Dockerfile
├── requirements.cpu.txt # CPU dependencies used by Dockerfile.cpu
├── requirements.gpu.txt # deployed as requirements.txt for Gradio
├── packages.gpu.txt     # deployed as packages.txt for Gradio
├── PLAN.md
├── AGENTS.md
├── LICENSE
│
└── main/                # all CPU, GPU, and shared application logic
    ├── app.py           # entrypoint: FastAPI + Gradio mounted at "/"
    │
    ├── core/            # shared across both Spaces (reused by the GPU Space)
    │   ├── config.py
    │   ├── models.py
    │   ├── filenames.py
    │   ├── time_utils.py
    │   ├── media_types.py
    │   ├── manifests.py
    │   └── storage/
    │       ├── bucket.py
    │       └── retention.py
    │
    ├── backend/
    │   ├── probe.py
    │   ├── capabilities.py
    │   ├── command_builder.py
    │   ├── ffmpeg_runner.py
    │   ├── job_manager.py
    │   ├── download.py
    │   ├── security.py
    │   └── services.py
    │
    ├── operations/      # one module per operation; registry in __init__.py
    │   ├── base.py
    │   ├── compress.py
    │   ├── target_size.py
    │   ├── resize.py
    │   ├── audio.py
    │   ├── convert.py
    │   ├── trim.py
    │   ├── fps.py
    │   ├── crop.py
    │   ├── rotate.py
    │   ├── speed.py
    │   ├── subtitles.py
    │   ├── merge.py
    │   ├── concatenate.py
    │   ├── gif.py
    │   ├── screenshot.py
    │   ├── metadata.py
    │   ├── compatibility.py
    │   └── advanced.py
    │
    ├── ui/
    │   ├── app.py
    │   ├── theme.py
    │   ├── components.py
    │   ├── tools.py
    │   └── history.py
    │
    ├── cleanup/
    │   └── cleanup.py
    │
    ├── scripts/
    │   └── deploy_space.py   # stages the selected SDK package
    │
    └── gpu/              # ZeroGPU Gradio application
        ├── app.py        # README app_file: main/gpu/app.py
        ├── backend/
        │   ├── preprocessing.py
        │   ├── postprocessing.py
        │   └── job_manager.py
        ├── models/
        │   ├── whisper.py
        │   ├── demucs.py
        │   └── realesrgan.py
        └── ui/
            ├── app.py
            ├── transcription.py
            ├── stems.py
            ├── upscale.py
            └── history.py
```

Deviations from the original plan below, kept deliberately:

- The GPU Space application lives specifically in `main/gpu/`. It reuses the
  schema and bucket conventions implemented in `main/core/`; do not invent a
  second manifest or storage layout.
- Deployment is one metadata-driven Python script
  (`main/scripts/deploy_space.py`, using `huggingface_hub`) instead of
  per-Space shell scripts. It reads but never rewrites the root README.
- There is exactly one README and one Dockerfile source in the monorepo.
  **Do not create `main/gpu/README.md` or a GPU Dockerfile.** ZeroGPU is
  Gradio-only and cannot use the Docker SDK.
- All infrastructure source files stay at repository root. The source names
  are `Dockerfile.cpu`, `requirements.cpu.txt`, `requirements.gpu.txt`, and
  `packages.gpu.txt`; none belong inside `main/`.
- `deploy_space.py` builds an empty temporary staging directory. For CPU it
  maps `Dockerfile.cpu` to `Dockerfile` and includes
  `requirements.cpu.txt`. For GPU it maps `requirements.gpu.txt` to
  `requirements.txt` and `packages.gpu.txt` to `packages.txt`, as required by
  the Gradio builder. Non-selected infrastructure is not uploaded.
- No `Makefile` / `pyproject.toml`.
- No test files in the repository (see §66).

The original sketch below is historical reference only. **Do not reproduce its
per-Space README files, second Dockerfile, or separate deploy scripts.**

```text
media-toolbox/
│
├── README.md
├── .gitignore
├── Makefile
├── pyproject.toml
│
├── shared/
│   └── media_toolbox_core/
│       ├── __init__.py
│       ├── config.py
│       ├── models.py
│       ├── filenames.py
│       ├── time_utils.py
│       ├── media_types.py
│       ├── manifests.py
│       └── storage/
│           ├── __init__.py
│           ├── bucket.py
│           └── retention.py
│
├── cpu-space/
│   ├── README.md
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   │
│   ├── backend/
│   │   ├── config.py
│   │   ├── probe.py
│   │   ├── ffmpeg_runner.py
│   │   ├── command_builder.py
│   │   ├── capabilities.py
│   │   ├── job_manager.py
│   │   ├── download.py
│   │   └── security.py
│   │
│   ├── operations/
│   │   ├── compress.py
│   │   ├── target_size.py
│   │   ├── resize.py
│   │   ├── audio.py
│   │   ├── convert.py
│   │   ├── trim.py
│   │   ├── fps.py
│   │   ├── crop.py
│   │   ├── rotate.py
│   │   ├── speed.py
│   │   ├── subtitles.py
│   │   ├── merge.py
│   │   ├── concatenate.py
│   │   ├── gif.py
│   │   ├── metadata.py
│   │   ├── compatibility.py
│   │   └── advanced.py
│   │
│   ├── ui/
│   │   ├── app.py
│   │   ├── components.py
│   │   ├── theme.py
│   │   ├── cpu_tools.py
│   │   ├── history.py
│   │   └── result_card.py
│   │
│   └── tests/
│
├── gpu-space/
│   ├── README.md
│   ├── requirements.txt
│   ├── packages.txt
│   ├── app.py
│   │
│   ├── backend/
│   │   ├── config.py
│   │   ├── preprocessing.py
│   │   ├── postprocessing.py
│   │   └── job_manager.py
│   │
│   ├── models/
│   │   ├── whisper.py
│   │   ├── demucs.py
│   │   └── realesrgan.py
│   │
│   ├── ui/
│   │   ├── app.py
│   │   ├── transcription.py
│   │   ├── stems.py
│   │   ├── upscale.py
│   │   └── history.py
│   │
│   └── tests/
│
├── cleanup/
│   ├── cleanup.py
│   ├── requirements.txt
│   └── README.md
│
├── scripts/
│   ├── deploy_cpu.sh
│   ├── deploy_gpu.sh
│   ├── create_bucket.sh
│   ├── create_cleanup_job.sh
│   └── smoke_test.sh
│
└── tests/
    └── integration/
```

Deployment still targets **two different HF Space repositories**. The monorepo is simply the development source.

Each Hub repository receives an exact SDK-specific package assembled from the
git-visible monorepo files. The CPU package omits `main/gpu/` and GPU
infrastructure; the GPU package receives the combined `main/` tree and only
the dependency filenames consumed by the Gradio builder. A deployment also
removes stale remote files that are not in the selected package.

---

# 4. Hugging Face resources to create

Use these names unless the user changes them:

```text
Space 1:
<username>/media-toolbox-cpu

Space 2:
<username>/media-toolbox-gpu

Bucket:
<username>/media-toolbox
```

Make both application Spaces **public**. Keep the shared Storage Bucket
**private** because media files may contain personal content. Since V1 has no
user accounts, the public application exposes its shared 24-hour history to
visitors even though the bucket itself is not public.

The CPU Space should use:

```yaml
---
title: Media Toolbox
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
---
```

Hugging Face Docker Spaces support custom containers, FastAPI applications, runtime secrets, and the default exposed application port of 7860. ([Hugging Face][4])

The GPU Space must use:

```yaml
---
title: Media AI Toolbox
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: gradio
app_file: main/gpu/app.py
python_version: 3.10.13
---
```

because ZeroGPU currently supports only the Gradio SDK. ([Hugging Face][3])

These are two alternate frontmatter states of the **same root README.md**.
The user manually changes the frontmatter before deploying each target. The
deployment helper validates the matching source infrastructure and `app_file`,
then stages the filenames expected by that SDK. The Markdown body below the
frontmatter stays unchanged.

Do not put `private: true` in README frontmatter. Repository visibility is a
Hub setting; `deploy_space.py` explicitly keeps both Spaces public on every
deployment while bucket creation remains private.

## Deployment contract

`main/scripts/deploy_space.py` must:

```text
read root README metadata without modifying it
accept only sdk: docker or sdk: gradio
validate Dockerfile.cpu + requirements.cpu.txt for Docker
validate requirements.gpu.txt + packages.gpu.txt + app_file for Gradio
infer cpu-basic for Docker
infer zero-a10g for Gradio
create/update the selected public Space
request the inferred/explicit hardware on every deployment
stage only git-visible files plus the selected infrastructure mappings
delete stale files from the remote Space repository during the atomic upload
optionally create and/or attach the shared bucket
```

CPU deployment:

```bash
python main/scripts/deploy_space.py --dry-run
python main/scripts/deploy_space.py \
  --repo-id <username>/media-toolbox-cpu \
  --create-bucket
```

Before GPU deployment, change the root README to the Gradio metadata above,
then run:

```bash
python main/scripts/deploy_space.py --dry-run
python main/scripts/deploy_space.py \
  --repo-id <username>/media-toolbox-gpu \
  --attach-bucket \
  --bucket-id <username>/media-toolbox
```

`--create-bucket` creates the bucket and attaches it read/write.
`--attach-bucket` attaches an existing bucket and replaces the Space's current
volume list. After GPU deployment, restore the Docker frontmatter before the
next CPU deployment. Always inspect `--dry-run` and its detected SDK,
entrypoint, and hardware before a real upload.

---

# 5. Shared bucket design

Attach the same private Storage Bucket to both Spaces.

Recommended mount:

```text
/data/media-bucket
```

HF currently supports attaching buckets to Spaces as persistent read/write volumes, while normal Space disk remains ephemeral. ([Hugging Face][5])

The CPU deployment normally creates and attaches the bucket with
`--create-bucket`. The GPU deployment attaches that same existing bucket with
`--attach-bucket --bucket-id <username>/media-toolbox`. Both mounts must be
read/write at exactly `/data/media-bucket`.

However, **do not process media directly inside the mounted bucket**.

Use this pattern:

```text
upload
 ↓
/tmp/media-toolbox/<job-id>/
 ↓
process locally
 ↓
verify result
 ↓
move/upload completed output into bucket
 ↓
delete local working directory
```

Processing locally reduces the risk of exposing half-written files in the persistent bucket.

---

# 6. Bucket directory layout

Use expiry time in the directory name.

For example:

```text
jobs/
    1786903200_a31f5f92/
        manifest.json
        output.mp4
        thumbnail.jpg

    1786908417_c428af20/
        manifest.json
        transcription.srt
        transcription.vtt
        transcription.txt

    1786912202_e635bee9/
        manifest.json
        vocals.flac
        instrumental.flac
```

The format should be:

```text
<expires_unix_timestamp>_<job_uuid>
```

This is preferable to relying only on timestamps inside `manifest.json`.

Cleanup can simply parse:

```text
1786903200_a31f5f92
^^^^^^^^^^
expiry
```

Then:

```python
if expiry_timestamp <= current_timestamp:
    delete_entire_prefix()
```

Even orphaned or partially uploaded jobs therefore eventually expire.

---

# 7. Job manifest format

Every successful job must contain:

```json
{
  "schema_version": 1,
  "job_id": "a31f5f92-...",
  "source": "cpu",
  "operation": "compress_video",

  "original_filename": "vacation.mov",
  "original_size": 1498838471,

  "created_at": "2026-08-16T10:15:20Z",
  "completed_at": "2026-08-16T10:22:14Z",
  "expires_at": "2026-08-17T10:22:14Z",
  "expires_unix": 1786904534,

  "outputs": [
    {
      "id": "main",
      "filename": "vacation-compressed.mp4",
      "mime_type": "video/mp4",
      "size": 421833725
    }
  ],

  "parameters": {
    "codec": "h264",
    "crf": 23,
    "resolution": "1080p",
    "audio_bitrate": "128k"
  },

  "media_info": {
    "duration_seconds": 612.34,
    "width": 1920,
    "height": 1080
  },

  "app_version": "1.0.0"
}
```

Do not store:

```text
HF tokens
IP addresses
browser headers
absolute local paths
raw authentication information
```

---

# 8. CPU Space technology

Use:

```text
Python
FastAPI
Gradio Blocks
FFmpeg
FFprobe
huggingface_hub
Pydantic
```

Run FastAPI as the top-level server and mount the Gradio application onto it.

Current Gradio provides `mount_gradio_app()` specifically for mounting `gr.Blocks` into FastAPI. ([Gradio][6])

Recommended route structure:

```text
/                     → Gradio UI
/_health              → health check

/api/capabilities
/api/jobs
/api/jobs/{job_id}
/api/jobs/{job_id}/delete
/api/jobs/{job_id}/download/{file_id}
```

The business logic must **not** live directly inside Gradio callbacks.

Use:

```text
UI
 ↓
service
 ↓
operation
 ↓
command builder
 ↓
FFmpeg runner
```

This makes a REST API easy to add later.

---

# 9. FFmpeg execution engine

This is one of the most important components.

Never execute:

```python
os.system(command)
```

Never execute:

```python
subprocess.run(command, shell=True)
```

All commands must be represented as argument arrays:

```python
[
    "ffmpeg",
    "-i",
    input_path,
    "-c:v",
    "libx264",
    "-crf",
    "23",
    output_path
]
```

and executed with:

```text
shell=False
```

FFmpeg supports transcoding, filtering, stream selection, and stream copying; stream copy should be preferred when an operation does not require decoding/filtering because it avoids re-encoding. ([FFmpeg][7])

Create:

```text
FFmpegCommandBuilder
FFmpegRunner
FFmpegProgressParser
FFprobeService
```

as separate abstractions.

---

# 10. Detect FFmpeg capabilities at startup

Do not blindly assume every encoder/filter exists.

At startup execute:

```text
ffmpeg -version
ffmpeg -encoders
ffmpeg -decoders
ffmpeg -filters
ffmpeg -formats
```

Create a capability object:

```json
{
  "libx264": true,
  "libx265": true,
  "libsvtav1": false,
  "aac": true,
  "libopus": true,
  "subtitles_filter": true
}
```

Hide unavailable UI options automatically.

For example:

```text
if AV1 encoder unavailable:
    do not display AV1
```

This prevents UI choices from producing predictable runtime failures.

---

# 11. FFprobe analysis

Immediately after upload, run FFprobe and parse JSON.

Show:

```text
Filename
File size
Duration
Container

Video
    Codec
    Width
    Height
    FPS
    Bitrate
    Pixel format
    HDR information where available

Audio
    Codec
    Sample rate
    Channels
    Bitrate
    Language

Subtitles
    Codec
    Language
```

Store this as a typed `MediaInfo` model.

Use it for validation and for automatically generating commands.

---

# 12. CPU UI

Use `gr.Blocks`, not a simple `gr.Interface`, because this project needs a custom multi-tab layout. Gradio Blocks is specifically designed for more complex layouts and event flows. ([Gradio][8])

Main navigation:

```text
Media Toolbox

Video
Audio
Subtitles
Utilities
Advanced
History
```

Place one shared uploader at the top of each primary media section, above its
nested operation tabs:

```text
Video
    shared video upload + probe card
    Compress | Target Size | Resize | Convert | ...

Audio
    shared audio-or-video upload + probe card
    Extract | Convert | Compress | Sample Rate | ...

Subtitles
    shared video upload + probe card
    shared optional subtitle file
    Extract | Add Track | Burn
```

Every subtool in a section must read the same upload component and probe state,
so switching subtools never requires uploading the source again. The selection
is scoped to the current Gradio session and remains temporary; it is not copied
to the shared bucket.

Tools that inherently need extra inputs request only those extras:

```text
Merge A+V      shared video + one audio file
Concatenate    shared video first + additional clips
Add Track      shared video + shared subtitle file
Burn           shared video + shared optional subtitle file or embedded stream
```

Use generic file uploads rather than forcing Gradio to convert uploads through
`gr.Video`.

---

# 13. CPU feature set

## A. Compress Video

Inputs:

```text
Codec
    H.264
    H.265
    AV1 if available

Quality mode
    Quick
    Balanced
    High Quality
    Custom

Resolution
    Original
    2160p
    1440p
    1080p
    720p
    480p
    Custom

Audio bitrate
    Original when possible
    320k
    256k
    192k
    128k
    96k
    64k

Remove audio
    yes/no
```

Suggested H.264 defaults:

```text
Quick
preset=veryfast
crf=24

Balanced
preset=medium
crf=23

High Quality
preset=slow
crf=20
```

Do not upscale unless:

```text
Allow upscaling = enabled
```

Resolution changes should maintain aspect ratio and produce codec-safe even dimensions. FFmpeg's scale filter supports preservation of aspect ratio and forcing dimensions divisible by a requested amount. ([FFmpeg][9])

---

# 14. Target File Size

Implement a dedicated mode:

```text
Target size:
[ 200 ] MB
```

Calculate:

```text
total_bitrate =
target_bytes * 8 / duration_seconds

video_bitrate =
total_bitrate - audio_bitrate
```

Apply a configurable container-overhead safety factor.

For example:

```text
usable target = requested target × 0.97
```

Perform **two-pass encoding** for this operation.

Initially support target-size encoding with H.264.

Later add H.265.

UI result:

```text
Target             200 MB
Actual             197.4 MB
Difference         -1.3%
```

Reject impossible configurations such as a requested target that leaves essentially no bitrate for video.

---

# 15. Resize Video

Provide:

```text
2160p
1440p
1080p
720p
480p
360p
Custom
```

Options:

```text
Preserve aspect ratio
Prevent upscaling
Fit inside dimensions
Exact dimensions
```

For exact dimensions offer:

```text
Stretch
Crop
Letterbox
```

---

# 16. Extract Audio

Support:

```text
MP3
AAC / M4A
Opus
FLAC
WAV
```

Modes:

```text
Copy original audio
Convert audio
```

If stream copy is compatible with the requested container, avoid re-encoding. FFmpeg stream copy operates without decoding/encoding and therefore avoids quality loss from transcoding. ([FFmpeg][7])

---

# 17. Remove Audio

Implement:

```text
video stream → copy
audio stream → remove
```

Do not re-encode video when unnecessary.

---

# 18. Format Conversion

Support:

```text
MP4
MKV
MOV
WebM
```

Modes:

```text
Auto
Remux only
Re-encode
```

`Auto` should determine whether selected streams are valid in the target container.

If yes:

```text
stream copy
```

If no:

```text
transcode
```

---

# 19. Trim / Cut

Inputs:

```text
Start time
End time
```

Support:

### Fast cut

Use stream copying.

Display:

```text
Fast, no quality loss.
Cut position may not be frame-exact.
```

### Accurate cut

Decode/re-encode as required.

Display:

```text
Frame-accurate but slower.
```

---

# 20. FPS conversion

Options:

```text
Original
60
50
30
25
24
Custom
```

Use FFmpeg's FPS/filtering functionality. ([FFmpeg][9])

---

# 21. Rotate and flip

Operations:

```text
90° clockwise
90° counterclockwise
180°
Flip horizontal
Flip vertical
```

---

# 22. Crop

UI:

```text
Original resolution: 1920×1080

X
Y
Width
Height
```

Also presets:

```text
16:9
9:16
4:3
1:1
21:9
```

Provide a simple preview before processing.

---

# 23. Change speed

Support:

```text
0.25×
0.5×
0.75×
1.25×
1.5×
2×
Custom
```

Change both:

```text
video timestamps
audio tempo
```

Do not simply alter video timestamps while leaving audio unchanged.

---

# 24. Audio tools

Create a dedicated Audio section containing:

```text
Convert audio
Compress audio
Change bitrate
Change sample rate
Stereo → Mono
Mono → Stereo
Normalize volume
Trim
Change speed
Extract segment
```

For normalization provide:

```text
Simple normalize
EBU loudness normalization
```

---

# 25. Browser/Device Compatibility preset

Create one-button:

# Make Compatible

Output:

```text
Container: MP4
Video: H.264
Audio: AAC
Pixel format: yuv420p
Fast-start enabled
```

This should be optimized for broad browser/mobile playback rather than maximum compression.

---

# 26. Optimize MP4 for streaming

Add:

```text
Optimize for web streaming
```

Move MP4 metadata required for playback startup appropriately using FFmpeg's fast-start functionality.

If no codec change is necessary, stream-copy rather than re-encoding.

---

# 27. Subtitles

Support three groups.

### Extract

List subtitle streams:

```text
English — SRT
Bengali — ASS
Japanese — PGS
```

Allow compatible text streams to be exported.

### Add subtitle track

Input:

```text
video
subtitle.srt
language
title
```

Mux into compatible container.

### Burn subtitles

Render subtitles into video.

This necessarily requires video re-encoding because FFmpeg filters operate on decoded frames. ([FFmpeg][7])

---

# 28. Merge video and audio

Inputs:

```text
video file
audio file
```

Options:

```text
Replace existing audio
Keep existing audio + add new track
Shortest stream determines length
Video determines length
```

Prefer stream copy when codecs/container allow it.

---

# 29. Concatenate clips

Support multiple uploaded clips.

Modes:

```text
Fast join
Compatible join
```

Fast join:

```text
only when stream parameters are compatible
```

Compatible join:

```text
normalize resolution
normalize FPS
normalize codecs
then concatenate
```

Process sequentially because the CPU Space has limited CPU capacity.

---

# 30. Video → GIF

Inputs:

```text
Start
End
Width
FPS
```

Use FFmpeg's palette-generation approach rather than naïve GIF conversion.

Put conservative limits on duration because GIF files become enormous quickly.

---

# 31. Screenshot / thumbnail

Allow:

```text
Choose timestamp
Extract image
```

Output:

```text
JPG
PNG
WebP
```

Useful both independently and for generated thumbnails.

---

# 32. Remove metadata

Provide:

```text
Remove metadata
```

Optionally keep:

```text
chapters
rotation metadata
language metadata
```

---

# 33. Advanced FFmpeg mode

This is mandatory.

UI:

```text
Input
    uploaded file

Output extension
    mp4

Custom FFmpeg arguments

    -c:v libx265 -crf 27 -preset slow
    -c:a aac -b:a 128k

Generated command preview

    ffmpeg -i input.mkv ...
```

The actual user input must **never contain the input or output filename**.

The application controls those.

Process custom arguments using:

```python
shlex.split()
```

and then append them into the subprocess argument list.

Still use:

```text
shell=False
```

---

# 34. Advanced-mode security

Reject advanced arguments attempting to:

```text
add another input
specify an arbitrary output
use remote URLs
access arbitrary files
invoke network protocols
create pipe/tee outputs
escape the working directory
```

At minimum reject:

```text
-i
http://
https://
ftp://
rtmp://
rtsp://
file:
concat:
subfile:
../
```

Do not provide arbitrary shell execution.

The advanced mode is:

```text
advanced FFmpeg arguments
```

not:

```text
terminal
```

---

# 35. FFmpeg progress reporting

Do not just show a spinner.

Use FFmpeg machine-readable progress and known media duration to calculate:

```text
0–100%
```

UI:

```text
Compressing...

████████████████░░░░░ 78%

Processed       08:13 / 10:31
Speed           0.84×
Elapsed         09:45
```

Gradio has explicit progress-tracking support that can be updated during long-running functions. ([Gradio][10])

Also keep the last N FFmpeg log lines for failure diagnostics.

---

# 36. Cancellation

Add:

```text
Cancel
```

Each running job needs a tracked subprocess.

When cancelled:

```text
terminate FFmpeg
wait briefly
kill if necessary
delete partial output
delete temporary directory
mark job cancelled
```

Never upload cancelled output to the bucket.

---

# 37. CPU concurrency

Default:

```text
MAX_CONCURRENT_CPU_JOBS=1
```

One FFmpeg encoding already has access to the two CPU cores available on CPU Basic.

Allow the rest to queue.

Expose this as configuration rather than hardcoding it.

---

# 38. Result UI

After completion show:

```text
✓ Complete

Input
1.43 GB

Output
416 MB

Reduction
70.9%

Duration
10:12

Processing time
08:42

Expires
23h 59m

[ Preview ]
[ Download ]
[ Delete Now ]
```

For operations where multiple files are produced:

```text
Outputs

vocals.flac            [Download]
instrumental.flac      [Download]
drums.flac             [Download]
bass.flac              [Download]

[Download all as ZIP]
[Delete job]
```

---

# 39. History page

Both Spaces should use the shared bucket manifest format.

Display:

```text
Recent Jobs

Compress Video
vacation.mp4
416 MB
CPU
expires in 22h 13m

[Download]
[Delete]

────────────────────

Speech to Text
meeting.mp4
GPU
3 outputs
expires in 20h 44m

[TXT]
[SRT]
[VTT]
[Delete]
```

History should:

```text
hide expired jobs
sort newest first
show CPU and GPU jobs
support manual deletion
```

No SQL database is needed for V1.

The bucket is the persistence layer.

---

# 40. Exact 24-hour expiry implementation

On successful processing:

```text
completed_at = UTC now
expires_at = completed_at + 24 hours
```

When listing jobs:

```python
if now >= expires_at:
    don't display it
```

When downloading:

```python
if now >= expires_at:
    return HTTP 410 Gone
```

This check must cover **every** download and preview path. Do not pass physical
bucket paths to `gr.File`, `gr.Video`, `gr.Audio`, or `gr.Image`, and do not
whitelist the bucket root with Gradio `allowed_paths`. Otherwise Gradio's file
route can bypass the expiry-aware FastAPI endpoint. UI downloads and previews
must use an application route that resolves the manifest, checks expiry, then
serves the file.

When cleanup runs:

```python
if directory_expiry <= now:
    permanently delete prefix
```

The scheduled cleanup should run hourly. HF Scheduled Jobs support `@hourly` and normal CRON expressions. ([Hugging Face][11])

---

# 41. Cleanup script

Implement:

```text
cleanup/cleanup.py
```

Algorithm:

```text
1. Get current UTC Unix timestamp.

2. List jobs/ prefixes.

3. Parse:
   <expiry>_<uuid>

4. Ignore malformed directories safely.

5. For every expired prefix:
   delete all objects under prefix.

6. Retry transient failures.

7. Log:
   prefixes scanned
   expired prefixes
   deleted objects
   reclaimed bytes
   failures

8. Exit 0 if cleanup completed.
```

The Bucket API supports recursive listings and file deletion; batch operations are non-transactional, so cleanup operations should be idempotent and safe to retry. ([Hugging Face][2])

Provide:

```text
--dry-run
```

so the user can test cleanup safely.

Create exactly **one** cleanup schedule for the shared bucket, not one schedule
per Space. It is an HF Scheduled Job, not a process inside either Space. Mount
the CPU Space repository read-only for the script and the bucket read/write:

```bash
hf jobs scheduled run \
  --name media-toolbox-cleanup \
  --volume hf://spaces/<username>/media-toolbox-cpu:/workspace:ro \
  --volume hf://buckets/<username>/media-toolbox:/data/media-bucket \
  @hourly python:3.12-slim \
  python /workspace/main/cleanup/cleanup.py --bucket /data/media-bucket
```

After creating it, trigger it once manually and inspect its logs before relying
on the hourly schedule.

---

# 42. GPU Space purpose

The GPU Space is **not another FFmpeg interface**.

It is:

# AI Media Toolbox

Initial navigation:

```text
Transcription
Stem Separation
AI Upscaling
History
```

FFmpeg is still installed in this Space for CPU-side preprocessing and postprocessing.

GPU inference happens only inside functions decorated with:

```python
@spaces.GPU(...)
```

Because the GPU application mounts Gradio inside FastAPI and starts Uvicorn
instead of calling `Blocks.launch()`, it must explicitly invoke the available
`spaces.zero.startup()` hook after all decorated model modules are imported.
Without that registration step, ZeroGPU terminates startup with
`No @spaces.GPU function detected during startup` even when decorated
functions exist. Pass the already-built ASGI application object to
`uvicorn.run`; do not re-import `gpu.app` by string and construct the app twice.
The mount must also set `ssr_mode=False`: the Hugging Face Gradio runtime can
enable SSR by environment variable, causing Gradio's Node process and Uvicorn
to both bind port 7860. The GPU Space uses client-side Gradio rendering so
Uvicorn remains the only public HTTP server and continues to serve the FastAPI
health, job, expiry-checked download, and delete routes.

ZeroGPU assigns the actual GPU while the decorated function runs. ([Hugging Face][3])

---

# 43. GPU dependency constraints

Start with:

```text
Python 3.10.13
PyTorch 2.8.x
Gradio
spaces
transformers
accelerate
huggingface_hub
```

Keep GPU Python dependencies in root `requirements.gpu.txt` and Debian
packages in root `packages.gpu.txt`, never inside `main/gpu/`. During GPU
deployment these are staged as `requirements.txt` and `packages.txt`, the
filenames consumed by the Gradio SDK builder. The CPU Docker build installs
only root `requirements.cpu.txt` and is therefore isolated from the GPU
dependency set. Use `huggingface_hub>=1.8.0` anywhere Space/bucket volume APIs
are required.

Python 3.10.13 and PyTorch starting at 2.8 are currently in ZeroGPU's supported combinations. ([Hugging Face][3])

Avoid:

```python
torch.compile()
```

because HF currently states that ZeroGPU does not support `torch.compile`. ([Hugging Face][3])

Use SDPA or other compatible optimizations where appropriate.

---

# 44. GPU operation pipeline

Always separate processing into:

```text
CPU preprocessing
        ↓
GPU inference
        ↓
CPU postprocessing
        ↓
bucket upload
```

Example:

```text
Video
 ↓
FFmpeg extracts audio             CPU
 ↓
Whisper inference                 GPU
 ↓
Generate SRT/VTT/TXT              CPU
 ↓
Bucket                            CPU
```

Do not hold a GPU allocation while:

```text
uploading files
zipping files
copying outputs to bucket
generating basic metadata
running normal FFmpeg muxing
```

Those operations waste GPU quota.

---

# 45. GPU Tool 1 — Speech transcription

Use:

```text
openai/whisper-large-v3-turbo
```

as the default transcription model.

The current model card describes large-v3-turbo as an 809M-parameter pruned large-v3 model with only four decoding layers, designed to be substantially faster with a small quality trade-off. It supports multilingual ASR, speech translation to English, timestamps, and chunked long-form transcription. ([Hugging Face][12])

Inputs:

```text
Video or audio

Language
    Auto
    English
    Bengali
    Hindi
    etc.

Task
    Transcribe
    Translate to English

Timestamps
    Segment
    Word-level
```

Outputs:

```text
TXT
SRT
VTT
JSON
```

---

# 46. Whisper preprocessing

For uploaded video:

```text
extract audio with FFmpeg
↓
mono
↓
16 kHz PCM
↓
Whisper
```

Do not send the full video frames through the model.

For long input use chunked long-form transcription rather than trying to process the entire waveform as one tensor. Whisper's current HF model documentation explicitly supports long audio and documents a chunked strategy for faster single-file transcription. ([Hugging Face][12])

---

# 47. Whisper subtitle output

Generate accurate:

```text
SRT
VTT
```

from returned timestamps.

Also offer:

```text
Download transcript
Download SRT
Download VTT
Download JSON
```

Optionally:

```text
Burn generated subtitles into original video
```

but perform the subtitle burn using CPU FFmpeg after Whisper GPU inference finishes.

---

# 48. GPU Tool 2 — Stem Separation

Use:

```text
Demucs v4
htdemucs
```

Initial modes:

```text
Vocals + Instrumental
4 Stem
    Vocals
    Drums
    Bass
    Other
```

The currently maintained Demucs repository identifies `htdemucs` as its default Hybrid Transformer model and supports vocals/drums/bass/other separation. The maintainer also notes that development is no longer very active, so this dependency should be isolated and pinned rather than spread throughout the application. ([GitHub][13])

Do **not** make Demucs a hard dependency for the rest of the GPU app.

Implement:

```text
ENABLE_DEMUCS=true
```

so it can be disabled if a future dependency conflict occurs.

---

# 49. Stem outputs

Offer:

```text
WAV
FLAC
MP3
```

For two-stem mode:

```text
vocals
instrumental
```

For four-stem:

```text
vocals
drums
bass
other
```

ZIP all outputs optionally.

Persist each output under the same bucket job.

---

# 50. GPU Tool 3 — AI Upscaling

Use Real-ESRGAN initially.

Modes:

```text
Image
Short Video
```

Models/presets:

```text
General
Anime/illustration
```

Scale:

```text
2×
4×
```

The official Real-ESRGAN project provides models for general image restoration and dedicated anime-video models, including models usable at x2/x3/x4 scales. ([GitHub][14])

---

# 51. Image upscale flow

```text
Upload image
 ↓
validate
 ↓
Real-ESRGAN
 ↓
PNG/WebP/JPG
 ↓
bucket
```

Controls:

```text
Scale
Model
Output format
Denoise strength where supported
```

---

# 52. Video upscale flow

This should initially be marked:

```text
Experimental
```

Flow:

```text
video
 ↓
FFprobe
 ↓
extract frames/chunks
 ↓
GPU upscale
 ↓
reassemble frames with FFmpeg
 ↓
copy/re-encode original audio
 ↓
bucket
```

Do **not** retain every video frame indefinitely.

Use:

```text
chunk
process
write
delete temporary frames
next chunk
```

For V1 impose conservative configurable limits:

```text
GPU_VIDEO_MAX_DURATION
GPU_VIDEO_MAX_PIXELS
GPU_VIDEO_MAX_FILE_SIZE
```

Do not hardcode assumptions that long 4K videos will fit within ZeroGPU quota.

---

# 53. ZeroGPU duration handling

Do not blindly decorate every GPU operation with:

```python
@spaces.GPU(duration=600)
```

HF's current scheduler defaults to 60 seconds but lets the application provide either a fixed or dynamic duration. Hugging Face explicitly states that declaring shorter realistic durations helps queue priority. ([Hugging Face][3])

Implement one estimator per operation:

```text
estimate_whisper_duration()
estimate_demucs_duration()
estimate_upscale_duration()
```

Benchmark them on actual ZeroGPU hardware.

Then include a safety margin.

---

# 54. ZeroGPU size

Default every operation to:

```text
large
48 GB VRAM
```

Only request:

```text
xlarge
96 GB VRAM
```

after an actual demonstrated need.

HF currently charges xlarge at **2× ZeroGPU quota consumption** and warns it generally has a higher probability of queueing. ([Hugging Face][3])

Therefore:

```text
do not use xlarge just because it exists
```

---

# 55. GPU quota UX

At the top of the GPU Space display:

```text
ZeroGPU processing uses your Hugging Face GPU quota.

Large or long videos may consume significant quota.
```

PRO currently receives 40 included ZeroGPU minutes per 24-hour usage window, with additional GPU time available via paid credits. ([Hugging Face][3])

Do not attempt to invent a remaining-quota counter unless HF exposes a reliable API for it.

---

# 56. Shared CPU/GPU history

GPU jobs should use exactly the same manifest schema as CPU jobs except:

```json
"source": "gpu"
```

Example:

```json
{
  "source": "gpu",
  "operation": "whisper_transcription"
}
```

The CPU Space can therefore show GPU jobs and vice versa.

---

# 57. V2 cross-Space handoff

Do not implement this until both Spaces work independently.

Later add:

```text
CPU result
 ↓
[Open in AI Toolbox]
 ↓
GPU Space
 ↓
use existing bucket object
```

This avoids uploading a file twice.

Example:

```text
compress video on CPU
↓
bucket
↓
send job ID to GPU Space
↓
Whisper loads CPU output
```

Implement only after V1.

---

# 58. Local temporary directories

Every job gets:

```text
/tmp/media-toolbox/<uuid>/
```

Example:

```text
/tmp/media-toolbox/
    930c4.../
        input
        working/
        output.part.mp4
        output.mp4
```

Never use user-provided filenames as directories.

Always sanitize displayed filenames.

---

# 59. Atomic-ish result handling

Write:

```text
output.part.mp4
```

first.

After FFmpeg successfully exits:

```text
ffprobe output.part.mp4
```

Verify:

```text
file exists
file size > 0
expected media stream exists
duration reasonable
```

Then rename:

```text
output.part.mp4
→
output.mp4
```

Only then upload it to the bucket.

---

# 60. Failure handling

Every operation must handle:

```text
invalid input
FFprobe failure
FFmpeg non-zero exit
disk full
out of memory
unsupported codec
GPU OOM
ZeroGPU quota exhausted
ZeroGPU queue error
model loading failure
bucket upload failure
user cancellation
Space restart
```

User-facing messages should be concise:

```text
Conversion failed.

FFmpeg could not decode the input video.

Technical details:
[expand]
```

Do not dump a 5,000-line FFmpeg trace directly into the page.

---

# 61. Disk-space protection

Before running:

```text
check available disk
```

Estimate required working space.

Reject a job when:

```text
input + expected output + temporary overhead
```

would leave an unsafe amount of free disk.

CPU Basic's local 50 GB is ephemeral, so the app must never treat it as permanent storage. ([Hugging Face][1])

Provide configurable:

```text
MIN_FREE_DISK_GB
MAX_INPUT_SIZE_GB
```

rather than embedding the values throughout the code.

---

# 62. Security requirements

V1 should accept:

```text
local browser uploads only
```

Do not support:

```text
Download video from URL
YouTube URL
HTTP media input
remote FFmpeg input
```

That can be added separately later.

This dramatically reduces SSRF/network-protocol problems.

---

# 63. Hugging Face secrets

Never commit credentials.

Configuration:

```text
HF_BUCKET_ID=<username>/media-toolbox

RETENTION_HOURS=24
MAX_CONCURRENT_CPU_JOBS=1
WORK_DIR=/tmp/media-toolbox
BUCKET_MOUNT=/data/media-bucket
```

Sensitive values go in HF Space Secrets.

HF Docker Spaces expose runtime secrets as environment variables rather than requiring them to be embedded into source or images. ([Hugging Face][4])

---

# 64. Health endpoints

CPU:

```json
GET /_health

{
  "status": "ok",
  "ffmpeg": true,
  "ffprobe": true,
  "bucket": true
}
```

GPU UI should also expose a basic health function containing:

```text
application loaded
bucket accessible
models configured
```

Do not allocate ZeroGPU merely for health checking.

---

# 65. Startup diagnostics

CPU Space startup log:

```text
Media Toolbox v1.0.0

FFmpeg: 8.x
FFprobe: OK

Encoders:
H264      yes
H265      yes
AV1       no
AAC       yes
Opus      yes

Bucket:
connected

Retention:
24 hours
```

GPU:

```text
AI Media Toolbox v1.0.0

ZeroGPU mode detected

Whisper     enabled
Demucs      enabled
Real-ESRGAN enabled

Bucket      connected
```

Never log tokens.

---

# 66. Testing strategy

> **Deviation (deliberate):** this repository is kept **without test files**
> at the user's request. Verification is manual instead: `python -m compileall`,
> booting the app, driving operations with synthetic FFmpeg lavfi media
> (`testsrc2`, `sine`) through the JobManager/API, and running
> `cleanup/cleanup.py --dry-run` twice (idempotency). See the verification
> checklist in `AGENTS.md`. The unit-test list below is retained as a review
> checklist for those manual verifications.

The AI agent must write tests alongside implementation.

Do not wait until the end.

### Unit tests

Test:

```text
FFprobe JSON parsing
filename sanitization
job ID creation
manifest serialization
expiry calculation
target bitrate calculation
resolution calculations
FFmpeg argument construction
advanced argument validation
bucket prefix parsing
cleanup expiry detection
output naming
media type detection
```

### Integration tests

Generate synthetic media with FFmpeg itself.

For example:

```text
5-second color/test-pattern video
sine-wave audio
subtitle fixture
multiple audio streams
```

Avoid committing large real videos into the repository.

---

# 67. CPU acceptance tests

The complete CPU Space must successfully perform:

```text
1080p → 720p
H.264 compression
target-size compression
MP4 → MKV remux
MKV → MP4 conversion
video → MP3
video → FLAC
remove audio
fast trim
accurate trim
30 FPS conversion
rotate
crop
speed change
audio normalization
add subtitle track
burn subtitles
merge audio/video
GIF creation
screenshot extraction
metadata removal
advanced FFmpeg parameters
```

Each output must then pass FFprobe validation.

---

# 68. Storage acceptance tests

Test:

```text
successful output uploaded
manifest uploaded
history reads manifest
download works
manual delete works
expired download returns 410
expired jobs hidden
cleanup dry-run identifies job
cleanup deletes expired job
cleanup keeps nonexpired job
```

Also test cleanup twice.

Second run must still succeed.

That proves idempotency.

---

# 69. GPU acceptance tests

Whisper:

```text
30-second English clip
Bengali clip
automatic language detection
TXT
SRT
VTT
timestamps
translation-to-English mode
```

Demucs:

```text
short music clip
2-stem
4-stem
valid audio outputs
```

Real-ESRGAN:

```text
small JPG
PNG
2×
4×
general model
anime model
```

Then test one deliberately unsupported/oversized media input and verify the user receives a useful error.

---

# 70. ZeroGPU-specific testing

Do not consider the GPU application finished merely because:

```text
torch.cuda.is_available()
```

works locally.

Deploy it to actual ZeroGPU and test:

```text
queue
GPU allocation
model transfer
inference
GPU release
second invocation
longer invocation
error recovery
quota-related failure
```

This is particularly important because HF explicitly notes that ZeroGPU compatibility can differ from conventional dedicated GPU Spaces. ([Hugging Face][3])

---

# 71. UI design requirements

Both Spaces should visually look like one product.

Use the same:

```text
title
spacing
cards
icons
typography
result layout
history layout
```

CPU title:

# Media Toolbox

GPU title:

# Media AI Toolbox

Each should contain a clear link to the other.

---

# 72. Mobile support

The interface must work on:

```text
desktop
tablet
mobile
```

On narrow screens:

```text
two-column option forms
→
single column
```

Do not make horizontal scrolling necessary for ordinary controls.

---

# 73. Command preview

For FFmpeg operations add:

```text
Advanced details ▾
```

Inside:

```text
Generated FFmpeg command
```

Example:

```text
ffmpeg -i input.mp4 \
  -vf scale=-2:720 \
  -c:v libx264 \
  -preset medium \
  -crf 23 \
  -c:a aac \
  -b:a 128k \
  output.mp4
```

This is informational.

Execution must still use the safe argument array.

---

# 74. Configuration system

Centralize settings.

Example:

```python
class Settings:
    retention_hours
    bucket_id
    bucket_mount_path
    work_directory
    max_upload_size
    max_cpu_concurrent_jobs
    ffmpeg_path
    ffprobe_path

    whisper_model
    enable_demucs
    enable_realesrgan

    gpu_video_max_duration
    gpu_video_max_pixels
```

Environment variables override defaults.

No magic constants scattered through operation modules.

---

# 75. Logging

Use structured logging.

Every log entry related to a job should include:

```text
job_id
operation
source
stage
duration
```

Example:

```text
job=a31f5f
source=cpu
operation=compress
stage=ffmpeg
elapsed=521.3
status=success
```

Do not log personal media contents.

---

# 76. Metrics

No external monitoring system is required initially.

Maintain simple internal counters:

```text
jobs completed
jobs failed
jobs cancelled

CPU processing seconds

input bytes
output bytes

bucket uploads
bucket upload failures

GPU tool invocations
```

Expose them only through logs initially.

---

# 77. README documentation

Maintain exactly one source README at repository root. Do not add a README
under `main/gpu/`. The same README body must cover both Spaces:

```text
Purpose
Features
Hardware
Environment variables
HF secrets
Bucket setup
Local development
Deployment
Known limitations
Security model
Retention policy
Troubleshooting
```

The README should clearly state:

```text
Files are retained for 24 hours.
```

The GPU sections should additionally explain ZeroGPU quota. Before each
deployment the user manually switches only the root frontmatter between the
CPU Docker block and GPU Gradio block from §4. `deploy_space.py` reads and
validates this metadata but must never modify it.

---

# 78. Docker requirements for CPU

Use a slim Debian/Python base.

Install OS packages including:

```text
ffmpeg
ca-certificates
fonts
libass dependencies if required
```

Use a non-root runtime user.

HF Docker Spaces run containers with UID 1000 expectations and document configuring the image accordingly. ([Hugging Face][4])

Launch:

```text
uvicorn app:app
    --host 0.0.0.0
    --port 7860
```

---

# 79. GPU dependency infrastructure

The Gradio ZeroGPU Space should install required system packages from source
file **root** `packages.gpu.txt`, staged as `packages.txt`, including at
minimum:

```text
ffmpeg
```

Keep this file to one Debian package name per non-empty line. Do not add `#`
comments: the Hugging Face Gradio builder passes every whitespace-separated
token through `xargs` to `apt-get`, so comment text is treated as package names.

plus libraries required by chosen audio/image models.

Keep GPU preprocessing FFmpeg features minimal rather than duplicating the complete CPU toolbox.

The root CPU and GPU infrastructure sources coexist without using Hugging
Face's reserved filenames in the development repository. `Dockerfile.cpu`
explicitly installs `requirements.cpu.txt`; the deploy helper publishes only
the selected target's infrastructure.

---

# 80. Implementation order

The agent should **not attempt everything simultaneously**.

### Phase 1 — Foundation

Implement:

```text
project structure
configuration
shared models
temporary job directories
FFprobe
FFmpeg runner
capability detection
```

Exit criteria:

```text
upload file
probe it
run one FFmpeg command
return output
```

### Phase 2 — CPU core

Implement:

```text
compress
resize
extract audio
remove audio
convert
trim
```

Exit criteria:

```text
all have tests
all work through UI
```

### Phase 3 — Persistence

Implement:

```text
private bucket integration
manifest
24-hour expiry
history
download
delete now
cleanup script
```

Exit criteria:

```text
restart Space
history remains available
```

HF explicitly recommends Storage Buckets when Space data must survive restarts because ordinary Space storage is ephemeral. ([Hugging Face][5])

### Phase 4 — CPU advanced tools

Implement:

```text
target size
FPS
crop
rotate
speed
audio tools
subtitles
merge
concatenate
GIF
screenshots
metadata
compatibility
advanced FFmpeg
shared per-section upload contexts for Video, Audio, and Subtitles
```

### Phase 5 — GPU foundation

Implement:

```text
main/gpu/app.py and the main/gpu/ package structure from §3
root requirements.gpu.txt for GPU Python dependencies
root packages.gpu.txt for ffmpeg and required system libraries
Gradio ZeroGPU Space
shared bucket
GPU decorator architecture
pre/postprocessing
expiry-checked download/preview routes (never direct bucket paths)
```

Deployment exit criteria:

```text
change root README to sdk: gradio + app_file: main/gpu/app.py
run deploy_space.py --dry-run and confirm gradio / main/gpu/app.py / zero-a10g
deploy to <username>/media-toolbox-gpu
attach the existing <username>/media-toolbox bucket at /data/media-bucket
verify GPU history can read CPU manifests
restore root README CPU Docker metadata after deployment
do not create another Dockerfile or README
```

### Phase 6 — Whisper

Build Whisper completely before adding another model.

Exit criteria:

```text
audio → TXT/SRT/VTT
video → TXT/SRT/VTT
bucket persistence
```

### Phase 7 — Demucs

Implement isolated Demucs module.

### Phase 8 — Real-ESRGAN image

Implement image upscale.

### Phase 9 — Real-ESRGAN video

Only after image inference works reliably.

### Phase 10 — polish

Implement:

```text
history improvements
cross-Space links
better progress
error messages
mobile UI
documentation
integration tests
```

---

# 81. Things explicitly NOT to build in V1

The AI agent should **not scope-creep** into:

```text
YouTube downloader
torrent support
cloud-drive integration
remote URLs
user accounts/database
payments
public sharing
permanent storage
video editor timeline
AI video generation
AI image generation
dedicated GPU Space
Kubernetes
Redis
Celery
PostgreSQL
```

None are necessary.

---

# 82. Important architectural rules for the agent

These rules should be treated as non-negotiable:

1. **CPU FFmpeg processing stays in the CPU Docker Space.**
2. **AI inference stays in the ZeroGPU Gradio Space.**
3. **Both use the same private HF Storage Bucket.**
4. **Input files are normally temporary and are not persisted.**
5. **Successful output files are persisted for 24 hours.**
6. **Expiry is enforced at download time, not only by cleanup.**
7. **Cleanup runs hourly.**
8. **Every job gets a UUID.**
9. **No SQL database in V1.**
10. **No shell=True.**
11. **No arbitrary command execution.**
12. **FFmpeg commands must be generated as arrays.**
13. **FFprobe validates input and output.**
14. **Partial outputs are never persisted as completed results.**
15. **CPU concurrency defaults to one job.**
16. **GPU allocation surrounds inference only.**
17. **Do not perform normal FFmpeg work inside `@spaces.GPU`.**
18. **Do not use `torch.compile` on ZeroGPU.** ([Hugging Face][3])
19. **Do not use ZeroGPU xlarge unless required by measured VRAM demand.** ([Hugging Face][3])
20. **The application must remain usable if Demucs or Real-ESRGAN is disabled.**
21. **Maintain one root README; manually switch only its Space frontmatter before CPU/GPU deployment.**
22. **Maintain one Dockerfile source, root `Dockerfile.cpu`, for the CPU Space; never add a GPU Dockerfile.**
23. **All application logic lives under `main/`; the GPU entrypoint is `main/gpu/app.py`.**
24. **Root infrastructure sources are target-qualified; deploy maps them to the SDK-required filenames and never rewrites README.**
25. **CPU defaults to `cpu-basic`; Gradio GPU defaults to `zero-a10g`; an explicit CLI hardware override is allowed.**
26. **Both Space repositories are public through Hub settings; the shared Storage Bucket remains private.**
27. **Both Spaces attach the same bucket read/write at `/data/media-bucket`.**
28. **Create one hourly cleanup job for the shared bucket, with explicit Space-repo and bucket volume mounts.**
29. **Never expose physical bucket paths through Gradio file components or `allowed_paths`; all downloads/previews must enforce expiry.**
30. **Video, Audio, and Subtitle subtools must reuse one session-scoped upload and probe state per section; only operation-specific secondary files get separate uploaders.**

---

# 83. Final V1 user experience

When everything is finished, the CPU workflow should look like:

```text
Media Toolbox
──────────────────────────────────────────────

Upload
movie.mkv

✓ Analysed

3840×2160
HEVC
60 FPS
01:43:12
1.82 GB

Operation
Compress Video

Resolution
1080p

Codec
H.264

Quality
Balanced

Audio
AAC 128 kbps

              [ Convert ]

█████████████░░░░░ 67%

──────────────────────────────────────────────

✓ Finished

Input       1.82 GB
Output      518 MB
Saved       71.5%

[Preview]
[Download]
[Delete now]

Expires in 23h 59m
```

And GPU:

```text
Media AI Toolbox
──────────────────────────────────────────────

Upload
meeting.mp4

Tool
Speech to Text

Model
Whisper Large V3 Turbo

Language
Auto Detect

Output
☑ TXT
☑ SRT
☑ VTT

             [ Transcribe ]

Waiting for GPU...
GPU allocated...
Transcribing...
Generating subtitles...

──────────────────────────────────────────────

✓ Finished

Detected language:
English

[Transcript]

[Download TXT]
[Download SRT]
[Download VTT]

Expires in 23h 59m
```

---

# 84. Definition of "finished"

The AI agent should not declare the project complete merely because the pages load.

The project is finished only when this entire path works:

```text
create CPU Space
        ↓
upload real video
        ↓
probe
        ↓
compress
        ↓
progress shown
        ↓
output validated
        ↓
upload to bucket
        ↓
download
        ↓
Space restart
        ↓
output still appears in history
        ↓
delete manually
        ↓
gone


create GPU Space
        ↓
root README selects sdk: gradio
        ↓
deploy dry-run confirms main/gpu/app.py and zero-a10g
        ↓
existing shared bucket attached at /data/media-bucket
        ↓
upload video
        ↓
extract audio
        ↓
ZeroGPU allocated
        ↓
Whisper transcription
        ↓
SRT created
        ↓
stored in same bucket
        ↓
visible in history
        ↓
download works
        ↓
restore root README to CPU Docker metadata


create test expired job
        ↓
download refused
        ↓
cleanup runs
        ↓
bucket objects deleted
```

Only after all three flows pass should V1 be considered complete.

## One additional instruction I would give the coding agent

**Build the CPU Space completely first, including the bucket and cleanup system, before writing the GPU features.**

That gives the GPU implementation an already-working foundation for job IDs, manifests, persistence, history, downloads, expiry, filenames, and cleanup. The GPU Space then only needs to solve the genuinely GPU-specific problem: inference.

This architecture also leaves a clean path later for things such as **frame interpolation, video background removal, speech denoising, face restoration, OCR, and "Send to AI Toolbox" directly from a CPU result** without redesigning the storage/job system.

[1]: https://huggingface.co/docs/hub/spaces-gpus "Using GPU Spaces · Hugging Face"
[2]: https://huggingface.co/docs/huggingface_hub/guides/buckets "Buckets · Hugging Face"
[3]: https://huggingface.co/docs/hub/spaces-zerogpu "Spaces ZeroGPU: Dynamic GPU Allocation for Spaces · Hugging Face"
[4]: https://huggingface.co/docs/hub/spaces-sdks-docker "Docker Spaces · Hugging Face"
[5]: https://huggingface.co/docs/hub/spaces-storage "Disk usage on Spaces · Hugging Face"
[6]: https://www.gradio.app/docs/gradio/mount_gradio_app?utm_source=chatgpt.com "mount_gradio_app"
[7]: https://ffmpeg.org/ffmpeg.html "ffmpeg Documentation"
[8]: https://gradio.app/docs/gradio/blocks?utm_source=chatgpt.com "Gradio Blocks docs"
[9]: https://ffmpeg.org/ffmpeg-filters.html?utm_source=chatgpt.com "FFmpeg Filters Documentation"
[10]: https://gradio.app/docs/gradio/progress?utm_source=chatgpt.com "Progress"
[11]: https://huggingface.co/docs/hub/jobs-schedule "Schedule Jobs · Hugging Face"
[12]: https://huggingface.co/openai/whisper-large-v3-turbo "openai/whisper-large-v3-turbo · Hugging Face"
[13]: https://github.com/adefossez/demucs "GitHub - adefossez/demucs: Code for the paper Hybrid Spectrogram and Waveform Source Separation · GitHub"
[14]: https://github.com/xinntao/real-esrgan?utm_source=chatgpt.com "Real-ESRGAN aims at developing Practical Algorithms for ..."
