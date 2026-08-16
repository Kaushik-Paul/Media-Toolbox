"""Compress Video: codec/quality/resolution/audio-bitrate control."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    QUALITY_PRESETS,
    VIDEO_ENCODERS,
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    finalize_output,
    part_path_for,
    require_encoder,
    run_ffmpeg,
    scale_to_height_filter,
)

RESOLUTION_HEIGHTS = {"2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
AUDIO_BITRATES = {"320k", "256k", "192k", "128k", "96k", "64k"}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")

    codec_key = str(params.get("codec", "h264"))
    if codec_key not in VIDEO_ENCODERS:
        raise OperationError(f"Unsupported codec: {codec_key}")
    encoder, out_ext = VIDEO_ENCODERS[codec_key]
    require_encoder(ctx, encoder)

    quality = str(params.get("quality", "balanced"))
    if quality == "custom":
        preset = str(params.get("preset", "medium"))
        crf = int(params.get("crf", 23))
        if not 0 <= crf <= 51:
            raise OperationError("CRF must be between 0 and 51.")
    else:
        preset, crf = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])

    filters: list[str] = []
    resolution = str(params.get("resolution", "original"))
    allow_upscale = bool(params.get("allow_upscale", False))
    if resolution in RESOLUTION_HEIGHTS:
        filters.append(scale_to_height_filter(RESOLUTION_HEIGHTS[resolution], prevent_upscale=not allow_upscale))
    elif resolution == "custom":
        width, height = int(params.get("width", 0)), int(params.get("height", 0))
        if width <= 0 and height <= 0:
            raise OperationError("Custom resolution needs a width and/or height.")
        w = str(width // 2 * 2) if width > 0 else "-2"
        h = str(height // 2 * 2) if height > 0 else "-2"
        if not allow_upscale and info.primary_video:
            src_w, src_h = info.primary_video.width or 0, info.primary_video.height or 0
            if width > 0 and src_w and width > src_w:
                raise OperationError("Target width exceeds the source. Enable 'Allow upscaling' to proceed.")
            if height > 0 and src_h and height > src_h:
                raise OperationError("Target height exceeds the source. Enable 'Allow upscaling' to proceed.")
        filters.append(f"scale={w}:{h}")

    remove_audio = bool(params.get("remove_audio", False))
    audio_bitrate = str(params.get("audio_bitrate", "128k"))

    out_name = output_name(info.filename, "compressed", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    if filters:
        b.add("-vf", ",".join(filters))
    b.add("-c:v", encoder, "-preset", preset, "-crf", crf)
    if codec_key == "h264":
        b.add("-pix_fmt", "yuv420p")
    if remove_audio or not info.has_audio:
        b.add("-an")
    elif audio_bitrate == "original":
        b.add("-c:a", "copy")
    else:
        if audio_bitrate not in AUDIO_BITRATES:
            raise OperationError(f"Unsupported audio bitrate: {audio_bitrate}")
        require_encoder(ctx, "aac")
        b.add("-c:a", "aac", "-b:a", audio_bitrate)
    b.add("-movflags", "+faststart")
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={
            "codec": codec_key, "quality": quality, "preset": preset, "crf": crf,
            "resolution": resolution, "audio_bitrate": audio_bitrate, "remove_audio": remove_audio,
        },
        command_previews=[b.preview()],
    )
