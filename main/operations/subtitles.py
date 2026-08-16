"""Subtitle tools: extract tracks, add a track, burn into video."""
from __future__ import annotations

import shutil
from pathlib import Path

from core.filenames import output_name, sanitize_filename
from core.media_types import ext_of
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    escape_filter_path, finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def list_subtitle_streams(ctx: JobContext) -> list[dict]:
    """Used by the UI to populate the stream picker."""
    info = ctx.media_info
    if info is None:
        return []
    result = []
    for i, s in enumerate(info.subtitle_streams):
        result.append({
            "stream_index": i,
            "codec": s.codec_name,
            "language": s.language or "und",
            "text_based": s.codec_name in TEXT_SUBTITLE_CODECS,
        })
    return result


def _extract(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    info = ctx.media_info
    subs = info.subtitle_streams if info else []
    if not subs:
        raise OperationError("This file has no subtitle streams.")
    stream_index = int(params.get("stream_index", 0))
    if not 0 <= stream_index < len(subs):
        raise OperationError(f"Subtitle stream {stream_index} does not exist.")
    stream = subs[stream_index]
    if stream.codec_name not in TEXT_SUBTITLE_CODECS:
        raise OperationError(
            f"Subtitle stream uses '{stream.codec_name}', an image-based format "
            "that cannot be exported as text."
        )
    out_fmt = str(params.get("format", "srt"))
    if out_fmt not in ("srt", "ass", "vtt"):
        raise OperationError(f"Unsupported subtitle format: {out_fmt}")

    lang = stream.language or "und"
    out_name = output_name(info.filename, f"subs-{lang}", out_fmt)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-map", f"0:s:{stream_index}", "-c:s", out_fmt if out_fmt != "vtt" else "webvtt")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"stream_index": stream_index, "format": out_fmt, "language": lang},
        command_previews=[b.preview()],
    )


def _add(ctx: JobContext, src: Path, params: dict, sub_file: Path) -> OperationResult:
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")
    language = sanitize_filename(str(params.get("language", "und")), fallback="und")
    title = sanitize_filename(str(params.get("title", "")), fallback="")

    out_ext = ext_of(info.filename) or "mp4"
    if out_ext not in ("mp4", "mkv", "mov"):
        out_ext = "mkv"  # safest container for arbitrary subtitle tracks
    out_name = output_name(info.filename, "subtitled", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.input(sub_file)
    b.add("-map", "0", "-map", "1")
    b.add("-c", "copy")
    b.add("-c:s:0", "mov_text" if out_ext in ("mp4", "mov") else "srt")
    b.add(f"-metadata:s:s:0", f"language={language}")
    if title:
        b.add("-metadata:s:s:0", f"title={title}")
    if out_ext == "mp4":
        b.add("-movflags", "+faststart")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"language": language, "title": title, "container": out_ext},
        command_previews=[b.preview()],
        summary={"Action": "Subtitle track muxed in (streams copied, no re-encode)"},
    )


def _burn(ctx: JobContext, src: Path, params: dict, sub_file: Path | None) -> OperationResult:
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")
    if not ctx.capabilities.has_filter("subtitles"):
        raise OperationError("The subtitles filter (libass) is not available in this FFmpeg build.")

    if sub_file is not None:
        # Copy to a simple name so filter escaping stays trivial.
        local_sub = ctx.work_dir / ("burn_subs" + (sub_file.suffix.lower() or ".srt"))
        shutil.copyfile(sub_file, local_sub)
        vf = f"subtitles={escape_filter_path(local_sub)}"
    else:
        stream_index = int(params.get("stream_index", 0))
        subs = info.subtitle_streams
        if not subs:
            raise OperationError("This file has no embedded subtitle stream. Upload a subtitle file instead.")
        if not 0 <= stream_index < len(subs):
            raise OperationError(f"Subtitle stream {stream_index} does not exist.")
        vf = f"subtitles={escape_filter_path(src)}:si={stream_index}"

    require_encoder(ctx, "libx264")
    out_name = output_name(info.filename, "hardsub", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", 20, "-pix_fmt", "yuv420p")
    if info.has_audio:
        b.add("-c:a", "copy")
    else:
        b.add("-an")
    b.add("-movflags", "+faststart")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"source": "file" if sub_file else "embedded"},
        command_previews=[b.preview()],
        summary={"Action": "Subtitles burned into the video (re-encoded)"},
    )


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    mode = str(params.get("mode", ""))
    src = inputs[0]
    sub_file = inputs[1] if len(inputs) > 1 else None
    if mode == "subtitles_extract":
        return _extract(ctx, src, params)
    if mode == "subtitles_add":
        if sub_file is None:
            raise OperationError("Upload a subtitle file (.srt/.ass/.vtt) to add.")
        return _add(ctx, src, params, sub_file)
    if mode == "subtitles_burn":
        return _burn(ctx, src, params, sub_file)
    raise OperationError(f"Unknown subtitle mode: {mode}")
