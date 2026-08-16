"""Rotate and flip video."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

TRANSFORMS = {
    "90cw": ("transpose=1", "rotated-90cw"),
    "90ccw": ("transpose=2", "rotated-90ccw"),
    "180": ("hflip,vflip", "rotated-180"),
    "hflip": ("hflip", "flipped-h"),
    "vflip": ("vflip", "flipped-v"),
}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    transform = str(params.get("transform", "90cw"))
    if transform not in TRANSFORMS:
        raise OperationError(f"Unknown transform: {transform}")
    vf, label = TRANSFORMS[transform]

    require_encoder(ctx, "libx264")
    out_name = output_name(info.filename, label, "mp4")
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
        parameters={"transform": transform},
        command_previews=[b.preview()],
    )
