"""CPU-side postprocessing for GPU jobs: subtitle writers, audio conversion,
ZIP packaging, and video reassembly. Never runs inside @spaces.GPU.
"""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

from gpu.backend.job_manager import JobContext, OperationError
from gpu.backend.preprocessing import finalize_output, part_path_for, run_ffmpeg

log = logging.getLogger(__name__)


# -- subtitle / transcript writers ----------------------------------------------


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")


def normalize_chunks(result: dict, total_duration: float | None = None) -> list[dict]:
    """Normalize Whisper pipeline chunks into [{start, end, text}]."""
    cues: list[dict] = []
    for chunk in result.get("chunks") or []:
        start, end = chunk.get("timestamp") or (None, None)
        text = (chunk.get("text") or "").strip()
        if not text or start is None:
            continue
        if end is None:
            end = total_duration if total_duration else start + 2.0
        cues.append({"start": float(start), "end": float(end), "text": text})
    return cues


def group_words_to_cues(words: list[dict], max_chars: int = 42, max_gap: float = 0.8,
                        max_duration: float = 5.0) -> list[dict]:
    """Group word-level timestamps into readable subtitle cues."""
    cues: list[dict] = []
    current: dict | None = None
    for word in words:
        text = word["text"]
        if current is None:
            current = {"start": word["start"], "end": word["end"], "text": text}
            continue
        gap = word["start"] - current["end"]
        grown = current["text"] + text
        if (gap > max_gap
                or len(grown) > max_chars
                or word["end"] - current["start"] > max_duration):
            cues.append(current)
            current = {"start": word["start"], "end": word["end"], "text": text}
        else:
            current["end"] = word["end"]
            current["text"] = grown
    if current is not None:
        cues.append(current)
    for cue in cues:
        cue["text"] = cue["text"].strip()
    return [c for c in cues if c["text"]]


def write_txt(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def write_srt(path: Path, cues: list[dict]) -> Path:
    lines: list[str] = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_vtt(path: Path, cues: list[dict]) -> Path:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{_vtt_time(cue['start'])} --> {_vtt_time(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# -- audio conversion / packaging -----------------------------------------------

_AUDIO_ENCODE_ARGS = {
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
}


def convert_audio(ctx: JobContext, input_path: Path, out_path: Path, fmt: str) -> Path:
    """Convert a WAV stem to the requested delivery format."""
    codec_args = _AUDIO_ENCODE_ARGS.get(fmt)
    if codec_args is None:
        raise OperationError(f"Unsupported audio output format: {fmt}")
    if fmt == "wav":
        # Stems are already WAV; nothing to re-encode.
        if input_path.resolve() != out_path.resolve():
            out_path.write_bytes(input_path.read_bytes())
        return out_path
    part = part_path_for(out_path)
    args = [ctx.settings.ffmpeg_path, "-y", "-i", str(input_path), *codec_args, str(part)]
    ctx.runner.run(args, cancel_event=ctx.cancel_event)
    return finalize_output(ctx, part, out_path)


def zip_outputs(paths: list[Path], zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=path.name)
    return zip_path


# -- video reassembly -------------------------------------------------------------


def encode_chunk_video(ctx: JobContext, frames_dir: Path, frame_count: int,
                       fps: float, out_path: Path) -> Path:
    """Encode a directory of %06d.png frames into an intermediate video chunk."""
    args = [
        ctx.settings.ffmpeg_path,
        "-y",
        "-framerate", f"{fps:.6g}",
        "-i", str(frames_dir / "%06d.png"),
        "-frames:v", str(frame_count),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    ctx.runner.run(args, cancel_event=ctx.cancel_event)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise OperationError("Failed to encode an upscaled video chunk.", frames_dir.name)
    return out_path


def concat_chunks(ctx: JobContext, chunk_paths: list[Path], out_path: Path) -> Path:
    """Concatenate same-codec chunks with the concat demuxer (stream copy)."""
    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "".join(f"file '{p}'\n" for p in chunk_paths), encoding="utf-8"
    )
    args = [
        ctx.settings.ffmpeg_path,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    ctx.runner.run(args, cancel_event=ctx.cancel_event)
    list_file.unlink(missing_ok=True)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise OperationError("Failed to join upscaled video chunks.")
    return out_path


def mux_original_audio(ctx: JobContext, video_path: Path, source_path: Path,
                       out_path: Path) -> Path:
    """Attach the original audio track (if any) to the upscaled video."""
    has_audio = bool(ctx.media_info and ctx.media_info.has_audio)
    part = part_path_for(out_path)
    args = [ctx.settings.ffmpeg_path, "-y", "-i", str(video_path)]
    if has_audio:
        args += ["-i", str(source_path), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c:v", "copy"]
    args += ["-movflags", "+faststart", str(part)]
    duration = ctx.media_info.duration_seconds if ctx.media_info else None
    run_ffmpeg(ctx, args, total_duration=duration, progress_offset=90.0,
               progress_span=8.0, stage="Muxing audio")
    return finalize_output(ctx, part, out_path)
