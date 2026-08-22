"""Centralized configuration. Environment variables override defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.0.0"


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_version: str = APP_VERSION
    source: str = "cpu"

    retention_hours: int = 24
    bucket_id: str = ""
    bucket_mount: Path = Path("/data/media-bucket")
    work_dir: Path = Path("/tmp/media-toolbox")

    max_concurrent_jobs: int = 1
    min_free_disk_gb: float = 2.0
    max_input_size_gb: float = 8.0

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    log_tail_lines: int = 40
    target_size_safety_factor: float = 0.97
    min_video_bitrate_kbps: int = 100
    gif_max_duration_seconds: float = 30.0

    port: int = 7860
    cancel_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            source=_env_str("APP_SOURCE", "cpu"),
            retention_hours=_env_int("RETENTION_HOURS", 24),
            bucket_id=_env_str("HF_BUCKET_ID", ""),
            bucket_mount=Path(_env_str("BUCKET_MOUNT", "/data/media-bucket")),
            work_dir=Path(_env_str("WORK_DIR", "/tmp/media-toolbox")),
            max_concurrent_jobs=_env_int("MAX_CONCURRENT_CPU_JOBS", 1),
            min_free_disk_gb=_env_float("MIN_FREE_DISK_GB", 2.0),
            max_input_size_gb=_env_float("MAX_INPUT_SIZE_GB", 8.0),
            ffmpeg_path=_env_str("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=_env_str("FFPROBE_PATH", "ffprobe"),
            log_tail_lines=_env_int("LOG_TAIL_LINES", 40),
            target_size_safety_factor=_env_float("TARGET_SIZE_SAFETY_FACTOR", 0.97),
            gif_max_duration_seconds=_env_float("GIF_MAX_DURATION_SECONDS", 30.0),
            port=_env_int("PORT", 7860),
            cancel_password=_env_str("TOOLBOX_PASSWORD", ""),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
