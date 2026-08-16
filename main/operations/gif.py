"""Video to GIF using palette generation for good quality."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.time_utils import parse_time
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, run_ffmpeg,
)


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    start = parse_time(params.get("start")) or 0.0
    end = parse_time(params.get("end"))
    duration_limit = ctx.settings.gif_max_duration_seconds
    if end is None:
        end = min((info.duration_seconds or duration_limit), start + duration_limit)
    if end <= start:
        raise OperationError("Invalid range: end must be greater than start.")
    segment = end - start
    if segment > duration_limit:
        raise OperationError(
            f"GIF duration is limited to {duration_limit:g} seconds.",
            f"Requested {segment:.1f}s. GIF files grow extremely large.",
        )

    fps = int(params.get("fps", 12))
    width = int(params.get("width", 480))
    if not 1 <= fps <= 30 or not 100 <= width <= 1280:
        raise OperationError("FPS must be 1-30 and width 100-1280 for GIF output.")

    out_name = output_name(info.filename, "clip", "gif")
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    palette = ctx.work_dir / "palette.png"
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"

    b1 = ctx.builder()
    b1.input(src, "-ss", f"{start:.3f}")
    b1.add("-t", f"{segment:.3f}", "-vf", f"{vf},palettegen")
    b1.output(palette)
    run_ffmpeg(ctx, b1.build(), total_duration=segment, progress_offset=0, progress_span=40)

    b2 = ctx.builder()
    b2.input(src, "-ss", f"{start:.3f}")
    b2.input(palette)
    b2.add("-t", f"{segment:.3f}", "-lavfi", f"{vf} [x]; [x][1:v] paletteuse")
    b2.output(part)
    run_ffmpeg(ctx, b2.build(), total_duration=segment, progress_offset=40, progress_span=60)

    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"start": start, "end": end, "fps": fps, "width": width},
        command_previews=[b1.preview(), b2.preview()],
    )
