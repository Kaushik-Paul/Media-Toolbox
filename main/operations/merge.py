"""Merge a video file with a separate audio file."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, run_ffmpeg,
)

MP4_AUDIO_COPY = {"aac", "mp3", "alac", "flac", "opus"}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    if len(inputs) < 2:
        raise OperationError("Provide both a video file and an audio file.")
    video_src, audio_src = inputs[0], inputs[1]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("The first file must contain a video stream.")
    audio_info = ctx.probe.probe(audio_src)
    if not audio_info.has_audio:
        raise OperationError("The second file does not contain an audio stream.")

    audio_mode = str(params.get("audio_mode", "replace"))  # replace | add
    length_mode = str(params.get("length", "shortest"))  # shortest | video

    out_ext = ext_of(info.filename) or "mp4"
    if out_ext not in ("mp4", "mkv", "mov"):
        out_ext = "mkv"
    out_name = output_name(info.filename, "merged", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    src_audio_codec = (audio_info.primary_audio.codec_name if audio_info.primary_audio else "")
    can_copy_audio = out_ext == "mkv" or src_audio_codec in MP4_AUDIO_COPY

    b = ctx.builder()
    b.input(video_src)
    b.input(audio_src)
    if audio_mode == "replace":
        b.add("-map", "0:v", "-map", "1:a")
    else:
        b.add("-map", "0", "-map", "1:a")
    b.add("-c:v", "copy")
    if can_copy_audio:
        b.add("-c:a", "copy")
    else:
        b.add("-c:a", "aac", "-b:a", "192k")
    if length_mode == "shortest":
        b.add("-shortest")
    if out_ext == "mp4":
        b.add("-movflags", "+faststart")
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"audio_mode": audio_mode, "length": length_mode,
                    "audio_copied": can_copy_audio},
        command_previews=[b.preview()],
        summary={"Audio": "Stream copied" if can_copy_audio else "Re-encoded to AAC"},
    )
