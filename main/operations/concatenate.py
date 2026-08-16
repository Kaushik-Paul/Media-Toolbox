"""Concatenate multiple clips: fast join (stream copy) or compatible join (normalize)."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from operations.base import (
    JobContext, OperationError, OperationResult, ProducedOutput,
    finalize_output, part_path_for, require_encoder, run_ffmpeg,
)

MAX_CLIPS = 20


def _streams_compatible(ctx: JobContext, inputs: list[Path]) -> bool:
    try:
        first = ctx.probe.probe(inputs[0])
        fv, fa = first.primary_video, first.primary_audio
        for path in inputs[1:]:
            info = ctx.probe.probe(path)
            v, a = info.primary_video, info.primary_audio
            if (v is None) != (fv is None):
                return False
            if v and fv and (v.codec_name, v.width, v.height) != (fv.codec_name, fv.width, fv.height):
                return False
            if (a is None) != (fa is None):
                return False
            if a and fa and (a.codec_name, a.sample_rate, a.channels) != (fa.codec_name, fa.sample_rate, fa.channels):
                return False
        return True
    except Exception:
        return False


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    if len(inputs) < 2:
        raise OperationError("Upload at least two clips to concatenate.")
    if len(inputs) > MAX_CLIPS:
        raise OperationError(f"Too many clips (max {MAX_CLIPS}).")

    info = ctx.media_info
    mode = str(params.get("mode", "fast"))  # fast | compatible
    out_ext = ext_of(info.filename) or "mp4"
    if out_ext not in ("mp4", "mkv", "mov"):
        out_ext = "mp4" if mode == "compatible" else "mkv"

    out_name = output_name(info.filename, "joined", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    total_duration = sum(
        (ctx.probe.probe(p).duration_seconds or 0) for p in inputs
    ) or None

    if mode == "fast":
        if not _streams_compatible(ctx, inputs):
            raise OperationError(
                "Clips have different codecs, resolutions, or audio parameters.",
                "Use 'Compatible join' to normalize them first.",
            )
        list_file = ctx.work_dir / "concat.txt"
        list_file.write_text(
            "".join(f"file '{p}'\n" for p in inputs), encoding="utf-8"
        )
        b = ctx.builder()
        b.add("-f", "concat", "-safe", 0)
        b.input(list_file)
        b.add("-c", "copy")
        if out_ext == "mp4":
            b.add("-movflags", "+faststart")
        b.output(part)
        run_ffmpeg(ctx, b.build(), total_duration=total_duration)
        action = "Fast join (stream copy)"
    else:
        require_encoder(ctx, "libx264")
        v = info.primary_video
        if v is None or not v.width or not v.height:
            raise OperationError("Compatible join needs a video stream in every clip.")
        width, height = v.width // 2 * 2, v.height // 2 * 2
        fps = int(round(v.fps or 30))
        has_audio = info.has_audio

        b = ctx.builder()
        for p in inputs:
            b.input(p)
        chains = []
        labels = []
        for i in range(len(inputs)):
            chains.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
                f":force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
                f",fps={fps},setsar=1[v{i}]"
            )
            labels.append(f"[v{i}]")
            if has_audio:
                chains.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")
                labels.append(f"[a{i}]")
        chains.append(
            "".join(labels) + f"concat=n={len(inputs)}:v=1:a={1 if has_audio else 0}[vout]"
            + ("[aout]" if has_audio else "")
        )
        b.add("-filter_complex", ";".join(chains))
        b.add("-map", "[vout]")
        if has_audio:
            b.add("-map", "[aout]")
        b.add("-c:v", "libx264", "-preset", "medium", "-crf", 21, "-pix_fmt", "yuv420p")
        if has_audio:
            b.add("-c:a", "aac", "-b:a", "160k")
        if out_ext == "mp4":
            b.add("-movflags", "+faststart")
        b.output(part)
        run_ffmpeg(ctx, b.build(), total_duration=total_duration)
        action = "Compatible join (normalized and re-encoded)"

    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"mode": mode, "clips": len(inputs)},
        command_previews=[b.preview()],
        summary={"Action": action, "Clips": str(len(inputs))},
    )
