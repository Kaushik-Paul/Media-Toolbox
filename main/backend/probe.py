"""FFprobe analysis producing typed MediaInfo."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.models import MediaInfo, StreamInfo


class ProbeError(Exception):
    pass


def _parse_fps(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            if den_f == 0:
                return None
            return round(float(num) / den_f, 3)
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_probe_json(data: dict, filename: str = "", size: int = 0) -> MediaInfo:
    fmt = data.get("format", {}) or {}
    streams: list[StreamInfo] = []
    for raw in data.get("streams", []) or []:
        tags = raw.get("tags", {}) or {}
        streams.append(
            StreamInfo(
                index=raw.get("index", 0),
                codec_type=raw.get("codec_type", ""),
                codec_name=raw.get("codec_name", ""),
                width=_parse_int(raw.get("width")),
                height=_parse_int(raw.get("height")),
                fps=_parse_fps(raw.get("avg_frame_rate") or raw.get("r_frame_rate")),
                bit_rate=_parse_int(raw.get("bit_rate")),
                sample_rate=_parse_int(raw.get("sample_rate")),
                channels=_parse_int(raw.get("channels")),
                pix_fmt=raw.get("pix_fmt"),
                language=tags.get("language"),
                duration=_parse_float(raw.get("duration")),
            )
        )
    return MediaInfo(
        filename=filename,
        size=size or _parse_int(fmt.get("size")) or 0,
        duration_seconds=_parse_float(fmt.get("duration")),
        format_name=fmt.get("format_name", ""),
        bit_rate=_parse_int(fmt.get("bit_rate")),
        streams=streams,
    )


class FFprobeService:
    def __init__(self, ffprobe_path: str = "ffprobe"):
        self.ffprobe_path = ffprobe_path

    def probe(self, path: str | Path) -> MediaInfo:
        path = Path(path)
        args = [
            self.ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120, shell=False)
        except FileNotFoundError as exc:
            raise ProbeError(f"ffprobe not found at {self.ffprobe_path!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("ffprobe timed out") from exc
        if proc.returncode != 0:
            raise ProbeError(f"ffprobe failed: {proc.stderr.strip()[:500]}")
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ProbeError("ffprobe returned invalid JSON") from exc
        if not data.get("format") and not data.get("streams"):
            raise ProbeError("ffprobe returned no media information (unsupported or corrupt file)")
        return parse_probe_json(data, filename=path.name, size=path.stat().st_size if path.exists() else 0)
