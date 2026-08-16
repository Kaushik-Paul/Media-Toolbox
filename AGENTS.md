# AGENTS.md — Media Toolbox

Guidance for AI agents working in this repository.

## What this is

A personal media-processing system hosted on Hugging Face Spaces, built per
`PLAN.md` (the authoritative spec — read it before large changes).

- **CPU Space** (`media-toolbox-cpu`, Docker SDK): general FFmpeg operations.
  Lives in `main/`. **This is built and working.**
- **GPU Space** (`media-toolbox-gpu`, Gradio SDK + ZeroGPU): Whisper, Demucs,
  Real-ESRGAN. Lives in `gpu/`. **This is built and working.** It reuses the
  same manifest schema, bucket layout, and job-prefix convention.
- Shared private HF Storage Bucket (`media-toolbox`) mounted at
  `/data/media-bucket` holds outputs for 24 hours.

## Repository layout

```text
README.md            # single README, includes HF Space YAML frontmatter
PLAN.md              # full build specification
Dockerfile           # at repo ROOT: builds the app from main/ (CPU Space builds here)
requirements.txt     # at repo ROOT: GPU Space deps (Gradio Spaces install from root)
packages.txt         # at repo ROOT: apt packages for the GPU Space (ffmpeg, libsndfile1)
.dockerignore
main/                # the CPU Space application code
  app.py             # entrypoint: FastAPI + Gradio mounted at "/"
  requirements.txt
  scripts/deploy_space.py  # deploys the repo root as the Docker Space
  core/              # config, models, filenames, time_utils, media_types,
                     # manifests, storage/ (bucket + retention)
  backend/           # probe, capabilities, command_builder, ffmpeg_runner,
                     # security, job_manager, services (singletons), download (API)
  operations/        # one module per operation; registry in __init__.py
  ui/                # Gradio Blocks: app, theme, components, tools, history
  cleanup/cleanup.py # hourly expiry cleanup job (--dry-run, idempotent)
gpu/                 # the GPU Space application code (ZeroGPU)
  app.py             # entrypoint: FastAPI + Gradio; sys.path adds main/ for shared code
  backend/           # config (GpuSettings + @gpu decorator), job_manager,
                     # preprocessing, postprocessing, services
  models/            # whisper.py, demucs.py, realesrgan.py (lazy heavy imports)
  ui/                # common (OpUI), transcription, stems, upscale, history, app
```

No tests directory — the user asked to keep the repo without tests. Verify
changes by running the app and driving operations manually instead.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate   # venv at REPO ROOT, never in main/
pip install -r main/requirements.txt
cd main && python app.py                             # http://127.0.0.1:7860
```

Without a mounted bucket, a dev bucket is created at `$WORK_DIR/bucket`.
Useful overrides: `WORK_DIR=/tmp/mt BUCKET_MOUNT=/tmp/mt-bucket`.

## Hard rules (from PLAN.md §82 — non-negotiable)

1. Never `shell=True`, never `os.system`. FFmpeg commands are argument arrays
   built with `backend/command_builder.FFmpegCommandBuilder`.
2. FFprobe validates input after upload and output before persistence.
3. Outputs are written locally under `$WORK_DIR/<job_id>/`, verified, then moved
   into the bucket. Partial/cancelled outputs are never persisted.
4. Expiry is enforced at download time (`410 Gone`), not only by cleanup.
5. Bucket layout: `jobs/<expires_unix>_<job_id>/manifest.json + outputs`.
   Manifest schema: `core/models.py::JobManifest` (schema_version 1).
6. CPU concurrency defaults to 1 (`MAX_CONCURRENT_CPU_JOBS`); jobs queue.
7. Local browser uploads only. No URL/remote input (SSRF surface).
8. Advanced mode = validated FFmpeg *arguments* only (`backend/security.py`);
   the app always controls input/output filenames.
9. No SQL database, no Redis/Celery, no user accounts. The bucket is the
   persistence layer.
10. No secrets in source. Config via env vars — see `core/config.py::Settings`.

## Conventions

- Business logic lives in `operations/`, never inside Gradio callbacks.
  Flow: UI → JobManager → operation module → command builder → FFmpegRunner.
- New operations: add `operations/foo.py` with
  `run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult`,
  register it in `operations/__init__.py::OPERATIONS`, add a tab builder in
  `ui/tools.py` using `ui/components.py::OpUI` + `upload_row`, and wire it in
  `ui/app.py`.
- Operations write to `ctx.out_dir` via `part_path_for()` +
  `finalize_output()` (probes then renames from `.part`).
- Use helpers in `operations/base.py` (scale/atempo/crop filters, codec maps,
  `require_encoder`) instead of re-deriving them.
- Check `ctx.capabilities` before using optional encoders/filters; hide
  unavailable choices in the UI (see `_codec_choices()` in `ui/tools.py`).
- Logging: structured, includes `job_id`/`operation`/`stage`; never log media
  contents or tokens.

## GPU Space conventions (gpu/)

- Reuses `main/core/*`, `main/backend/probe.py`, `main/backend/ffmpeg_runner.py`,
  and `main/ui/theme.py` via sys.path insertion in `gpu/app.py` — never copy
  them into `gpu/`.
- GPU work happens only inside `@gpu(duration=...)` functions
  (`gpu/backend/config.py` wraps `spaces.GPU`, no-op off ZeroGPU). Dynamic
  duration callables must take the same arguments as the decorated function.
- ZeroGPU runs each GPU function in a throwaway fork: load models in the MAIN
  process (`_get_pipeline()` / `_get_model()` called from `run()` before the
  GPU call) so forks inherit them. A model loaded inside the GPU function is
  reloaded on every call.
- torch/transformers/demucs imports stay lazy (inside functions) so the app
  boots with models disabled or deps missing (PLAN.md rule 20).
- Downloads/previews go through the FastAPI expiry-checked routes, never
  `allowed_paths` on the bucket.
- No `gpu/README.md`, no GPU Dockerfile: the root README frontmatter is
  switched to the gradio block for GPU deploys; root `requirements.txt` /
  `packages.txt` belong to the GPU Space.

## Gradio 6 gotchas (this repo uses gradio>=6)

- `theme`/`css` are **not** `gr.Blocks()` constructor args anymore — they are
  passed to `gr.mount_gradio_app(...)` in `main/app.py`.
- `gr.Video`/`gr.Image` have no `show_download_button` parameter.
- `gr.File(type="filepath")` yields str path (single) or list[str] (multiple);
  the uploaded temp file keeps the original basename.
- Result files are served from the bucket mount via
  `mount_gradio_app(..., allowed_paths=[...])`.

## Verification checklist after changes

1. `python -m compileall app.py core backend operations ui cleanup` (from
   `main/`); for GPU changes, `python -m compileall gpu` from the repo root.
2. Boot: `cd main && ../.venv/bin/python app.py`, check startup diagnostics log
   (encoders detected, bucket connected). For the GPU Space:
   `.venv/bin/python gpu/app.py` from the repo root.
3. Generate synthetic media with FFmpeg lavfi (`testsrc2`, `sine`) and run the
   affected operation through `backend.services.init_services().jobs.submit(...)`.
4. Exercise the API: `/_health`, `/api/jobs`, download, delete.
5. If storage/expiry changed: run `cleanup/cleanup.py --bucket <dir> --dry-run`
   then for real, twice (idempotency).

## Deployment

- Use `main/scripts/deploy_space.py` (modeled on the Manga-Translator-OCR
  helper): uploads git-visible repo-root files to the Docker Space repo (the
  root Dockerfile builds from `main/`; the root README carries the
  frontmatter). `--dry-run` previews, `--create-bucket` provisions the shared
  bucket. Auth via `hf auth login` or `HF_TOKEN`.
- Attach private bucket at `/data/media-bucket` (read/write).
- Schedule `python main/cleanup/cleanup.py --bucket /data/media-bucket` as an
  `@hourly` HF Job.
