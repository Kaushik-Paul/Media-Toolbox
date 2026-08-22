"""Assemble the Gradio Blocks application."""
from __future__ import annotations

import gradio as gr

from ui import tools
from ui.history import history_tab
from ui.shell import global_controls

HEADER = """
<div class='app-header'>
  <h1>Media Toolbox</h1>
  <p>FFmpeg-powered video & audio conversion. Outputs are kept for 24 hours, then deleted.</p>
</div>
"""


def build_basic_tool_tabs(*, include_history: bool = True) -> None:
    """Render every shared FFmpeg tool into the current outer Tabs."""
    with gr.Tab("Video"):
        video_source = tools.video_source_upload()
        with gr.Tabs():
            with gr.Tab("Compress"):
                tools.compress_tab(video_source)
            with gr.Tab("Target Size"):
                tools.target_size_tab(video_source)
            with gr.Tab("Resize"):
                tools.resize_tab(video_source)
            with gr.Tab("Convert"):
                tools.convert_tab(video_source)
            with gr.Tab("Trim / Cut"):
                tools.trim_tab(video_source)
            with gr.Tab("FPS"):
                tools.fps_tab(video_source)
            with gr.Tab("Rotate / Flip"):
                tools.rotate_tab(video_source)
            with gr.Tab("Crop"):
                tools.crop_tab(video_source)
            with gr.Tab("Speed"):
                tools.speed_tab(video_source)
            with gr.Tab("Merge A+V"):
                tools.merge_tab(video_source)
            with gr.Tab("Concatenate"):
                tools.concatenate_tab(video_source)
            with gr.Tab("GIF"):
                tools.gif_tab(video_source)
            with gr.Tab("Screenshot"):
                tools.screenshot_tab(video_source)
            with gr.Tab("Remove Audio"):
                tools.remove_audio_tab(video_source)
    with gr.Tab("Audio"):
        audio_source = tools.audio_source_upload()
        with gr.Tabs():
            with gr.Tab("Extract from Video"):
                tools.extract_audio_tab(audio_source)
            with gr.Tab("Convert"):
                tools.convert_audio_tab(audio_source)
            with gr.Tab("Compress"):
                tools.compress_audio_tab(audio_source)
            with gr.Tab("Sample Rate"):
                tools.sample_rate_tab(audio_source)
            with gr.Tab("Channels"):
                tools.channels_tab(audio_source)
            with gr.Tab("Normalize"):
                tools.normalize_tab(audio_source)
            with gr.Tab("Trim"):
                tools.audio_trim_tab(audio_source)
            with gr.Tab("Speed"):
                tools.audio_speed_tab(audio_source)
    with gr.Tab("Subtitles"):
        subtitle_video, subtitle_file = tools.subtitle_source_uploads()
        with gr.Tabs():
            with gr.Tab("Extract"):
                tools.subtitles_extract_tab(subtitle_video)
            with gr.Tab("Add Track"):
                tools.subtitles_add_tab(subtitle_video, subtitle_file)
            with gr.Tab("Burn"):
                tools.subtitles_burn_tab(subtitle_video, subtitle_file)
    with gr.Tab("Utilities"):
        with gr.Tabs():
            with gr.Tab("Make Compatible"):
                tools.make_compatible_tab()
            with gr.Tab("Optimize Streaming"):
                tools.optimize_streaming_tab()
            with gr.Tab("Remove Metadata"):
                tools.remove_metadata_tab()
            with gr.Tab("Media Info"):
                tools.media_info_tab()
    with gr.Tab("Advanced"):
        tools.advanced_tab()
    if include_history:
        with gr.Tab("History"):
            history_tab()


def build_blocks() -> gr.Blocks:
    # theme/css are applied in app.py via gr.mount_gradio_app (Gradio 6 API).
    with gr.Blocks(title="Media Toolbox") as blocks:
        gr.HTML(HEADER)
        global_controls()
        with gr.Tabs():
            build_basic_tool_tabs()
    return blocks
