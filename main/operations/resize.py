"""Resize Video: presets, fit-inside, exact dimensions (stretch/crop/letterbox)."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    exact_size_filter,
    finalize_output,
    fit_inside_filter,
    part_path_for,
    require_encoder,
    run_ffmpeg,
    scale_to_height_filter,
)

PRESETS = {"2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    preset = str(params.get("preset", "720p"))
    prevent_upscale = bool(params.get("prevent_upscale", True))
    mode = str(params.get("mode", "fit"))  # fit | exact
    exact_mode = str(params.get("exact_mode", "letterbox"))  # stretch | crop | letterbox

    if preset in PRESETS:
        height = PRESETS[preset]
        if mode == "exact":
            v = info.primary_video
            src_w, src_h = (v.width or 0), (v.height or 0)
            if not src_w or not src_h:
                raise OperationError("Could not determine source dimensions.")
            width = int(src_w * height / src_h) // 2 * 2
            vf = exact_size_filter(width, height, exact_mode)
        else:
            vf = scale_to_height_filter(height, prevent_upscale=prevent_upscale)
        label = preset
    else:
        width, height = int(params.get("width", 0)), int(params.get("height", 0))
        if width <= 0 or height <= 0:
            raise OperationError("Custom resize needs both width and height.")
        if mode == "exact":
            vf = exact_size_filter(width, height, exact_mode)
        else:
            vf = fit_inside_filter(width, height, prevent_upscale=prevent_upscale)
        label = f"{width}x{height}"

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
        parameters={"preset": preset, "mode": mode, "exact_mode": exact_mode,
                    "prevent_upscale": prevent_upscale, "filter": vf},
        command_previews=[b.preview()],
    )
