"""FPS conversion using the fps filter."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

FPS_CHOICES = {"60": 60, "50": 50, "30": 30, "25": 25, "24": 24}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    choice = str(params.get("fps", "30"))
    if choice == "custom":
        fps_value = float(params.get("custom_fps", 0))
        if not 1 <= fps_value <= 240:
            raise OperationError("Custom FPS must be between 1 and 240.")
    elif choice in FPS_CHOICES:
        fps_value = float(FPS_CHOICES[choice])
    else:
        raise OperationError(f"Unsupported FPS value: {choice}")

    require_encoder(ctx, "libx264")
    out_name = output_name(info.filename, f"{fps_value:g}fps", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-vf", f"fps={fps_value:g}", "-c:v", "libx264", "-preset", "medium", "-crf", 20,
          "-pix_fmt", "yuv420p")
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
        parameters={"fps": fps_value},
        command_previews=[b.preview()],
    )
