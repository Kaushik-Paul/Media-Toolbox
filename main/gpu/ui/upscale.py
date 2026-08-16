"""AI Upscaling tab: Real-ESRGAN for images and short videos."""
from __future__ import annotations

import gradio as gr

from core.media_types import IMAGE_EXTS, VIDEO_EXTS

from gpu.backend.services import get_services
from gpu.models.realesrgan import IMAGE_FORMATS, MODEL_LABELS, SCALES
from gpu.ui.common import OpUI, upload_row


def upscale_tab():
    services = get_services()
    if not services.gpu_settings.enable_realesrgan:
        gr.Markdown(
            "<div class='error-card'><b>AI upscaling is disabled</b> on this "
            "deployment (ENABLE_REALESRGAN=false).</div>"
        )
        return
    g = services.gpu_settings
    gr.Markdown(
        "Upscale images or short videos with Real-ESRGAN. "
        f"Video mode is **experimental**: max {g.gpu_video_max_duration:g}s, "
        f"up to {g.gpu_video_max_pixels // 1000}k pixels, "
        f"{g.gpu_video_max_file_size_gb:g} GB."
    )
    upload = upload_row(
        "Drop an image or short video here",
        file_types=sorted("." + e for e in IMAGE_EXTS | VIDEO_EXTS),
    )
    with gr.Row():
        mode = gr.Radio(choices=["Image", "Short Video"], value="Image", label="Mode")
        model = gr.Dropdown(choices=list(MODEL_LABELS), value=MODEL_LABELS[0], label="Model")
        scale = gr.Dropdown(choices=[str(s) for s in SCALES], value="4", label="Scale")
        out_format = gr.Dropdown(choices=list(IMAGE_FORMATS), value="PNG",
                                 label="Image format (image mode only)")
    ui = OpUI("Upscale")
    ui.wire(
        lambda p: "realesrgan_video" if p.get("mode") == "Short Video" else "realesrgan_image",
        [upload.file],
        {"mode": mode, "model": model, "scale": scale, "format": out_format},
    )
