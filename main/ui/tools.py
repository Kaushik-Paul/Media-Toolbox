"""Tool tab builders for the Gradio Blocks UI."""
from __future__ import annotations

import tempfile
from pathlib import Path

import gradio as gr

from backend.security import AdvancedArgsError, validate_advanced_args, validate_output_extension
from backend.services import get_services
from operations.advanced import ALLOWED_EXTENSIONS
from operations.crop import preview_crop
from ui.components import OpUI, upload_row

VIDEO_UPLOAD = ["video"]
AUDIO_UPLOAD = ["audio"]
AV_UPLOAD = ["video", "audio"]
SUB_UPLOAD = [".srt", ".ass", ".ssa", ".vtt"]

BITRATES = ["original", "320k", "256k", "192k", "128k", "96k", "64k"]
AUDIO_BITRATES = ["320k", "256k", "192k", "128k", "96k", "64k"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav"]
LOSSLESS_FORMATS = ["flac", "wav", "mp3", "m4a", "opus"]
RESOLUTIONS = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p", "custom"]
RESIZE_PRESETS = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "custom"]


def _codec_choices() -> list[str]:
    caps = get_services().capabilities
    choices = []
    if caps.has_encoder("libx264"):
        choices.append("h264")
    if caps.has_encoder("libx265"):
        choices.append("h265")
    if caps.has_encoder("libsvtav1"):
        choices.append("av1")
    return choices or ["h264"]


def _audio_format_choices() -> list[str]:
    caps = get_services().capabilities
    needed = {"mp3": "libmp3lame", "m4a": "aac", "opus": "libopus", "flac": "flac", "wav": "pcm_s16le"}
    return [f for f in AUDIO_FORMATS if caps.has_encoder(needed[f])] or ["wav"]


# ---------------------------------------------------------------------------
# Video tabs
# ---------------------------------------------------------------------------


def compress_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        codec = gr.Dropdown(_codec_choices(), value="h264", label="Codec")
        quality = gr.Dropdown(["quick", "balanced", "high", "custom"], value="balanced", label="Quality")
    with gr.Row(visible=False) as custom_q:
        preset = gr.Dropdown(
            ["ultrafast", "veryfast", "fast", "medium", "slow", "veryslow"],
            value="medium", label="Preset",
        )
        crf = gr.Slider(0, 51, value=23, step=1, label="CRF (lower = better)")
    quality.change(fn=lambda q: gr.update(visible=q == "custom"), inputs=[quality], outputs=[custom_q])
    with gr.Row():
        resolution = gr.Dropdown(RESOLUTIONS, value="original", label="Resolution")
        audio_bitrate = gr.Dropdown(BITRATES, value="128k", label="Audio bitrate")
    with gr.Row(visible=False) as custom_res:
        width = gr.Number(label="Width", value=0, precision=0)
        height = gr.Number(label="Height", value=0, precision=0)
    resolution.change(fn=lambda r: gr.update(visible=r == "custom"), inputs=[resolution], outputs=[custom_res])
    with gr.Row():
        allow_upscale = gr.Checkbox(False, label="Allow upscaling")
        remove_audio = gr.Checkbox(False, label="Remove audio")

    ui = OpUI("Compress")
    ui.wire("compress_video", [file], {
        "codec": codec, "quality": quality, "preset": preset, "crf": crf,
        "resolution": resolution, "width": width, "height": height,
        "allow_upscale": allow_upscale, "audio_bitrate": audio_bitrate,
        "remove_audio": remove_audio,
    })


def target_size_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        target = gr.Number(label="Target size (MB)", value=200, minimum=1)
        audio_bitrate = gr.Dropdown(AUDIO_BITRATES, value="128k", label="Audio bitrate")
        codec = gr.Dropdown(["h264", "h265"], value="h264", label="Codec")
    gr.Markdown("Uses two-pass encoding. The actual size lands within a few percent of the target.")
    ui = OpUI("Encode to size")
    ui.wire("target_size", [file], {"target_mb": target, "audio_bitrate": audio_bitrate, "codec": codec})


def resize_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        preset = gr.Dropdown(RESIZE_PRESETS, value="720p", label="Resolution")
        mode = gr.Radio(["fit", "exact"], value="fit", label="Sizing mode")
    with gr.Row(visible=False) as custom_row:
        width = gr.Number(label="Width", value=1280, precision=0)
        height = gr.Number(label="Height", value=720, precision=0)
    preset.change(fn=lambda p: gr.update(visible=p == "custom"), inputs=[preset], outputs=[custom_row])
    with gr.Row(visible=False) as exact_row:
        exact_mode = gr.Radio(["letterbox", "crop", "stretch"], value="letterbox", label="Exact fit behavior")
    mode.change(fn=lambda m: gr.update(visible=m == "exact"), inputs=[mode], outputs=[exact_row])
    prevent = gr.Checkbox(True, label="Prevent upscaling")
    ui = OpUI("Resize")
    ui.wire("resize_video", [file], {
        "preset": preset, "mode": mode, "exact_mode": exact_mode,
        "width": width, "height": height, "prevent_upscale": prevent,
    })


def convert_tab():
    file, info, _ = upload_row("Drop a video or audio file here", AV_UPLOAD)
    with gr.Row():
        container = gr.Dropdown(["mp4", "mkv", "mov", "webm"], value="mp4", label="Target container")
        mode = gr.Radio(["auto", "remux", "reencode"], value="auto", label="Mode")
    gr.Markdown("`auto` remuxes without quality loss when the streams fit the container, otherwise re-encodes.")
    ui = OpUI("Convert")
    ui.wire("convert_format", [file], {"container": container, "mode": mode})


def trim_tab():
    file, info, _ = upload_row("Drop media here", AV_UPLOAD)
    with gr.Row():
        start = gr.Textbox(label="Start", value="00:00:00", placeholder="HH:MM:SS or seconds")
        end = gr.Textbox(label="End", placeholder="HH:MM:SS or seconds (empty = end of file)")
    mode = gr.Radio(["fast", "accurate"], value="fast", label="Cut mode")
    gr.Markdown("`fast`: stream copy, no quality loss, may not be frame-exact. `accurate`: re-encodes, frame-exact, slower.")
    ui = OpUI("Trim")
    ui.wire("trim", [file], {"start": start, "end": end, "mode": mode})


def fps_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        fps = gr.Dropdown(["60", "50", "30", "25", "24", "custom"], value="30", label="Target FPS")
        custom = gr.Number(label="Custom FPS", value=30, visible=False)
    fps.change(fn=lambda f: gr.update(visible=f == "custom"), inputs=[fps], outputs=[custom])
    ui = OpUI("Convert FPS")
    ui.wire("fps_convert", [file], {"fps": fps, "custom_fps": custom})


def rotate_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    transform = gr.Dropdown(
        [("90° clockwise", "90cw"), ("90° counterclockwise", "90ccw"), ("180°", "180"),
         ("Flip horizontal", "hflip"), ("Flip vertical", "vflip")],
        value="90cw", label="Transform",
    )
    ui = OpUI("Apply")
    ui.wire("rotate_flip", [file], {"transform": transform})


def crop_tab():
    file, info, info_state = upload_row("Drop a video here", VIDEO_UPLOAD)
    preset = gr.Dropdown(["custom", "16:9", "9:16", "4:3", "1:1", "21:9"], value="custom", label="Aspect preset")
    with gr.Row():
        x = gr.Number(label="X", value=0, precision=0)
        y = gr.Number(label="Y", value=0, precision=0)
        width = gr.Number(label="Width", value=0, precision=0)
        height = gr.Number(label="Height", value=0, precision=0)
    preview_btn = gr.Button("Preview crop frame", size="sm")
    preview_img = gr.Image(label="Crop preview", visible=False)

    def _preview(file_value, state, preset_v, x_v, y_v, w_v, h_v):
        if not file_value or not state.get("width"):
            return gr.update(visible=False)
        try:
            dest = Path(tempfile.mkdtemp(prefix="crop-preview-")) / "preview.jpg"
            preview_crop(
                get_services().settings.ffmpeg_path, Path(file_value),
                state["width"], state["height"],
                {"preset": preset_v, "x": x_v, "y": y_v, "width": w_v, "height": h_v},
                dest,
            )
            return gr.update(visible=True, value=str(dest))
        except Exception:
            return gr.update(visible=False)

    preview_btn.click(fn=_preview,
                      inputs=[file, info_state, preset, x, y, width, height],
                      outputs=[preview_img])
    ui = OpUI("Crop")
    ui.wire("crop_video", [file], {"preset": preset, "x": x, "y": y, "width": width, "height": height})


def speed_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        speed = gr.Dropdown(["0.25", "0.5", "0.75", "1.25", "1.5", "2", "custom"], value="1.5", label="Speed")
        custom = gr.Number(label="Custom factor", value=1.5, visible=False)
    speed.change(fn=lambda s: gr.update(visible=s == "custom"), inputs=[speed], outputs=[custom])
    gr.Markdown("Adjusts both video timestamps and audio tempo.")
    ui = OpUI("Change speed")
    ui.wire("change_speed", [file], {"speed": speed, "custom_speed": custom})


def merge_tab():
    video = gr.File(label="Video file", file_types=VIDEO_UPLOAD, type="filepath")
    audio = gr.File(label="Audio file", file_types=AUDIO_UPLOAD, type="filepath")
    with gr.Row():
        audio_mode = gr.Radio(
            [("Replace existing audio", "replace"), ("Keep existing + add new track", "add")],
            value="replace", label="Audio mode",
        )
        length = gr.Radio(
            [("Shortest stream wins", "shortest"), ("Video length wins", "video")],
            value="shortest", label="Length",
        )
    ui = OpUI("Merge")
    ui.wire("merge_av", [video, audio], {"audio_mode": audio_mode, "length": length})


def concatenate_tab():
    files, info, _ = upload_row("Drop clips here (in order)", VIDEO_UPLOAD, file_count="multiple")
    mode = gr.Radio(
        [("Fast join (same codec/resolution required)", "fast"),
         ("Compatible join (normalize + re-encode)", "compatible")],
        value="fast", label="Join mode",
    )
    ui = OpUI("Concatenate")
    ui.wire("concatenate", [files], {"mode": mode})


def gif_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        start = gr.Textbox(label="Start", value="00:00:00")
        end = gr.Textbox(label="End", placeholder="empty = up to 30s limit")
    with gr.Row():
        width = gr.Slider(100, 1280, value=480, step=10, label="Width")
        fps = gr.Slider(1, 30, value=12, step=1, label="FPS")
    ui = OpUI("Create GIF")
    ui.wire("video_to_gif", [file], {"start": start, "end": end, "width": width, "fps": fps})


def screenshot_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    with gr.Row():
        timestamp = gr.Textbox(label="Timestamp", placeholder="HH:MM:SS or seconds (empty = auto)")
        fmt = gr.Dropdown(["jpg", "png", "webp"], value="jpg", label="Format")
    ui = OpUI("Extract frame")
    ui.wire("screenshot", [file], {"timestamp": timestamp, "format": fmt})


def remove_audio_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    gr.Markdown("The video stream is copied without re-encoding; only the audio is dropped.")
    ui = OpUI("Remove audio")
    ui.wire("remove_audio", [file], {"mode": gr.State("remove_audio")})


# ---------------------------------------------------------------------------
# Audio tabs
# ---------------------------------------------------------------------------


def _audio_tab(title: str, mode: str, options, run_label: str):
    file, info, _ = upload_row(f"Drop an audio or video file here", AV_UPLOAD)
    params = {"mode": gr.State(mode)}
    params.update(options)
    ui = OpUI(run_label)
    ui.wire(mode, [file], params)


def extract_audio_tab():
    with gr.Row():
        fmt = gr.Dropdown(_audio_format_choices(), value="mp3", label="Format")
        extract_mode = gr.Radio(
            [("Convert", "convert"), ("Copy original audio", "copy")], value="convert", label="Mode"
        )
        bitrate = gr.Dropdown(AUDIO_BITRATES, value="192k", label="Bitrate (convert mode)")
    _audio_tab("Extract audio", "extract_audio",
               {"format": fmt, "extract_mode": extract_mode, "bitrate": bitrate}, "Extract audio")


def convert_audio_tab():
    with gr.Row():
        fmt = gr.Dropdown(_audio_format_choices(), value="mp3", label="Target format")
        bitrate = gr.Dropdown(AUDIO_BITRATES, value="192k", label="Bitrate")
    _audio_tab("Convert audio", "convert_audio", {"format": fmt, "bitrate": bitrate}, "Convert")


def compress_audio_tab():
    with gr.Row():
        bitrate = gr.Dropdown(AUDIO_BITRATES, value="96k", label="Target bitrate")
        fmt = gr.Dropdown(_audio_format_choices(), value="mp3", label="Format")
    _audio_tab("Compress audio", "compress_audio", {"bitrate": bitrate, "format": fmt}, "Compress")


def sample_rate_tab():
    with gr.Row():
        rate = gr.Dropdown(["48000", "44100", "32000", "24000", "16000"], value="44100", label="Sample rate (Hz)")
        fmt = gr.Dropdown(LOSSLESS_FORMATS, value="flac", label="Format")
    _audio_tab("Sample rate", "audio_sample_rate", {"sample_rate": rate, "format": fmt}, "Convert sample rate")


def channels_tab():
    with gr.Row():
        direction = gr.Radio([("Stereo to Mono", "mono"), ("Mono to Stereo", "stereo")],
                             value="mono", label="Channels")
        fmt = gr.Dropdown(LOSSLESS_FORMATS, value="flac", label="Format")
    _audio_tab("Channels", "audio_channels", {"channels": direction, "format": fmt}, "Convert channels")


def normalize_tab():
    with gr.Row():
        mode = gr.Radio([("Simple normalize", "simple"), ("EBU R128 loudness (two-pass)", "ebu")],
                        value="simple", label="Normalization")
        fmt = gr.Dropdown(LOSSLESS_FORMATS, value="flac", label="Format")
    _audio_tab("Normalize", "audio_normalize", {"normalize_mode": mode, "format": fmt}, "Normalize")


def audio_trim_tab():
    with gr.Row():
        start = gr.Textbox(label="Start", value="00:00:00")
        end = gr.Textbox(label="End", placeholder="empty = end of file")
        fmt = gr.Dropdown(LOSSLESS_FORMATS, value="flac", label="Format")
    _audio_tab("Trim audio", "audio_trim", {"start": start, "end": end, "format": fmt}, "Extract segment")


def audio_speed_tab():
    with gr.Row():
        speed = gr.Dropdown(["0.5", "0.75", "1.25", "1.5", "2"], value="1.5", label="Speed")
        fmt = gr.Dropdown(LOSSLESS_FORMATS, value="flac", label="Format")
    _audio_tab("Audio speed", "audio_speed", {"speed": speed, "format": fmt}, "Change speed")


# ---------------------------------------------------------------------------
# Subtitle tabs
# ---------------------------------------------------------------------------


def _stream_picker(info_state: gr.State):
    picker = gr.Dropdown([], value=None, label="Subtitle stream", interactive=True)

    def _update(state):
        subs = state.get("subtitles", []) if state else []
        choices = [(f"#{s['index']} — {s['language']} ({s['codec']})", s["index"]) for s in subs]
        return gr.update(choices=choices, value=choices[0][1] if choices else None)

    info_state.change(fn=_update, inputs=[info_state], outputs=[picker])
    return picker


def subtitles_extract_tab():
    file, info, info_state = upload_row("Drop a video with subtitle tracks here", VIDEO_UPLOAD)
    picker = _stream_picker(info_state)
    fmt = gr.Dropdown(["srt", "vtt", "ass"], value="srt", label="Export format")
    ui = OpUI("Extract subtitles")
    ui.wire("subtitles_extract", [file],
            {"mode": gr.State("subtitles_extract"), "stream_index": picker, "format": fmt})


def subtitles_add_tab():
    video = gr.File(label="Video file", file_types=VIDEO_UPLOAD, type="filepath")
    sub = gr.File(label="Subtitle file", file_types=SUB_UPLOAD, type="filepath")
    with gr.Row():
        language = gr.Textbox(label="Language code", value="eng", max_lines=1)
        title = gr.Textbox(label="Track title (optional)", max_lines=1)
    ui = OpUI("Add subtitle track")
    ui.wire("subtitles_add", [video, sub],
            {"mode": gr.State("subtitles_add"), "language": language, "title": title})


def subtitles_burn_tab():
    file, info, info_state = upload_row("Drop a video here", VIDEO_UPLOAD)
    sub = gr.File(label="Subtitle file (optional if embedded)", file_types=SUB_UPLOAD, type="filepath")
    picker = _stream_picker(info_state)
    gr.Markdown("Burning renders subtitles into the picture and requires re-encoding.")
    ui = OpUI("Burn subtitles")
    ui.wire("subtitles_burn", [file, sub],
            {"mode": gr.State("subtitles_burn"), "stream_index": picker})


# ---------------------------------------------------------------------------
# Utilities + Advanced
# ---------------------------------------------------------------------------


def make_compatible_tab():
    file, info, _ = upload_row("Drop a video here", VIDEO_UPLOAD)
    gr.Markdown("One-button output: MP4 / H.264 / AAC / yuv420p / fast-start. Plays in essentially every browser and phone.")
    ui = OpUI("Make compatible")
    ui.wire("make_compatible", [file], {"mode": gr.State("make_compatible")})


def optimize_streaming_tab():
    file, info, _ = upload_row("Drop an MP4/MOV here", VIDEO_UPLOAD)
    gr.Markdown("Moves MP4 metadata for instant playback start. Stream copy, no re-encode.")
    ui = OpUI("Optimize for streaming")
    ui.wire("optimize_streaming", [file], {"mode": gr.State("optimize_streaming")})


def remove_metadata_tab():
    file, info, _ = upload_row("Drop media here", AV_UPLOAD)
    with gr.Row():
        keep_chapters = gr.Checkbox(False, label="Keep chapters")
        keep_rotation = gr.Checkbox(True, label="Keep rotation/language tags")
    ui = OpUI("Remove metadata")
    ui.wire("remove_metadata", [file], {"keep_chapters": keep_chapters, "keep_rotation": keep_rotation})


def media_info_tab():
    file = gr.File(label="Drop any media file here", type="filepath")
    out = gr.JSON(label="FFprobe output")

    def _probe(file_value):
        if not file_value:
            return None
        try:
            return get_services().probe.probe(Path(file_value)).model_dump()
        except Exception as exc:
            return {"error": str(exc)}

    file.change(fn=_probe, inputs=[file], outputs=[out])


def advanced_tab():
    file, info, _ = upload_row("Drop media here", AV_UPLOAD)
    with gr.Row():
        ext = gr.Dropdown(sorted(ALLOWED_EXTENSIONS), value="mp4", label="Output extension")
    args = gr.Textbox(
        label="Custom FFmpeg arguments",
        placeholder="-c:v libx265 -crf 27 -preset slow -c:a aac -b:a 128k",
        lines=3,
    )
    preview = gr.Markdown()
    gr.Markdown(
        "Input and output files are controlled by the app. Network URLs, extra inputs, "
        "pipes, and path escapes are rejected."
    )

    def _preview_cmd(args_text, ext_value):
        try:
            user_args = validate_advanced_args(args_text or "")
            ext_value = validate_output_extension(ext_value or "", ALLOWED_EXTENSIONS)
        except AdvancedArgsError as exc:
            return f"**Rejected:** {exc}"
        import shlex
        cmd = ["ffmpeg", "-i", "input", *user_args, f"output.{ext_value}"]
        return f"```bash\n{shlex.join(cmd)}\n```"

    args.change(fn=_preview_cmd, inputs=[args, ext], outputs=[preview])
    ext.change(fn=_preview_cmd, inputs=[args, ext], outputs=[preview])

    ui = OpUI("Run custom command")
    ui.wire("advanced_ffmpeg", [file], {"args": args, "extension": ext})
