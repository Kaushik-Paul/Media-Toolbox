"""Transcription tab: Whisper speech-to-text with SRT/VTT/TXT/JSON outputs."""
from __future__ import annotations

import gradio as gr

from core.media_types import AUDIO_EXTS, VIDEO_EXTS

from gpu.models.whisper import LANGUAGES, TASKS, TIMESTAMP_MODES
from gpu.ui.common import OpUI, upload_row


def transcription_tab():
    gr.Markdown(
        "Transcribe speech from video or audio with Whisper large-v3-turbo. "
        "Produces TXT, SRT, VTT, and JSON. To burn the generated subtitles into "
        "the video, download the SRT and use the CPU Toolbox's Subtitles tab."
    )
    upload = upload_row(
        "Drop video or audio here",
        file_types=sorted("." + e for e in VIDEO_EXTS | AUDIO_EXTS),
    )
    with gr.Row():
        language = gr.Dropdown(choices=list(LANGUAGES.keys()), value="Auto", label="Language")
        task = gr.Radio(choices=list(TASKS.keys()), value="Transcribe", label="Task")
        timestamps = gr.Radio(choices=list(TIMESTAMP_MODES), value="Segment", label="Timestamps")
    ui = OpUI("Transcribe")
    ui.wire(
        "whisper_transcription",
        [upload.file],
        {"language": language, "task": task, "timestamps": timestamps},
    )
