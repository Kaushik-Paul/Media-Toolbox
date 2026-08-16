"""Assemble the Gradio Blocks application."""
from __future__ import annotations

import gradio as gr

from ui import tools
from ui.history import history_tab

HEADER = """
<div class='app-header'>
  <h1>Media Toolbox</h1>
  <p>FFmpeg-powered video & audio conversion. Outputs are kept for 24 hours, then deleted.</p>
</div>
"""


def build_blocks() -> gr.Blocks:
    # theme/css are applied in app.py via gr.mount_gradio_app (Gradio 6 API).
    with gr.Blocks(title="Media Toolbox") as blocks:
        gr.HTML(HEADER)
        with gr.Tabs():
            with gr.Tab("Video"):
                with gr.Tabs():
                    with gr.Tab("Compress"):
                        tools.compress_tab()
                    with gr.Tab("Target Size"):
                        tools.target_size_tab()
                    with gr.Tab("Resize"):
                        tools.resize_tab()
                    with gr.Tab("Convert"):
                        tools.convert_tab()
                    with gr.Tab("Trim / Cut"):
                        tools.trim_tab()
                    with gr.Tab("FPS"):
                        tools.fps_tab()
                    with gr.Tab("Rotate / Flip"):
                        tools.rotate_tab()
                    with gr.Tab("Crop"):
                        tools.crop_tab()
                    with gr.Tab("Speed"):
                        tools.speed_tab()
                    with gr.Tab("Merge A+V"):
                        tools.merge_tab()
                    with gr.Tab("Concatenate"):
                        tools.concatenate_tab()
                    with gr.Tab("GIF"):
                        tools.gif_tab()
                    with gr.Tab("Screenshot"):
                        tools.screenshot_tab()
                    with gr.Tab("Remove Audio"):
                        tools.remove_audio_tab()
            with gr.Tab("Audio"):
                with gr.Tabs():
                    with gr.Tab("Extract from Video"):
                        tools.extract_audio_tab()
                    with gr.Tab("Convert"):
                        tools.convert_audio_tab()
                    with gr.Tab("Compress"):
                        tools.compress_audio_tab()
                    with gr.Tab("Sample Rate"):
                        tools.sample_rate_tab()
                    with gr.Tab("Channels"):
                        tools.channels_tab()
                    with gr.Tab("Normalize"):
                        tools.normalize_tab()
                    with gr.Tab("Trim"):
                        tools.audio_trim_tab()
                    with gr.Tab("Speed"):
                        tools.audio_speed_tab()
            with gr.Tab("Subtitles"):
                with gr.Tabs():
                    with gr.Tab("Extract"):
                        tools.subtitles_extract_tab()
                    with gr.Tab("Add Track"):
                        tools.subtitles_add_tab()
                    with gr.Tab("Burn"):
                        tools.subtitles_burn_tab()
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
            with gr.Tab("History"):
                history_tab()
    return blocks
