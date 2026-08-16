"""Advanced FFmpeg mode: validated custom arguments, app-controlled I/O."""
from __future__ import annotations

from pathlib import Path

from backend.security import AdvancedArgsError, validate_advanced_args, validate_output_extension
from core.filenames import output_name
from core.media_types import AUDIO_EXTS, VIDEO_EXTS
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, run_ffmpeg,
)

ALLOWED_EXTENSIONS = VIDEO_EXTS | AUDIO_EXTS


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None:
        raise OperationError("Could not analyze the input file.")

    try:
        user_args = validate_advanced_args(str(params.get("args", "")))
        out_ext = validate_output_extension(str(params.get("extension", "mp4")), ALLOWED_EXTENSIONS)
    except AdvancedArgsError as exc:
        raise OperationError(str(exc)) from exc

    out_name = output_name(info.filename, "custom", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add(*user_args)
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"args": " ".join(user_args), "extension": out_ext},
        command_previews=[b.preview()],
    )
