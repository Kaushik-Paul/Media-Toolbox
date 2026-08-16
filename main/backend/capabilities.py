"""Startup detection of FFmpeg encoders/decoders/filters/formats."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class FFmpegCapabilities:
    version: str = "unknown"
    encoders: set[str] = field(default_factory=set)
    decoders: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    formats: set[str] = field(default_factory=set)

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def summary(self) -> dict[str, bool]:
        return {
            "libx264": self.has_encoder("libx264"),
            "libx265": self.has_encoder("libx265"),
            "libsvtav1": self.has_encoder("libsvtav1"),
            "libvpx_vp9": self.has_encoder("libvpx-vp9"),
            "aac": self.has_encoder("aac"),
            "libmp3lame": self.has_encoder("libmp3lame"),
            "libopus": self.has_encoder("libopus"),
            "flac": self.has_encoder("flac"),
            "subtitles_filter": self.has_filter("subtitles"),
            "loudnorm_filter": self.has_filter("loudnorm"),
        }


def _run_listing(ffmpeg_path: str, flag: str) -> str:
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", flag],
            capture_output=True, text=True, timeout=60, shell=False,
        )
        return proc.stdout or ""
    except Exception as exc:
        log.warning("capability detection failed for %s: %s", flag, exc)
        return ""


def parse_listing(text: str) -> set[str]:
    """Parse 'ffmpeg -encoders/-decoders/-filters/-formats' output into a name set."""
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 9 or line.startswith(("-", "=")):
            continue
        # Lines look like: ' V..... libx264    description' or ' A....D aac ...'
        parts = line.split()
        if len(parts) >= 2 and 2 <= len(parts[0]) <= 8 and not parts[0].isalpha():
            names.add(parts[1])
    return names


def detect_capabilities(ffmpeg_path: str = "ffmpeg") -> FFmpegCapabilities:
    caps = FFmpegCapabilities()
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=30, shell=False
        )
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        # 'ffmpeg version 6.1.1-...' -> '6.1.1-...'
        parts = first.split()
        caps.version = parts[2] if len(parts) >= 3 and parts[0] == "ffmpeg" else first
    except Exception as exc:
        log.warning("ffmpeg -version failed: %s", exc)
    caps.encoders = parse_listing(_run_listing(ffmpeg_path, "-encoders"))
    caps.decoders = parse_listing(_run_listing(ffmpeg_path, "-decoders"))
    caps.filters = parse_listing(_run_listing(ffmpeg_path, "-filters"))
    caps.formats = parse_listing(_run_listing(ffmpeg_path, "-formats"))
    return caps
