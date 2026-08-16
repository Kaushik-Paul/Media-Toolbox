"""Change playback speed for both video timestamps and audio tempo."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    atempo_chain, finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

SPEED_CHOICES = {"0.25", "0.5", "0.75", "1.25", "1.5", "2"}


def resolve_factor(params: dict) -> float:
    choice = str(params.get("speed", "1.5"))
    if choice == "custom":
        factor = float(params.get("custom_speed", 0))
    elif choice in SPEED_CHOICES:
        factor = float(choice)
    else:
        raise OperationError(f"Unsupported speed: {choice}")
    if not 0.1 <= factor <= 10:
        raise OperationError("Speed factor must be between 0.1 and 10.")
    return factor


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")
    factor = resolve_factor(params)

    require_encoder(ctx, "libx264")
    out_name = output_name(info.filename, f"{factor:g}x", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-vf", f"setpts=PTS/{factor:g}", "-c:v", "libx264", "-preset", "medium", "-crf", 20,
          "-pix_fmt", "yuv420p")
    if info.has_audio:
        b.add("-af", atempo_chain(factor), "-c:a", "aac", "-b:a", "160k")
    else:
        b.add("-an")
    b.add("-movflags", "+faststart")
    b.output(part)

    duration = (info.duration_seconds or 0) / factor
    run_ffmpeg(ctx, b.build(), total_duration=duration or None)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"speed": factor},
        command_previews=[b.preview()],
    )
