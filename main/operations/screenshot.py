"""Extract a screenshot / thumbnail at a chosen timestamp."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.time_utils import parse_time
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, run_ffmpeg,
)

FORMATS = {"jpg", "png", "webp"}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    timestamp = parse_time(params.get("timestamp"))
    if timestamp is None:
        timestamp = min(1.0, (info.duration_seconds or 1.0) / 2)
    if info.duration_seconds and timestamp >= info.duration_seconds:
        raise OperationError("Timestamp is beyond the end of the video.")

    fmt = str(params.get("format", "jpg")).lower()
    if fmt not in FORMATS:
        raise OperationError(f"Unsupported image format: {fmt}")

    out_name = output_name(info.filename, f"thumb-{timestamp:.1f}s", fmt)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src, "-ss", f"{timestamp:.3f}")
    b.add("-frames:v", "1", "-q:v", 2)
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=None)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"timestamp": timestamp, "format": fmt},
        command_previews=[b.preview()],
    )
