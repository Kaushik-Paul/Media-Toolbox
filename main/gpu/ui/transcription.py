"""Transcription tab: Whisper speech-to-text with SRT/VTT/TXT/JSON outputs."""
from __future__ import annotations

import gradio as gr

from gpu.models.whisper import LANGUAGES, TASKS, TIMESTAMP_MODES
from gpu.ui.common import OpUI
from ui.components import UploadContext


def transcription_tab(source: UploadContext):
    gr.Markdown(
        "Transcribe speech from video or audio with Whisper large-v3-turbo. "
        "Produces TXT, SRT, VTT, and JSON. To burn the generated subtitles into "
        "the video, download the SRT and use the CPU Toolbox's Subtitles tab."
    )
    with gr.Row():
        language = gr.Dropdown(choices=list(LANGUAGES.keys()), value="Auto", label="Language")
        task = gr.Radio(choices=list(TASKS.keys()), value="Transcribe", label="Task")
        timestamps = gr.Radio(choices=list(TIMESTAMP_MODES), value="Segment", label="Timestamps")
    ui = OpUI("Transcribe")
    ui.wire(
        "whisper_transcription",
        [source.file],
        {"language": language, "task": task, "timestamps": timestamps},
    )
