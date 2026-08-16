"""Safe FFmpeg command construction as argument arrays (never shell strings)."""
from __future__ import annotations

import shlex
from pathlib import Path


class FFmpegCommandBuilder:
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self._args: list[str] = [ffmpeg_path, "-hide_banner", "-y"]

    def add(self, *args: object) -> "FFmpegCommandBuilder":
        self._args.extend(str(a) for a in args)
        return self

    def input(self, path: str | Path, *pre_input_args: object) -> "FFmpegCommandBuilder":
        """Add an input, optionally with args placed before '-i' (e.g. -ss for fast seek)."""
        self._args.extend(str(a) for a in pre_input_args)
        self._args.extend(["-i", str(path)])
        return self

    def output(self, path: str | Path) -> "FFmpegCommandBuilder":
        self._args.append(str(path))
        return self

    def build(self) -> list[str]:
        return list(self._args)

    def preview(self) -> str:
        """Human-readable command for the UI. Execution still uses build()."""
        return shlex.join(self._args)
