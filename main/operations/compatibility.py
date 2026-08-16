"""One-button browser/device compatibility preset and MP4 streaming optimization."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, require_encoder, run_ffmpeg,
)


def _make_compatible(ctx: JobContext, src: Path) -> OperationResult:
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")
    require_encoder(ctx, "libx264")
    require_encoder(ctx, "aac")

    out_name = output_name(info.filename, "compatible", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-map", "0:v:0")
    if info.has_audio:
        b.add("-map", "0:a:0")
    b.add("-c:v", "libx264", "-preset", "medium", "-crf", 21, "-pix_fmt", "yuv420p",
          "-profile:v", "high", "-level", "4.1")
    if info.has_audio:
        b.add("-c:a", "aac", "-b:a", "160k")
    b.add("-movflags", "+faststart", "-sn")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"preset": "browser-compatible"},
        command_previews=[b.preview()],
        summary={"Output": "MP4 / H.264 / AAC / yuv420p / fast-start"},
    )


def _optimize_streaming(ctx: JobContext, src: Path) -> OperationResult:
    info = ctx.media_info
    if info is None:
        raise OperationError("Could not analyze the input file.")
    if ext_of(info.filename) not in ("mp4", "mov", "m4v"):
        raise OperationError("Optimize for streaming requires an MP4/MOV input. Convert first.")

    out_name = output_name(info.filename, "faststart", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-map", "0", "-c", "copy", "-movflags", "+faststart")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={},
        command_previews=[b.preview()],
        summary={"Action": "moov atom moved for fast start (stream copy, no re-encode)"},
    )


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    mode = str(params.get("mode", ""))
    if mode == "make_compatible":
        return _make_compatible(ctx, inputs[0])
    if mode == "optimize_streaming":
        return _optimize_streaming(ctx, inputs[0])
    raise OperationError(f"Unknown compatibility mode: {mode}")
