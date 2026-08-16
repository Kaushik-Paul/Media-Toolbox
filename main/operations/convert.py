"""Format conversion between MP4 / MKV / MOV / WebM with auto remux-or-transcode."""
from __future__ import annotations

from pathlib import Path

from core.filenames import output_name
from operations.base import (
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    finalize_output,
    part_path_for,
    require_encoder,
    run_ffmpeg,
)

CONTAINERS = {"mp4", "mkv", "mov", "webm"}

# Which source codecs can be stream-copied into each container.
VIDEO_COPY_OK = {
    "mp4": {"h264", "hevc", "av1", "mpeg4", "mpeg2video", "vp9"},
    "mkv": None,  # mkv accepts virtually everything
    "mov": {"h264", "hevc", "mpeg4", "prores"},
    "webm": {"vp8", "vp9", "av1"},
}
AUDIO_COPY_OK = {
    "mp4": {"aac", "mp3", "alac", "flac", "opus"},
    "mkv": None,
    "mov": {"aac", "mp3", "alac", "pcm_s16le"},
    "webm": {"opus", "vorbis"},
}


def _can_copy(allowed: set[str] | None, codec: str) -> bool:
    return allowed is None or codec in allowed


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    src = inputs[0]
    info = ctx.media_info
    if info is None or not (info.has_video or info.has_audio):
        raise OperationError("No audio or video streams found in this file.")

    target = str(params.get("container", "mp4")).lower()
    if target not in CONTAINERS:
        raise OperationError(f"Unsupported container: {target}")
    mode = str(params.get("mode", "auto"))  # auto | remux | reencode

    v = info.primary_video
    a = info.primary_audio
    video_ok = v is not None and _can_copy(VIDEO_COPY_OK[target], v.codec_name)
    audio_ok = a is None or _can_copy(AUDIO_COPY_OK[target], a.codec_name)

    if mode == "remux" and not (video_ok and audio_ok):
        raise OperationError(
            f"Streams cannot be remuxed into .{target}.",
            f"video={v.codec_name if v else '-'} audio={a.codec_name if a else '-'}; "
            "use Auto or Re-encode instead.",
        )
    copy_streams = mode == "remux" or (mode == "auto" and video_ok and audio_ok)

    out_name = output_name(info.filename, "converted", target)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    b.add("-map", "0")
    if copy_streams:
        b.add("-c", "copy")
        action = "remux"
    else:
        action = "transcode"
        if v is not None:
            if target == "webm":
                require_encoder(ctx, "libvpx-vp9")
                b.add("-c:v", "libvpx-vp9", "-crf", 34, "-b:v", 0, "-row-mt", 1)
            else:
                require_encoder(ctx, "libx264")
                b.add("-c:v", "libx264", "-preset", "medium", "-crf", 22, "-pix_fmt", "yuv420p")
        if a is not None:
            if target == "webm":
                require_encoder(ctx, "libopus")
                b.add("-c:a", "libopus", "-b:a", "128k")
            else:
                require_encoder(ctx, "aac")
                b.add("-c:a", "aac", "-b:a", "160k")
        # Subtitle streams that the target cannot carry are dropped.
        if target in ("mp4", "mov", "webm"):
            b.add("-sn")
    if target == "mp4":
        b.add("-movflags", "+faststart")
    b.output(part)

    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)

    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"container": target, "mode": mode, "action": action},
        command_previews=[b.preview()],
        summary={"Action": "Remux (stream copy, no quality loss)" if action == "remux" else "Re-encoded"},
    )
