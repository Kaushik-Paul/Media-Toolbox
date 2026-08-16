"""Trim / Cut: fast (stream copy) and accurate (re-encode) modes."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from core.time_utils import parse_time
from operations.base import (
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    finalize_output,
    part_path_for,
    require_encoder,
    run_ffmpeg,
)


def _range(params: dict, duration: float | None) -> tuple[float, float]:
    start = parse_time(params.get("start")) or 0.0
    end = parse_time(params.get("end"))
    if end is None:
        if duration is None:
            raise OperationError("Provide an end time for this file.")
        end = duration
    if start < 0 or end <= start:
        raise OperationError("Invalid range: end must be greater than start.")
    if duration and start >= duration:
        raise OperationError("Start time is beyond the end of the media.")
    return start, end


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None:
        raise OperationError("Could not analyze the input file.")
    start, end = _range(params, info.duration_seconds)
    segment = end - start
    mode = str(params.get("mode", "fast"))  # fast | accurate

    out_ext = ext_of(info.filename) or ("mp4" if info.has_video else "m4a")
    out_name = output_name(info.filename, "trimmed", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    if mode == "fast":
        # Input seeking + stream copy: fast, no quality loss, not frame-exact.
        b.input(src, "-ss", f"{start:.3f}")
        b.add("-t", f"{segment:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero")
    else:
        # Output seeking + re-encode: frame-accurate but slower.
        b.input(src)
        b.add("-ss", f"{start:.3f}", "-t", f"{segment:.3f}")
        if info.has_video:
            require_encoder(ctx, "libx264")
            b.add("-c:v", "libx264", "-preset", "medium", "-crf", 18, "-pix_fmt", "yuv420p")
        if info.has_audio:
            b.add("-c:a", "aac", "-b:a", "192k")
        if out_ext == "mp4":
            b.add("-movflags", "+faststart")
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=segment)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"start": start, "end": end, "mode": mode},
        command_previews=[b.preview()],
        summary={
            "Mode": "Fast cut (stream copy)" if mode == "fast" else "Accurate cut (re-encoded)",
        },
    )
