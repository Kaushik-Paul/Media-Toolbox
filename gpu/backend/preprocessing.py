"""CPU-side preprocessing for GPU jobs (FFmpeg only, never inside @spaces.GPU).

All commands are argument arrays executed with shell=False via the shared
FFmpegRunner from ``main/`` (PLAN.md section 9).
"""
from __future__ import annotations

import logging
from pathlib import Path

from backend.ffmpeg_runner import ProgressUpdate
from core.media_types import is_probed_media

from gpu.backend.job_manager import JobContext, OperationError

log = logging.getLogger(__name__)


def run_ffmpeg(
    ctx: JobContext,
    args: list[str],
    *,
    total_duration: float | None = None,
    progress_offset: float = 0.0,
    progress_span: float = 100.0,
    stage: str = "Processing",
) -> None:
    """Run an FFmpeg argument array, mapping progress into the given span."""

    def _on_progress(update: ProgressUpdate) -> None:
        ctx.report(
            update.percent,
            f"{stage}... {update.processed_seconds:.1f}s media"
            + (f", speed {update.speed:.2f}x" if update.speed else ""),
        )

    ctx.runner.run(
        args,
        total_duration=total_duration,
        on_progress=_on_progress if ctx.on_progress else None,
        cancel_event=ctx.cancel_event,
        progress_offset=progress_offset,
        progress_span=progress_span,
    )


def part_path_for(final_path: Path) -> Path:
    return final_path.with_name(final_path.stem + ".part" + final_path.suffix)


def finalize_output(ctx: JobContext, part_path: Path, final_path: Path) -> Path:
    """Verify a produced file (ffprobe for media) then rename from .part."""
    if not part_path.exists() or part_path.stat().st_size == 0:
        raise OperationError("Processing produced no output.", f"missing/empty: {part_path.name}")
    if is_probed_media(final_path.name):
        try:
            ctx.probe.probe(part_path)
        except Exception as exc:
            raise OperationError(
                "Output validation failed.", f"ffprobe could not read {part_path.name}: {exc}"
            ) from exc
    part_path.rename(final_path)
    return final_path


def extract_audio(
    ctx: JobContext,
    input_path: Path,
    out_path: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    progress_span: tuple[float, float] = (0.0, 10.0),
    stage: str = "Extracting audio",
) -> Path:
    """Extract audio as PCM WAV (mono 16 kHz by default, for Whisper)."""
    duration = ctx.media_info.duration_seconds if ctx.media_info else None
    args = [
        ctx.settings.ffmpeg_path,
        "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(part_path_for(out_path)),
    ]
    run_ffmpeg(
        ctx, args,
        total_duration=duration,
        progress_offset=progress_span[0],
        progress_span=progress_span[1] - progress_span[0],
        stage=stage,
    )
    return finalize_output(ctx, part_path_for(out_path), out_path)


def extract_frames(
    ctx: JobContext,
    video_path: Path,
    frames_dir: Path,
    *,
    start: float,
    duration: float,
) -> list[Path]:
    """Extract a time window of frames as PNGs. Returns sorted frame paths."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    args = [
        ctx.settings.ffmpeg_path,
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video_path),
        "-vsync", "0",
        "-start_number", "0",
        str(frames_dir / "%06d.png"),
    ]
    ctx.runner.run(args, cancel_event=ctx.cancel_event)
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        raise OperationError(
            "Could not extract video frames.",
            f"window start={start:.2f}s duration={duration:.2f}s",
        )
    return frames
