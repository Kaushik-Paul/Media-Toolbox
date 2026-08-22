"""Media Toolbox (CPU Space) entrypoint.

FastAPI is the top-level server; the Gradio UI is mounted at '/'.
Run with:  uvicorn app:app --host 0.0.0.0 --port 7860
"""
from __future__ import annotations

import logging
import os

# Gradio upload temp files live inside the work dir so lifecycle is owned by us.
os.environ.setdefault(
    "GRADIO_TEMP_DIR",
    os.path.join(os.environ.get("WORK_DIR", "/tmp/media-toolbox"), "gradio-tmp"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from fastapi import FastAPI

from backend.download import router as api_router
from backend.services import init_services
from core.http_activity import ExclusiveUploadMiddleware

services = init_services()

app = FastAPI(title="Media Toolbox", version=services.settings.app_version)
app.include_router(api_router)

import gradio as gr

from ui.app import build_blocks

blocks = build_blocks()

from ui.theme import CSS, THEME, THEME_JS

# Uploaded temporary files remain available to Gradio. Results are served only
# by the expiry-checked, byte-range-capable FastAPI download route.
_allowed = [str(services.settings.work_dir)]

app = gr.mount_gradio_app(
    app,
    blocks,
    path="/",
    theme=THEME,
    css=CSS,
    js=THEME_JS,
    allowed_paths=_allowed,
    max_file_size=f"{int(services.settings.max_input_size_gb)}gb",
)
app.add_middleware(ExclusiveUploadMiddleware, activity=services.activity)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=services.settings.port)
