"""Stem Separation tab: Demucs htdemucs vocals/instrumental or 4-stem split."""
from __future__ import annotations

import gradio as gr

from core.media_types import AUDIO_EXTS, VIDEO_EXTS

from gpu.backend.services import get_services
from gpu.models.demucs import FORMATS, MODES
from gpu.ui.common import OpUI, upload_row


def stems_tab():
    if not get_services().gpu_settings.enable_demucs:
        gr.Markdown(
            "<div class='error-card'><b>Stem separation is disabled</b> on this "
            "deployment (ENABLE_DEMUCS=false).</div>"
        )
        return
    gr.Markdown(
        "Separate music into vocals + instrumental, or a full 4-stem split "
        "(vocals, drums, bass, other) with Demucs htdemucs."
    )
    upload = upload_row(
        "Drop audio or video here",
        file_types=sorted("." + e for e in AUDIO_EXTS | VIDEO_EXTS),
    )
    with gr.Row():
        mode = gr.Radio(choices=list(MODES), value=MODES[0], label="Mode")
        fmt = gr.Dropdown(choices=list(FORMATS), value="FLAC", label="Output format")
        make_zip = gr.Checkbox(value=True, label="Also bundle all stems as ZIP")
    ui = OpUI("Separate")
    ui.wire(
        "demucs_separation",
        [upload.file],
        {"mode": mode, "format": fmt, "zip": make_zip},
    )
