"""Process-wide singletons for the GPU Space (settings, storage, jobs)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from backend.probe import FFprobeService
from core.config import Settings, get_settings
from core.storage.bucket import BucketStorage

from gpu.backend.config import GpuSettings, get_gpu_settings, on_zerogpu
from gpu.backend.job_manager import JobManager

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    gpu_settings: GpuSettings
    storage: BucketStorage
    probe: FFprobeService
    jobs: JobManager
    dev_bucket: bool


_services: Services | None = None


def init_services() -> Services:
    global _services
    if _services is not None:
        return _services

    settings = get_settings()
    gpu_settings = get_gpu_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    gpu_settings.model_cache_dir.mkdir(parents=True, exist_ok=True)

    # Keep model caches inside the configured cache dir unless the environment
    # (e.g. the Space itself) already provides them.
    os.environ.setdefault("TORCH_HOME", str(gpu_settings.model_cache_dir / "torch"))
    os.environ.setdefault("HF_HOME", str(gpu_settings.model_cache_dir / "hf"))

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
    jobs = JobManager(settings, gpu_settings, storage, probe)

    _services = Services(settings, gpu_settings, storage, probe, jobs, dev_bucket)
    _log_startup(_services)
    return _services


def get_services() -> Services:
    if _services is None:
        return init_services()
    return _services


def _log_startup(s: Services) -> None:
    g = s.gpu_settings
    lines = [
        f"AI Media Toolbox v{s.settings.app_version}",
        "",
        "ZeroGPU mode detected" if on_zerogpu() else "ZeroGPU not detected (local/no-op GPU)",
        "",
        f"Whisper     enabled ({g.whisper_model})",
        f"Demucs      {'enabled' if g.enable_demucs else 'DISABLED'} ({g.demucs_model})",
        f"Real-ESRGAN {'enabled' if g.enable_realesrgan else 'DISABLED'}",
        "",
        f"Bucket      {'connected' if s.storage.check_writable() else 'UNAVAILABLE'}"
        + (" (local dev fallback)" if s.dev_bucket else ""),
        f"Retention   {s.settings.retention_hours} hours",
    ]
    log.info("\n".join(lines))
