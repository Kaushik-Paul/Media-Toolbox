"""Shared operation scaffolding: context, results, and FFmpeg helpers."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.capabilities import FFmpegCapabilities
from backend.command_builder import FFmpegCommandBuilder
from backend.ffmpeg_runner import FFmpegRunner, ProgressUpdate
from backend.probe import FFprobeService
from core.config import Settings
from core.media_types import is_probed_media
from core.models import MediaInfo


class OperationError(Exception):
    """User-facing operation failure with a concise message."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


@dataclass
class ProducedOutput:
    local_path: Path
    filename: str
    output_id: str = "main"


@dataclass
class OperationResult:
    outputs: list[ProducedOutput]
    parameters: dict = field(default_factory=dict)
    command_previews: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


@dataclass
class JobContext:
    job_id: str
    work_dir: Path
    settings: Settings
    probe: FFprobeService
    runner: FFmpegRunner
    capabilities: FFmpegCapabilities
    cancel_event: threading.Event
    on_progress: callable | None = None
    media_info: MediaInfo | None = None

    @property
    def out_dir(self) -> Path:
        path = self.work_dir / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def builder(self) -> FFmpegCommandBuilder:
        return FFmpegCommandBuilder(self.settings.ffmpeg_path)

    def emit(self, update: ProgressUpdate) -> None:
        if self.on_progress:
            self.on_progress(update)

    def report(self, percent: float, text: str = "") -> None:
        self.emit(ProgressUpdate(percent=percent, processed_seconds=0.0, speed=None, elapsed_seconds=0.0))


def run_ffmpeg(
    ctx: JobContext,
    args: list[str],
    *,
    total_duration: float | None = None,
    progress_offset: float = 0.0,
    progress_span: float = 100.0,
) -> None:
    ctx.runner.run(
        args,
        total_duration=total_duration,
        on_progress=ctx.on_progress,
        cancel_event=ctx.cancel_event,
        progress_offset=progress_offset,
        progress_span=progress_span,
    )


def finalize_output(ctx: JobContext, part_path: Path, final_path: Path) -> Path:
    """Verify a produced file (ffprobe for media) then rename from .part."""
    if not part_path.exists() or part_path.stat().st_size == 0:
        raise OperationError("FFmpeg produced no output.", f"missing/empty: {part_path.name}")
    if is_probed_media(final_path.name):
        try:
            ctx.probe.probe(part_path)
        except Exception as exc:
            raise OperationError(
                "Output validation failed.", f"ffprobe could not read {part_path.name}: {exc}"
            ) from exc
    part_path.rename(final_path)
    return final_path


def part_path_for(final_path: Path) -> Path:
    return final_path.with_name(final_path.stem + ".part" + final_path.suffix)


# -- filter helpers -----------------------------------------------------------


def scale_to_height_filter(height: int, prevent_upscale: bool = True) -> str:
    """Even-dimension, aspect-preserving scale to a target height."""
    height = int(height) // 2 * 2
    if prevent_upscale:
        return f"scale=-2:min({height}\\,ih)"
    return f"scale=-2:{height}"


def fit_inside_filter(width: int, height: int, prevent_upscale: bool = True) -> str:
    width, height = int(width) // 2 * 2, int(height) // 2 * 2
    if prevent_upscale:
        return (
            f"scale='min(iw\\,{width})':'min(ih\\,{height})'"
            ":force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2"


def exact_size_filter(width: int, height: int, mode: str) -> str:
    """mode: stretch | crop | letterbox"""
    width, height = int(width) // 2 * 2, int(height) // 2 * 2
    if mode == "stretch":
        return f"scale={width}:{height}"
    if mode == "crop":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase"
            f":force_divisible_by=2,crop={width}:{height}"
        )
    # letterbox
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease"
        f":force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def atempo_chain(factor: float) -> str:
    """Build an atempo filter chain for any positive speed factor."""
    if factor <= 0:
        raise OperationError("Speed factor must be positive.")
    parts: list[str] = []
    remaining = factor
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.6g}")
    return ",".join(parts)


def escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument."""
    text = str(path)
    for ch, rep in (("\\", "\\\\"), (":", "\\:"), ("'", "\\'"), (",", "\\,"), ("[", "\\["), ("]", "\\]")):
        text = text.replace(ch, rep)
    return text


def crop_preset_dimensions(src_w: int, src_h: int, preset: str) -> tuple[int, int, int, int]:
    """Largest centered crop matching an aspect preset. Returns (w, h, x, y)."""
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "1:1": 1.0, "21:9": 21 / 9}
    ratio = ratios.get(preset)
    if ratio is None:
        raise OperationError(f"Unknown crop preset: {preset}")
    if src_w / src_h > ratio:
        new_h = src_h
        new_w = int(src_h * ratio)
    else:
        new_w = src_w
        new_h = int(src_w / ratio)
    new_w, new_h = new_w // 2 * 2, new_h // 2 * 2
    x, y = (src_w - new_w) // 2, (src_h - new_h) // 2
    return new_w, new_h, x, y


# -- codec maps ----------------------------------------------------------------

VIDEO_ENCODERS = {
    "h264": ("libx264", "mp4"),
    "h265": ("libx265", "mp4"),
    "av1": ("libsvtav1", "mp4"),
}

AUDIO_ENCODERS = {
    "mp3": ("libmp3lame", "mp3"),
    "m4a": ("aac", "m4a"),
    "aac": ("aac", "m4a"),
    "opus": ("libopus", "opus"),
    "flac": ("flac", "flac"),
    "wav": ("pcm_s16le", "wav"),
}

# Audio codecs that can be stream-copied into a given extension.
COPY_COMPATIBLE_AUDIO = {
    "mp3": {"mp3"},
    "m4a": {"aac", "alac"},
    "aac": {"aac"},
    "opus": {"opus"},
    "ogg": {"opus", "vorbis"},
    "flac": {"flac"},
    "wav": {"pcm_s16le", "pcm_s24le", "pcm_f32le", "pcm_u8"},
}

# Subtitle codecs that are text-based and can be carried by MP4 (as mov_text).
# Bitmap subtitles (PGS, VobSub, ...) cannot and are dropped for MP4 outputs.
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}

QUALITY_PRESETS = {
    "quick": ("veryfast", 24),
    "balanced": ("medium", 23),
    "high": ("slow", 20),
}


def require_encoder(ctx: JobContext, name: str) -> None:
    if not ctx.capabilities.has_encoder(name):
        raise OperationError(f"Encoder '{name}' is not available in this FFmpeg build.")
