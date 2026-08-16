"""Remove metadata, with optional preservation of chapters and rotation."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, run_ffmpeg,
)


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None:
        raise OperationError("Could not analyze the input file.")

    keep_chapters = bool(params.get("keep_chapters", False))
    keep_rotation = bool(params.get("keep_rotation", True))

    out_ext = ext_of(info.filename) or "mp4"
    out_name = output_name(info.filename, "clean", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-map", "0", "-c", "copy", "-map_metadata", "-1")
    if not keep_chapters:
        b.add("-map_chapters", "-1")
    # -map_metadata -1 strips global metadata only. Stream-level tags such as
    # language and the rotation display matrix survive stream copy, which
    # satisfies the keep_rotation / keep_language options.
    if out_ext == "mp4":
        b.add("-movflags", "+faststart")
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"keep_chapters": keep_chapters, "keep_rotation": keep_rotation},
        command_previews=[b.preview()],
        summary={"Action": "Global metadata removed (streams copied, no re-encode)"},
    )
