"""Media AI Toolbox (GPU Space) entrypoint.

Selected by the root README frontmatter (`app_file: main/gpu/app.py`) when deploying
the ZeroGPU Gradio Space. FastAPI is the top-level server (health + expiry-
checked job/download routes); the Gradio UI is mounted at '/'.

The shared code beside ``gpu/`` (config, models, manifests, bucket storage,
FFprobe/FFmpeg helpers) is reused by putting ``main/`` on ``sys.path``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

MAIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MAIN_DIR.parent
for _path in (str(REPO_ROOT), str(MAIN_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Local dev keeps secrets in a .env at the repo root; on Hugging Face they
# come from Space secrets, so a missing file or package is fine.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

# Gradio upload temp files live inside the work dir so lifecycle is owned by us.
os.environ.setdefault(
    "GRADIO_TEMP_DIR",
    os.path.join(os.environ.get("WORK_DIR", "/tmp/media-toolbox"), "gradio-tmp"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from core.storage.bucket import JobExpiredError, JobNotFoundError
from core.storage.retention import seconds_until
from core.time_utils import format_countdown

from gpu.backend.config import on_zerogpu, report_zerogpu_startup
from gpu.backend.services import get_services, init_services

services = init_services()

app = FastAPI(title="Media AI Toolbox", version=services.settings.app_version)


@app.get("/_health")
def health() -> dict:
    """Basic health: app loaded, ffmpeg present, bucket accessible, models configured.

    Never allocates ZeroGPU just for health checking (PLAN.md section 64).
    """
    def _ok(binary: str) -> bool:
        try:
            subprocess.run([binary, "-version"], capture_output=True, timeout=10, shell=False)
            return True
        except Exception:
            return False

    g = services.gpu_settings
    return {
        "status": "ok",
        "ffmpeg": _ok(services.settings.ffmpeg_path),
        "ffprobe": _ok(services.settings.ffprobe_path),
        "bucket": services.storage.check_writable(),
        "dev_bucket_fallback": services.dev_bucket,
        "models": {
            "whisper": g.whisper_model,
            "demucs": g.demucs_model if g.enable_demucs else "disabled",
            "realesrgan": "enabled" if g.enable_realesrgan else "disabled",
        },
    }


@app.get("/api/jobs")
def list_jobs() -> dict:
    items = []
    for prefix, manifest in services.storage.list_jobs():
        items.append({
            "prefix": prefix,
            "job_id": manifest.job_id,
            "source": manifest.source,
            "operation": manifest.operation,
            "original_filename": manifest.original_filename,
            "completed_at": manifest.completed_at,
            "expires_at": manifest.expires_at,
            "expires_in": format_countdown(seconds_until(manifest.expires_unix)),
            "outputs": [o.model_dump() for o in manifest.outputs],
        })
    return {"jobs": items}


@app.get("/api/jobs/{prefix}")
def job_detail(prefix: str) -> dict:
    try:
        manifest = services.storage.get_manifest(prefix)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    return manifest.model_dump()


@app.get("/api/jobs/{prefix}/download/{file_id}")
def download(prefix: str, file_id: str) -> FileResponse:
    try:
        path, manifest = services.storage.resolve_output(prefix, file_id)
    except JobExpiredError:
        raise HTTPException(status_code=410, detail="This job has expired.")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    out = next(o for o in manifest.outputs if o.id == file_id)
    return FileResponse(path, filename=out.filename, media_type=out.mime_type)


@app.delete("/api/jobs/{prefix}")
@app.post("/api/jobs/{prefix}/delete")
def delete_job(prefix: str) -> dict:
    if not services.storage.delete_job(prefix):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"deleted": prefix}


import gradio as gr

from gpu.ui.app import build_blocks
from ui.theme import CSS, FORCE_DARK_JS, THEME, ForceDarkThemeMiddleware  # shared visual identity from the CPU Space

blocks = build_blocks()

# Only the ephemeral work dir is exposed to Gradio's file serving. Bucket
# files are served exclusively through the expiry-checked routes above.
_allowed = [str(services.settings.work_dir)]

# Gradio's built-in login page gates the whole UI when credentials are set.
_auth = None
if services.gpu_settings.toolbox_username and services.gpu_settings.toolbox_password:
    _auth = (services.gpu_settings.toolbox_username, services.gpu_settings.toolbox_password)
else:
    logging.getLogger(__name__).warning(
        "TOOLBOX_USERNAME/TOOLBOX_PASSWORD not set; UI login is disabled"
    )

app = gr.mount_gradio_app(
    app,
    blocks,
    path="/",
    theme=THEME,
    css=CSS,
    js=FORCE_DARK_JS,
    auth=_auth,
    allowed_paths=_allowed,
    max_file_size=f"{int(services.settings.max_input_size_gb)}gb",
    # The Gradio SDK enables SSR on Hugging Face. With mount_gradio_app(),
    # its Node process otherwise binds the same public port as Uvicorn.
    # Client-side rendering keeps FastAPI/Uvicorn as the single HTTP server.
    ssr_mode=False,
)

# Gradio issues its auth token as a session cookie, so the login is lost when
# the browser closes. Extend those cookies to 30 days so users stay signed in.
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class _PersistAuthCookieMiddleware:
    """Append Max-Age to Gradio's access-token cookies set by POST /login."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if (
                message["type"] == "http.response.start"
                and scope.get("path", "").rstrip("/") == "/login"
            ):
                headers = []
                for name, value in message.get("headers", []):
                    if (
                        name.lower() == b"set-cookie"
                        and b"access-token-" in value
                        and b"max-age" not in value.lower()
                    ):
                        value += f"; Max-Age={SESSION_MAX_AGE_SECONDS}".encode()
                    headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


if _auth is not None:
    app.add_middleware(_PersistAuthCookieMiddleware)
app.add_middleware(ForceDarkThemeMiddleware)

# ZeroGPU normally discovers decorated functions when Blocks.launch() runs.
# This app mounts Gradio under FastAPI, so explicitly execute the equivalent
# registration hook after build_blocks() has imported every model module.
if on_zerogpu():
    if report_zerogpu_startup():
        logging.getLogger(__name__).info("ZeroGPU functions registered with scheduler")
    else:
        logging.getLogger(__name__).warning(
            "ZeroGPU startup hook unavailable; scheduler registration may fail"
        )


if __name__ == "__main__":
    import uvicorn

    # Pass the already-built ASGI object. Importing "gpu.app:app" here would
    # construct the Gradio application twice under the Gradio SDK launcher.
    server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
    server_port = int(os.getenv("GRADIO_SERVER_PORT", str(services.settings.port)))
    uvicorn.run(app, host=server_name, port=server_port)
