"""Target File Size: two-pass H.264/H.265 encoding to hit a requested size."""
from __future__ import annotations

import os
from pathlib import Path

from core.filenames import output_name
from operations.base import (
    TEXT_SUBTITLE_CODECS,
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    finalize_output,
    part_path_for,
    require_encoder,
    run_ffmpeg,
)

ENCODERS = {"h264": "libx264", "h265": "libx265"}


def compute_bitrates(
    target_mb: float, duration_seconds: float, audio_bitrate_kbps: int, safety_factor: float
) -> tuple[int, int]:
    """Return (total_kbps, video_kbps) for the requested target size."""
    if duration_seconds <= 0:
        raise OperationError("Cannot determine input duration; target-size mode needs it.")
    usable_bytes = target_mb * 1024 * 1024 * safety_factor
    total_kbps = usable_bytes * 8 / duration_seconds / 1000
    video_kbps = int(total_kbps - audio_bitrate_kbps)
    return int(total_kbps), video_kbps


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    target_mb = float(params.get("target_mb", 0))
    if target_mb <= 0:
        raise OperationError("Target size must be greater than zero.")
    audio_bitrate_kbps = int(str(params.get("audio_bitrate", "128k")).rstrip("k"))
    codec_key = str(params.get("codec", "h264"))
    encoder = ENCODERS.get(codec_key)
    if encoder is None:
        raise OperationError(f"Target size currently supports: {', '.join(ENCODERS)}")
    require_encoder(ctx, encoder)

    duration = info.duration_seconds or 0
    total_kbps, video_kbps = compute_bitrates(
        target_mb, duration, audio_bitrate_kbps, ctx.settings.target_size_safety_factor
    )
    if video_kbps < ctx.settings.min_video_bitrate_kbps:
        raise OperationError(
            "Target size is too small for this duration.",
            f"Computed video bitrate {video_kbps} kbps is below the "
            f"{ctx.settings.min_video_bitrate_kbps} kbps floor. Increase the target size.",
        )

    out_name = output_name(info.filename, f"{target_mb:g}mb", "mp4")
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    passlog = str(ctx.work_dir / "twopass")

    common = ["-c:v", encoder, "-b:v", f"{video_kbps}k", "-preset", "medium"]
    # MP4 can only carry text subtitles (stored as mov_text); bitmap tracks
    # (PGS, VobSub, ...) are dropped rather than failing the whole encode.
    text_subs = [s for s in info.subtitle_streams if s.codec_name in TEXT_SUBTITLE_CODECS]

    # Pass 1 (analysis, video only, null output)
    b1 = ctx.builder()
    b1.input(src)
    b1.add("-map", "0:v:0")
    b1.add(*common, "-pass", 1, "-passlogfile", passlog, "-f", "null", os.devnull)
    run_ffmpeg(ctx, b1.build(), total_duration=duration, progress_offset=0, progress_span=50)

    # Pass 2
    b2 = ctx.builder()
    b2.input(src)
    b2.add("-map", "0:v:0")
    if info.has_audio:
        b2.add("-map", "0:a:0?")
    for s in text_subs:
        b2.add("-map", f"0:{s.index}")
    b2.add(*common, "-pass", 2, "-passlogfile", passlog)
    if info.has_audio:
        b2.add("-c:a", "aac", "-b:a", f"{audio_bitrate_kbps}k")
    if text_subs:
        b2.add("-c:s", "mov_text")
    b2.add("-movflags", "+faststart")
    b2.output(part)
    run_ffmpeg(ctx, b2.build(), total_duration=duration, progress_offset=50, progress_span=50)

    finalize_output(ctx, part, final)

    actual_mb = final.stat().st_size / (1024 * 1024)
    diff_pct = (actual_mb - target_mb) / target_mb * 100
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={
            "codec": codec_key, "target_mb": target_mb,
            "video_bitrate_kbps": video_kbps, "audio_bitrate_kbps": audio_bitrate_kbps,
        },
        command_previews=[b1.preview(), b2.preview()],
        summary={
            "Target": f"{target_mb:g} MB",
            "Actual": f"{actual_mb:.1f} MB",
            "Difference": f"{diff_pct:+.1f}%",
        },
    )
