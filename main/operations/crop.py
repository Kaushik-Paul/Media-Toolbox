"""Crop video with manual geometry or aspect presets, plus a frame preview."""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    crop_preset_dimensions, finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

PRESETS = {"16:9", "9:16", "4:3", "1:1", "21:9"}


def resolve_crop(params: dict, src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """Return (w, h, x, y) from either a preset or manual values."""
    preset = str(params.get("preset", "custom"))
    if preset in PRESETS:
        return crop_preset_dimensions(src_w, src_h, preset)
    w = int(params.get("width", 0))
    h = int(params.get("height", 0))
    x = int(params.get("x", 0))
    y = int(params.get("y", 0))
    if w <= 0 or h <= 0:
        raise OperationError("Crop width and height must be positive.")
    if x < 0 or y < 0 or x + w > src_w or y + h > src_h:
        raise OperationError(
            "Crop rectangle is outside the frame.",
            f"source={src_w}x{src_h} crop={w}x{h} at ({x},{y})",
        )
    return w // 2 * 2, h // 2 * 2, x, y


def preview_crop(ffmpeg_path: str, src: Path, src_w: int, src_h: int, params: dict, dest: Path) -> Path:
    """Extract a single cropped frame for the UI preview."""
    w, h, x, y = resolve_crop(params, src_w, src_h)
    args = [
        ffmpeg_path, "-hide_banner", "-y", "-v", "error",
        "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y}", "-frames:v", "1", str(dest),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120, shell=False)
    if proc.returncode != 0 or not dest.exists():
        raise OperationError("Could not generate a crop preview.", proc.stderr.strip()[:300])
    return dest


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video or not info.primary_video:
        raise OperationError("This file does not contain a video stream.")
    src_w, src_h = info.primary_video.width or 0, info.primary_video.height or 0
    if not src_w or not src_h:
        raise OperationError("Could not determine source dimensions.")
    w, h, x, y = resolve_crop(params, src_w, src_h)

    require_encoder(ctx, "libx264")
    out_name = output_name(info.filename, f"crop-{w}x{h}", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-vf", f"crop={w}:{h}:{x}:{y}", "-c:v", "libx264", "-preset", "medium", "-crf", 20,
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
        parameters={"width": w, "height": h, "x": x, "y": y},
        command_previews=[b.preview()],
    )
