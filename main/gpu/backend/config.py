"""GPU-specific configuration and the ZeroGPU decorator.

Shared settings (retention, bucket mount, work dir, ffmpeg paths) come from
``core.config.get_settings`` in ``main/``; this module adds only the GPU-side
knobs from PLAN.md section 74. Environment variables override defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class GpuSettings:
    whisper_model: str = "openai/whisper-large-v3-turbo"
    demucs_model: str = "htdemucs"
    enable_demucs: bool = True
    enable_realesrgan: bool = True

    # Conservative V1 limits for video upscaling (PLAN.md section 52).
    gpu_video_max_duration: float = 120.0  # seconds
    gpu_video_max_pixels: int = 1920 * 1080
    gpu_video_max_file_size_gb: float = 1.0

    # Optional link shown in the header back to the CPU Space.
    cpu_space_url: str = ""

    # Model weight caches default under the (ephemeral) work dir.
    model_cache_dir: Path = Path("/tmp/media-toolbox/models")

    @classmethod
    def from_env(cls) -> "GpuSettings":
        work_dir = Path(_env_str("WORK_DIR", "/tmp/media-toolbox"))
        return cls(
            whisper_model=_env_str("WHISPER_MODEL", "openai/whisper-large-v3-turbo"),
            demucs_model=_env_str("DEMUCS_MODEL", "htdemucs"),
            enable_demucs=_env_bool("ENABLE_DEMUCS", True),
            enable_realesrgan=_env_bool("ENABLE_REALESRGAN", True),
            gpu_video_max_duration=_env_float("GPU_VIDEO_MAX_DURATION", 120.0),
            gpu_video_max_pixels=_env_int("GPU_VIDEO_MAX_PIXELS", 1920 * 1080),
            gpu_video_max_file_size_gb=_env_float("GPU_VIDEO_MAX_FILE_SIZE_GB", 1.0),
            cpu_space_url=_env_str("CPU_SPACE_URL", ""),
            model_cache_dir=Path(_env_str("MODEL_CACHE_DIR", str(work_dir / "models"))),
        )


_gpu_settings: GpuSettings | None = None


def get_gpu_settings() -> GpuSettings:
    global _gpu_settings
    if _gpu_settings is None:
        _gpu_settings = GpuSettings.from_env()
    return _gpu_settings


# -- ZeroGPU decorator ---------------------------------------------------------
#
# `spaces` is only meaningful on Hugging Face ZeroGPU hardware. Everywhere else
# (local dev, import checks) fall back to a no-op decorator with the same
# signature so the code paths stay identical.

try:  # pragma: no cover - environment dependent
    import spaces as _spaces
except Exception:  # noqa: BLE001 - any import failure means "not on ZeroGPU"
    _spaces = None


def gpu(duration=None):
    """@spaces.GPU with a local no-op fallback.

    ``duration`` may be a fixed number of seconds or a callable taking the same
    arguments as the decorated function (dynamic duration, PLAN.md section 53).
    """
    if _spaces is not None:
        return _spaces.GPU(duration=duration)

    def _decorate(fn):
        return fn

    return _decorate


def on_zerogpu() -> bool:
    return _spaces is not None and os.environ.get("SPACE_ID") is not None


def report_zerogpu_startup() -> bool:
    """Report registered GPU functions when Gradio is mounted under FastAPI.

    ZeroGPU normally performs this step from its ``Blocks.launch`` patch. This
    application uses ``mount_gradio_app`` and Uvicorn instead, so it must invoke
    the equivalent startup hook after all decorated model modules are imported.
    Returns whether the hook was available and called.
    """
    if not on_zerogpu():
        return False

    try:
        from spaces import zero as spaces_zero
    except (ImportError, AttributeError):
        return False

    startup = getattr(spaces_zero, "startup", None)
    if not callable(startup):
        return False
    startup()
    return True
