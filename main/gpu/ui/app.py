"""Assemble the GPU Space Gradio Blocks application."""
from __future__ import annotations

import html

import gradio as gr

from gpu.backend.services import get_services
from gpu.ui import history, stems, transcription, upscale
from ui.app import build_basic_tool_tabs
from ui.shell import global_controls

QUOTA_BANNER = """
<div class='media-info-card' style='margin-bottom:0.8rem'>
  <b>ZeroGPU processing uses your Hugging Face GPU quota.</b><br>
  <span class='dim'>Large or long videos may consume significant quota.
  Downloads remain available for 24 hours.</span>
</div>
"""


def _header() -> str:
    cpu_url = get_services().gpu_settings.cpu_space_url
    link = ""
    if cpu_url:
        link = (f" &middot; <a href='{html.escape(cpu_url)}' target='_blank'>"
                "Open the CPU Toolbox</a>")
    return f"""
<div class='app-header'>
  <h1>Media AI Toolbox</h1>
  <p>Whisper transcription, Demucs stem separation, and Real-ESRGAN upscaling.{link}</p>
</div>
"""


def build_blocks() -> gr.Blocks:
    # theme/css are applied in main/gpu/app.py via gr.mount_gradio_app (Gradio 6 API).
    with gr.Blocks(title="Media AI Toolbox") as blocks:
        gr.HTML(_header())
        global_controls()
        gr.HTML(QUOTA_BANNER)
        with gr.Tabs():
            def _gpu_tabs() -> None:
                with gr.Tab("Transcription"):
                    transcription.transcription_tab()
                with gr.Tab("Stem Separation"):
                    stems.stems_tab()
                with gr.Tab("AI Upscaling"):
                    upscale.upscale_tab()

            build_basic_tool_tabs(include_history=False, after_subtitles=_gpu_tabs)
            with gr.Tab("History"):
                history.history_tab()
    return blocks
