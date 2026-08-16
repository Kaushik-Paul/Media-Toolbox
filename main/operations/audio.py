"""Audio tools: extract, convert, compress, sample rate, channels, normalize,
trim, speed, and remove-audio (video). Dispatched via params['mode']."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from core.filenames import output_name
from core.media_types import ext_of
from core.time_utils import parse_time
from operations.base import (
    AUDIO_ENCODERS,
    COPY_COMPATIBLE_AUDIO,
    JobContext,
    OperationError,
    OperationResult,
    ProducedOutput,
    atempo_chain,
    finalize_output,
    part_path_for,
    require_encoder,
    run_ffmpeg,
)

AUDIO_BITRATES = {"320k", "256k", "192k", "128k", "96k", "64k"}
SAMPLE_RATES = {"48000", "44100", "32000", "24000", "16000"}


def _require_audio(ctx: JobContext) -> None:
    if ctx.media_info is None or not ctx.media_info.has_audio:
        raise OperationError("This file does not contain an audio stream.")


def _encode_audio(ctx: JobContext, src: Path, fmt: str, bitrate: str | None,
                  extra: list[str], suffix: str, strip_video: bool = True) -> OperationResult:
    info = ctx.media_info
    if fmt not in AUDIO_ENCODERS:
        raise OperationError(f"Unsupported audio format: {fmt}")
    encoder, out_ext = AUDIO_ENCODERS[fmt]
    require_encoder(ctx, encoder)

    out_name = output_name(info.filename, suffix, out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)

    b = ctx.builder()
    b.input(src)
    if strip_video:
        b.add("-vn")
    b.add("-c:a", encoder)
    if bitrate and encoder not in ("pcm_s16le", "flac"):
        b.add("-b:a", bitrate)
    b.add(*extra)
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"format": fmt, "bitrate": bitrate or ""},
        command_previews=[b.preview()],
    )


# -- modes ----------------------------------------------------------------------


def _extract_audio(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    info = ctx.media_info
    fmt = str(params.get("format", "mp3")).lower()
    mode = str(params.get("extract_mode", "convert"))  # copy | convert
    audio = info.primary_audio

    if mode == "copy" and audio is not None:
        # Pick a container extension that matches the source codec.
        codec_ext = {
            "aac": "m4a", "mp3": "mp3", "opus": "opus", "flac": "flac",
            "vorbis": "ogg", "pcm_s16le": "wav", "pcm_s24le": "wav", "alac": "m4a",
        }.get(audio.codec_name)
        if codec_ext is None:
            raise OperationError(
                f"Audio codec '{audio.codec_name}' cannot be stream-copied to a file. Use Convert mode."
            )
        out_name = output_name(info.filename, "audio", codec_ext)
        final = ctx.out_dir / out_name
        part = part_path_for(final)
        b = ctx.builder()
        b.input(src)
        b.add("-vn", "-c:a", "copy")
        b.output(part)
        run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
        finalize_output(ctx, part, final)
        return OperationResult(
            outputs=[ProducedOutput(final, out_name)],
            parameters={"extract_mode": "copy", "codec": audio.codec_name},
            command_previews=[b.preview()],
            summary={"Action": "Stream copy (no re-encode, no quality loss)"},
        )

    # Convert mode: copy when the requested format already matches the codec.
    if audio is not None and audio.codec_name in COPY_COMPATIBLE_AUDIO.get(fmt, set()):
        out_ext = AUDIO_ENCODERS[fmt][1]
        out_name = output_name(info.filename, "audio", out_ext)
        final = ctx.out_dir / out_name
        part = part_path_for(final)
        b = ctx.builder()
        b.input(src)
        b.add("-vn", "-c:a", "copy")
        b.output(part)
        run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
        finalize_output(ctx, part, final)
        return OperationResult(
            outputs=[ProducedOutput(final, out_name)],
            parameters={"format": fmt, "action": "copy"},
            command_previews=[b.preview()],
            summary={"Action": "Stream copy (already in the requested format)"},
        )

    bitrate = str(params.get("bitrate", "192k"))
    return _encode_audio(ctx, src, fmt, bitrate, [], "audio")


def _remove_audio(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("This file does not contain a video stream.")
    out_ext = ext_of(info.filename) or "mp4"
    out_name = output_name(info.filename, "no-audio", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    b = ctx.builder()
    b.input(src)
    b.add("-map", "0", "-c", "copy", "-an")
    if out_ext == "mp4":
        b.add("-movflags", "+faststart")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={},
        command_previews=[b.preview()],
        summary={"Action": "Video stream copied without re-encoding"},
    )


def _convert_audio(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    fmt = str(params.get("format", "mp3")).lower()
    bitrate = str(params.get("bitrate", "192k"))
    return _encode_audio(ctx, src, fmt, bitrate, [], "converted")


def _compress_audio(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    bitrate = str(params.get("bitrate", "96k"))
    if bitrate not in AUDIO_BITRATES:
        raise OperationError(f"Unsupported bitrate: {bitrate}")
    fmt = str(params.get("format", "mp3")).lower()
    return _encode_audio(ctx, src, fmt, bitrate, [], f"{bitrate}")


def _sample_rate(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    rate = str(params.get("sample_rate", "44100"))
    if rate not in SAMPLE_RATES:
        raise OperationError(f"Unsupported sample rate: {rate}")
    fmt = str(params.get("format", "flac")).lower()
    return _encode_audio(ctx, src, fmt, None, ["-ar", rate], f"{rate}hz")


def _channels(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    direction = str(params.get("channels", "mono"))
    count = {"mono": 1, "stereo": 2}.get(direction)
    if count is None:
        raise OperationError(f"Unsupported channel mode: {direction}")
    fmt = str(params.get("format", "flac")).lower()
    return _encode_audio(ctx, src, fmt, None, ["-ac", str(count)], direction)


def _loudnorm_measure(ctx: JobContext, src: Path) -> dict:
    args = [
        ctx.settings.ffmpeg_path, "-hide_banner", "-i", str(src),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600, shell=False)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.DOTALL)
    if proc.returncode != 0 or not match:
        raise OperationError("Loudness measurement failed.", proc.stderr.strip()[-400:])
    return json.loads(match.group(0))


def _normalize(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    info = ctx.media_info
    mode = str(params.get("normalize_mode", "simple"))  # simple | ebu
    fmt = str(params.get("format", "flac")).lower()
    if fmt not in AUDIO_ENCODERS:
        raise OperationError(f"Unsupported audio format: {fmt}")
    encoder, out_ext = AUDIO_ENCODERS[fmt]
    require_encoder(ctx, encoder)
    if not ctx.capabilities.has_filter("loudnorm"):
        raise OperationError("The loudnorm filter is not available in this FFmpeg build.")

    if mode == "ebu":
        ctx.report(5, "Measuring loudness (pass 1 of 2)")
        m = _loudnorm_measure(ctx, src)
        af = (
            f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}"
            f":measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
        )
        offset, span = 10.0, 90.0
    else:
        af = "loudnorm=I=-16:TP=-1.5:LRA=11"
        offset, span = 0.0, 100.0

    out_name = output_name(info.filename, "normalized", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    b = ctx.builder()
    b.input(src)
    b.add("-vn", "-af", af, "-c:a", encoder)
    if encoder not in ("pcm_s16le", "flac"):
        b.add("-b:a", "192k")
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=info.duration_seconds,
               progress_offset=offset, progress_span=span)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"normalize_mode": mode, "format": fmt},
        command_previews=[b.preview()],
        summary={"Normalization": "EBU R128 two-pass" if mode == "ebu" else "Simple (one-pass loudnorm)"},
    )


def _audio_trim(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    info = ctx.media_info
    start = parse_time(params.get("start")) or 0.0
    end = parse_time(params.get("end"))
    if end is None:
        end = info.duration_seconds
    if end is None or end <= start or start < 0:
        raise OperationError("Invalid range: end must be greater than start.")
    fmt = str(params.get("format", "flac")).lower()
    encoder, out_ext = AUDIO_ENCODERS.get(fmt, AUDIO_ENCODERS["flac"])
    require_encoder(ctx, encoder)
    out_name = output_name(info.filename, "segment", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    b = ctx.builder()
    b.input(src, "-ss", f"{start:.3f}")
    b.add("-t", f"{end - start:.3f}", "-vn", "-c:a", encoder)
    b.output(part)
    run_ffmpeg(ctx, b.build(), total_duration=end - start)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"start": start, "end": end},
        command_previews=[b.preview()],
    )


def _audio_speed(ctx: JobContext, src: Path, params: dict) -> OperationResult:
    _require_audio(ctx)
    info = ctx.media_info
    factor = float(params.get("speed", 1.5))
    if not 0.1 <= factor <= 10:
        raise OperationError("Speed factor must be between 0.1 and 10.")
    fmt = str(params.get("format", "flac")).lower()
    encoder, out_ext = AUDIO_ENCODERS.get(fmt, AUDIO_ENCODERS["flac"])
    require_encoder(ctx, encoder)
    out_name = output_name(info.filename, f"{factor:g}x", out_ext)
    final = ctx.out_dir / out_name
    part = part_path_for(final)
    b = ctx.builder()
    b.input(src)
    b.add("-vn", "-af", atempo_chain(factor), "-c:a", encoder)
    b.output(part)
    duration = (info.duration_seconds or 0) / factor
    run_ffmpeg(ctx, b.build(), total_duration=duration or None)
    finalize_output(ctx, part, final)
    return OperationResult(
        outputs=[ProducedOutput(final, out_name)],
        parameters={"speed": factor},
        command_previews=[b.preview()],
    )


_MODES = {
    "extract_audio": _extract_audio,
    "remove_audio": _remove_audio,
    "convert_audio": _convert_audio,
    "compress_audio": _compress_audio,
    "audio_sample_rate": _sample_rate,
    "audio_channels": _channels,
    "audio_normalize": _normalize,
    "audio_trim": _audio_trim,
    "audio_speed": _audio_speed,
}


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    mode = str(params.get("mode", ""))
    handler = _MODES.get(mode)
    if handler is None:
        raise OperationError(f"Unknown audio mode: {mode}")
    return handler(ctx, inputs[0], params)
