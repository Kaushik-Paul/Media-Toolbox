# AGENTS.md — Media Toolbox

Guidance for AI agents working in this repository.

## What this is

A personal media-processing system hosted on Hugging Face Spaces.
Hosted at **https://www.mediatoolbox.pp.ua/**.

- **CPU Space** (`media-toolbox-cpu`, Docker SDK): general FFmpeg operations.
  Lives in `main/`. **This is built and working.**
- **GPU Space** (`media-toolbox-gpu`, Gradio SDK + ZeroGPU): Whisper, Demucs,
  Real-ESRGAN. Lives in `main/gpu/`. **This is built and working.** It reuses the
  same manifest schema, bucket layout, and job-prefix convention.
- Shared private HF Storage Bucket (`media-toolbox`) mounted at
  `/data/media-bucket` exposes outputs for 24 hours. A daily authenticated
  Cloud Run function physically removes job folders older than 30 days.

## Repository layout

```text
README.md            # single README (no Space frontmatter committed)
gradio_sdk.txt       # stash of both ready-to-paste frontmatter blocks (CPU/GPU)
Dockerfile.cpu       # CPU Docker source; deployed as root Dockerfile
requirements.cpu.txt # CPU Python dependencies
requirements.gpu.txt # GPU deps; deployed as root requirements.txt
packages.gpu.txt     # GPU apt deps; deployed as root packages.txt
.dockerignore
main/                # all CPU, GPU, and shared application logic
  app.py             # entrypoint: FastAPI + Gradio mounted at "/"
  scripts/deploy_space.py  # stages and deploys the selected CPU/GPU Space
  scripts/deploy_cleanup_function.py # deploys daily GCP bucket cleanup
  cloud_cleanup/      # Cloud Run function source (HF server-side deletion)
  core/              # config, models, filenames, time_utils, media_types,
                     # manifests, storage/ (bucket + retention)
  backend/           # probe, capabilities, command_builder, ffmpeg_runner,
                     # security, job_manager, services (singletons), download (API)
  operations/        # one module per operation; registry in __init__.py
  ui/                # Gradio Blocks: app, theme, components, tools, history
  cleanup/cleanup.py # optional mounted-bucket cleanup utility
  gpu/               # the GPU Space application code (ZeroGPU)
    app.py            # entrypoint: FastAPI + Gradio; adds main/ to sys.path
    backend/          # config, job manager, pre/postprocessing, services
    models/           # whisper.py, demucs.py, realesrgan.py
    ui/               # common, transcription, stems, upscale, history, app
```

No tests directory — the user asked to keep the repo without tests. Verify
changes by running the app and driving operations manually instead.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate   # venv at REPO ROOT, never in main/
pip install -r requirements.cpu.txt
cd main && python app.py                             # http://127.0.0.1:7860
```

Without a mounted bucket, a dev bucket is created at `$WORK_DIR/bucket`.
Useful overrides: `WORK_DIR=/tmp/mt BUCKET_MOUNT=/tmp/mt-bucket`.

## Hard rules (non-negotiable)

1. Never `shell=True`, never `os.system`. FFmpeg commands are argument arrays
   built with `backend/command_builder.FFmpegCommandBuilder`.
2. FFprobe validates input after upload and output before persistence.
3. Outputs are written locally under `$WORK_DIR/<job_id>/`, verified, then moved
   into the bucket. Partial/cancelled outputs are never persisted.
4. Expiry is enforced at download time (`410 Gone`), not only by cleanup.
5. Bucket layout: `jobs/<expires_unix>_<job_id>/manifest.json + outputs`.
   Manifest schema: `core/models.py::JobManifest` (schema_version 1).
6. CPU concurrency defaults to 1 (`MAX_CONCURRENT_CPU_JOBS`); jobs queue.
7. Remote inputs must go through `core/remote_download.py`: HTTP(S) only,
   public IPs and ports 80/443 only, every redirect revalidated, streamed size
   cap, cancellation, and FFprobe validation. Never hand a URL to FFmpeg.
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

## GPU Space conventions (main/gpu/)

- Reuses `main/core/*`, `main/backend/probe.py`, `main/backend/ffmpeg_runner.py`,
  and `main/ui/theme.py` via sys.path insertion in `main/gpu/app.py` — never
  copy them into `main/gpu/`.
- GPU work happens only inside `@gpu(duration=...)` functions
  (`main/gpu/backend/config.py` wraps `spaces.GPU`, no-op off ZeroGPU). Dynamic
  duration callables must take the same arguments as the decorated function.
- ZeroGPU runs each GPU function in a throwaway fork: load models in the MAIN
  process (`_get_pipeline()` / `_get_model()` called from `run()` before the
  GPU call) so forks inherit them. A model loaded inside the GPU function is
  reloaded on every call.
- torch/transformers/demucs imports stay lazy (inside functions) so the app
  boots with models disabled or deps missing.
- Downloads/previews go through the FastAPI expiry-checked routes, never
  `allowed_paths` on the bucket.
- No `main/gpu/README.md`, no GPU Dockerfile: the root README frontmatter is
  switched to the Gradio block for GPU deploys. The deploy helper maps root
  `requirements.gpu.txt` / `packages.gpu.txt` to the builder filenames.

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
   `main/`); for GPU changes, `python -m compileall main/gpu` from the repo root.
2. Boot: `cd main && ../.venv/bin/python app.py`, check startup diagnostics log
   (encoders detected, bucket connected). For the GPU Space:
   `.venv/bin/python main/gpu/app.py` from the repo root.
3. Generate synthetic media with FFmpeg lavfi (`testsrc2`, `sine`) and run the
   affected operation through `backend.services.init_services().jobs.submit(...)`.
4. Exercise the API: `/_health`, `/api/jobs`, download, delete.
5. If storage/expiry changed: run `cleanup/cleanup.py --bucket <dir> --dry-run`
   then for real, twice (idempotency).

## Deployment

- The committed README carries no Space frontmatter: `deploy_space.py` reads
  `sdk:` from it and exits if missing. Before deploying, paste the matching
  block from `gradio_sdk.txt` at the top of README.md, deploy, then remove it.
- Use `main/scripts/deploy_space.py`: it stages the selected SDK package from
  git-visible files. CPU maps `Dockerfile.cpu` → root `Dockerfile`; GPU maps
  `requirements.gpu.txt` / `packages.gpu.txt` → root `requirements.txt` /
  `packages.txt` and excludes `main/gpu/` from CPU deploys. Each deploy makes
  the remote Space an exact mirror of the staged package and keeps the Space
  public.
  - `--dry-run` previews; `--create-bucket` provisions the shared private
    bucket and attaches it; `--attach-bucket` attaches an existing one.
  - Hardware defaults: `cpu-basic` (docker), `zero-a10g` (gradio).
  - Auth via `hf auth login` or `HF_TOKEN`.
- Attach private bucket at `/data/media-bucket` (read/write).
- Deploy the daily 30-day physical bucket cleanup with
  `python main/scripts/deploy_cleanup_function.py`. It targets `asia-south1`,
  invokes through an OIDC-authenticated Cloud Scheduler job, and deletes through
  the HF server-side bucket API without transferring media through Google.
