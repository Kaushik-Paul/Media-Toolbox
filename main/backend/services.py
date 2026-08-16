"""Process-wide singletons shared by the FastAPI routes and the Gradio UI."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.capabilities import FFmpegCapabilities, detect_capabilities
from backend.job_manager import JobManager
from backend.probe import FFprobeService
from core.config import Settings, get_settings
from core.storage.bucket import BucketStorage

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    storage: BucketStorage
    probe: FFprobeService
    capabilities: FFmpegCapabilities
    jobs: JobManager
    dev_bucket: bool


_services: Services | None = None


def init_services() -> Services:
    global _services
    if _services is not None:
        return _services

    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)

    bucket_root = settings.bucket_mount
    dev_bucket = False
    try:
        bucket_root.mkdir(parents=True, exist_ok=True)
        probe_file = bucket_root / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
    except Exception:
        bucket_root = settings.work_dir / "bucket"
        bucket_root.mkdir(parents=True, exist_ok=True)
        dev_bucket = True
        log.warning(
            "Bucket mount %s unavailable; using local dev bucket at %s",
            settings.bucket_mount, bucket_root,
        )

    storage = BucketStorage(bucket_root)
    probe = FFprobeService(settings.ffprobe_path)
    capabilities = detect_capabilities(settings.ffmpeg_path)
    jobs = JobManager(settings, storage, probe, capabilities)

    _services = Services(settings, storage, probe, capabilities, jobs, dev_bucket)
    _log_startup(_services)
    return _services


def get_services() -> Services:
    if _services is None:
        return init_services()
    return _services


def _log_startup(s: Services) -> None:
    summary = s.capabilities.summary()
    lines = [
        f"Media Toolbox v{s.settings.app_version}",
        "",
        f"FFmpeg: {s.capabilities.version}",
        "FFprobe: OK",
        "",
        "Encoders:",
        f"  H264      {'yes' if summary['libx264'] else 'no'}",
        f"  H265      {'yes' if summary['libx265'] else 'no'}",
        f"  AV1       {'yes' if summary['libsvtav1'] else 'no'}",
        f"  AAC       {'yes' if summary['aac'] else 'no'}",
        f"  Opus      {'yes' if summary['libopus'] else 'no'}",
        "",
        f"Bucket: {'connected' if s.storage.check_writable() else 'UNAVAILABLE'}"
        + (" (local dev fallback)" if s.dev_bucket else ""),
        "",
        f"Retention: {s.settings.retention_hours} hours",
        f"Max concurrent jobs: {s.settings.max_concurrent_jobs}",
    ]
    log.info("\n".join(lines))
