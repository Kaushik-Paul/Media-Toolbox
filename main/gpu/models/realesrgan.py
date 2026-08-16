"""AI upscaling with Real-ESRGAN (images and short videos).

The RRDBNet architecture is vendored here and official weights are downloaded
from the Real-ESRGAN release URLs, which avoids the fragile basicsr/torchvision
dependency chain on ZeroGPU. torch is imported lazily; the app stays usable
with ENABLE_REALESRGAN=false (PLAN.md rule 20).

Video flow (experimental, PLAN.md section 52): extract a chunk of frames ->
GPU upscale -> encode chunk -> delete temporary frames -> next chunk ->
concat -> mux original audio.
"""
from __future__ import annotations

import logging
import math
import shutil
import threading
import urllib.request
from pathlib import Path

from core.filenames import output_name

from gpu.backend.config import gpu
from gpu.backend.job_manager import JobContext, OperationError, OperationResult, ProducedOutput
from gpu.backend.postprocessing import concat_chunks, encode_chunk_video, mux_original_audio
from gpu.backend.preprocessing import extract_frames

log = logging.getLogger(__name__)

MODEL_LABELS = ("General", "Anime/illustration")
SCALES = (2, 4)
IMAGE_FORMATS = ("PNG", "WebP", "JPG")

# Official Real-ESRGAN weights (github.com/xinntao/Real-ESRGAN releases).
_WEIGHTS = {
    ("General", 4): (
        "RealESRGAN_x4plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        23, 4,
    ),
    ("General", 2): (
        "RealESRGAN_x2plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        23, 2,
    ),
    ("Anime/illustration", 4): (
        "RealESRGAN_x4plus_anime_6B.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        6, 4,
    ),
}
# Anime has no official x2 model: upscale x4 with the anime model, then
# Lanczos-downscale to x2 (standard practice for this model family).

CHUNK_SECONDS = 10.0
_TILE = 512
_TILE_PAD = 16

_model_cache: dict = {}
_model_lock = threading.Lock()
_CACHE_DIR = {"value": None}


def _rrdbnet_class():
    """Define RRDBNet lazily so importing this module never requires torch."""
    import torch
    from torch import nn
    import torch.nn.functional as F

    class ResidualDenseBlock(nn.Module):
        def __init__(self, num_feat: int = 64, num_grow_ch: int = 32):
            super().__init__()
            self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
            self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, num_feat: int, num_grow_ch: int = 32):
            super().__init__()
            self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

        def forward(self, x):
            out = self.rdb1(x)
            out = self.rdb2(out)
            out = self.rdb3(out)
            return out * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, num_in_ch=3, num_out_ch=3, scale=4, num_feat=64,
                     num_block=23, num_grow_ch=32):
            super().__init__()
            self.scale = scale
            self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
            self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            feat = self.conv_first(x)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            if self.scale == 4:
                feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet


def _weights_path(model_label: str, scale: int, cache_dir: Path) -> tuple[Path, int, int]:
    """Return (weights_path, num_block, model_scale), downloading if needed."""
    key = (model_label, scale)
    if key not in _WEIGHTS:
        # Anime x2 -> anime x4 weights (caller downscales the result).
        key = (model_label, 4)
    filename, url, num_block, model_scale = _WEIGHTS[key]
    dest = cache_dir / "realesrgan" / filename
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading Real-ESRGAN weights %s", filename)
        urllib.request.urlretrieve(url, str(dest))  # noqa: S310 - fixed official URL
    return dest, num_block, model_scale


def _get_model(model_label: str, scale: int):
    """Build (once per variant) the RRDBNet model in the main process.

    Must run OUTSIDE @spaces.GPU functions (ZeroGPU forks are throwaway);
    CUDA emulation makes `.to("cuda")` a no-op there and forks inherit the
    loaded model.
    """
    cache_key = (model_label, scale)
    with _model_lock:
        if cache_key in _model_cache:
            return _model_cache[cache_key]
        import torch

        if _CACHE_DIR["value"] is None:
            raise RuntimeError("model cache dir not initialized")
        weights, num_block, model_scale = _weights_path(model_label, scale, _CACHE_DIR["value"])
        RRDBNet = _rrdbnet_class()
        model = RRDBNet(scale=model_scale, num_block=num_block)
        state = torch.load(str(weights), map_location="cpu", weights_only=True)
        model.load_state_dict(state.get("params_ema") or state.get("params") or state, strict=True)
        model.eval()
        if torch.cuda.is_available():
            model = model.to("cuda").half()
        result = (model, model_scale)
        _model_cache[cache_key] = result
        return result


def _cached_model(model_label: str, scale: int):
    """Model lookup for GPU functions: inherited cache, local-dev fallback."""
    cached = _model_cache.get((model_label, scale))
    return cached if cached is not None else _get_model(model_label, scale)


def _to_tensor(img):
    import numpy as np
    import torch

    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)  # (1, 3, H, W)


def _to_image(tensor):
    import numpy as np
    from PIL import Image

    arr = tensor.squeeze(0).clamp(0, 1).cpu().numpy().transpose(1, 2, 0)
    return Image.fromarray((arr * 255.0).round().astype(np.uint8))


def _tiled_upscale(model, tensor, scale: int):
    """Upscale a (1, 3, H, W) tensor in tiles to bound VRAM usage."""
    import torch

    _, _, h, w = tensor.shape
    if max(h, w) <= _TILE:
        with torch.no_grad():
            return model(tensor)

    output = torch.zeros((1, 3, h * scale, w * scale), device=tensor.device, dtype=tensor.dtype)
    stride = _TILE - _TILE_PAD * 2
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            in_y0, in_x0 = max(0, y - _TILE_PAD), max(0, x - _TILE_PAD)
            in_y1, in_x1 = min(h, y + stride + _TILE_PAD), min(w, x + stride + _TILE_PAD)
            with torch.no_grad():
                patch_out = model(tensor[:, :, in_y0:in_y1, in_x0:in_x1])
            valid_h = min(stride, h - y)
            valid_w = min(stride, w - x)
            crop_y0, crop_x0 = (y - in_y0) * scale, (x - in_x0) * scale
            output[
                :, :,
                y * scale:(y + valid_h) * scale,
                x * scale:(x + valid_w) * scale,
            ] = patch_out[:, :, crop_y0:crop_y0 + valid_h * scale,
                          crop_x0:crop_x0 + valid_w * scale]
    return output


def _upscale_one(model, model_scale: int, img, out_scale: int):
    import torch
    from PIL import Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tensor = _to_tensor(img).to(device)
    if device == "cuda":
        tensor = tensor.half()
    out = _tiled_upscale(model, tensor, model_scale)
    result = _to_image(out.float())
    if out_scale != model_scale:
        result = result.resize(
            (img.width * out_scale, img.height * out_scale), Image.LANCZOS
        )
    return result


def estimate_image_duration(image_path: str, model_label: str = "General",
                            scale: int = 4, out_format: str = "PNG") -> int:
    return 60


def estimate_frames_duration(frame_paths: list, out_dir: str,
                             model_label: str = "General", scale: int = 4) -> int:
    return int(max(30, min(300, 15 + len(frame_paths) * 0.6)))


@gpu(duration=estimate_frames_duration)
def upscale_frames(frame_paths: list, out_dir: str, model_label: str = "General",
                   scale: int = 4) -> list:
    """Upscale a chunk of frame PNGs. Returns the output frame paths."""
    from PIL import Image

    model, model_scale = _cached_model(model_label, scale)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for frame in frame_paths:
        with Image.open(frame) as img:
            result = _upscale_one(model, model_scale, img.convert("RGB"), scale)
        dest = out / Path(frame).name
        result.save(dest, format="PNG")
        written.append(str(dest))
    return written


@gpu(duration=estimate_image_duration)
def upscale_image_file(image_path: str, out_path: str, model_label: str = "General",
                       scale: int = 4, out_format: str = "PNG") -> str:
    """Upscale a single image file."""
    from PIL import Image

    model, model_scale = _cached_model(model_label, scale)
    with Image.open(image_path) as img:
        result = _upscale_one(model, model_scale, img.convert("RGB"), scale)
    save_kwargs = {"format": "JPEG", "quality": 95} if out_format == "JPG" else {"format": out_format}
    result.save(out_path, **save_kwargs)
    return out_path


# -- job entry points ------------------------------------------------------------


def _validate_common(ctx: JobContext, params: dict) -> tuple[str, int]:
    if not ctx.gpu_settings.enable_realesrgan:
        raise OperationError("AI upscaling is disabled on this deployment (ENABLE_REALESRGAN=false).")
    model_label = str(params.get("model") or "General")
    if model_label not in MODEL_LABELS:
        raise OperationError(f"Unknown model: {model_label}")
    try:
        scale = int(params.get("scale") or 4)
    except (TypeError, ValueError):
        raise OperationError("Scale must be 2 or 4.") from None
    if scale not in SCALES:
        raise OperationError("Scale must be 2 or 4.")
    return model_label, scale


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    mode = str(params.get("mode") or "Image")
    if mode == "Short Video":
        return _run_video(ctx, inputs, params)
    return _run_image(ctx, inputs, params)


def _run_image(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    model_label, scale = _validate_common(ctx, params)
    out_format = str(params.get("format") or "PNG")
    if out_format not in IMAGE_FORMATS:
        raise OperationError(f"Unsupported image format: {out_format}")

    _CACHE_DIR["value"] = ctx.gpu_settings.model_cache_dir
    original = ctx.media_info.filename if ctx.media_info else inputs[0].name
    ext = "jpg" if out_format == "JPG" else out_format.lower()
    final = ctx.out_dir / output_name(original, f"upscaled-{scale}x", ext)

    # Load in the main process so the ZeroGPU fork inherits the model.
    _get_model(model_label, scale)
    ctx.report(10.0, "Upscaling on GPU (ZeroGPU quota is used)")
    upscale_image_file(str(inputs[0]), str(final), model_label, scale, out_format)
    if not final.exists() or final.stat().st_size == 0:
        raise OperationError("Upscaling produced no output.")

    ctx.report(96.0, "Storing results")
    return OperationResult(
        outputs=[ProducedOutput(final, final.name, "main")],
        parameters={"model": model_label, "scale": f"{scale}x", "format": out_format},
        summary={"Scale": f"{scale}x"},
    )


def _run_video(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    model_label, scale = _validate_common(ctx, params)
    info = ctx.media_info
    if info is None or not info.has_video:
        raise OperationError("Upload a video file for video upscaling.")
    g = ctx.gpu_settings
    duration = info.duration_seconds or 0.0
    if duration <= 0:
        raise OperationError("Could not determine the video duration.")
    if duration > g.gpu_video_max_duration:
        raise OperationError(
            f"Video is too long for AI upscaling ({duration:.0f}s). "
            f"Limit is {g.gpu_video_max_duration:g}s."
        )
    video = info.primary_video
    pixels = (video.width or 0) * (video.height or 0)
    if pixels > g.gpu_video_max_pixels:
        raise OperationError(
            f"Resolution {video.width}x{video.height} exceeds the upscale limit. "
            "Resize it first with the CPU Toolbox."
        )
    max_bytes = g.gpu_video_max_file_size_gb * (1024 ** 3)
    if info.size > max_bytes:
        raise OperationError(
            f"File is too large for AI upscaling. Limit is {g.gpu_video_max_file_size_gb:g} GB."
        )

    _CACHE_DIR["value"] = g.model_cache_dir
    # Load in the main process so ZeroGPU forks inherit the model.
    _get_model(model_label, scale)
    fps = (video.fps if video and video.fps else 25.0)
    source = inputs[0]
    chunks_dir = ctx.work_dir / "chunks"
    frames_dir = ctx.work_dir / "frames"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    total_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
    chunk_paths: list[Path] = []
    for index in range(total_chunks):
        ctx.check_cancelled()
        start = index * CHUNK_SECONDS
        span = min(CHUNK_SECONDS, duration - start)
        base = 10.0 + (index / total_chunks) * 75.0
        ctx.report(base, f"Chunk {index + 1}/{total_chunks}: extracting frames")
        in_frames = extract_frames(ctx, source, frames_dir / f"in_{index:04d}",
                                   start=start, duration=span)

        ctx.report(base + 2.0, f"Chunk {index + 1}/{total_chunks}: upscaling on GPU")
        out_frames_dir = frames_dir / f"out_{index:04d}"
        written = upscale_frames([str(p) for p in in_frames], str(out_frames_dir),
                                 model_label, scale)

        ctx.report(base + 6.0, f"Chunk {index + 1}/{total_chunks}: encoding")
        chunk_path = encode_chunk_video(ctx, out_frames_dir, len(written), fps,
                                        chunks_dir / f"chunk_{index:04d}.mp4")
        chunk_paths.append(chunk_path)
        shutil.rmtree(frames_dir / f"in_{index:04d}", ignore_errors=True)
        shutil.rmtree(out_frames_dir, ignore_errors=True)

    ctx.check_cancelled()
    ctx.report(87.0, "Joining chunks")
    joined = concat_chunks(ctx, chunk_paths, ctx.work_dir / "joined.mp4")

    original = info.filename
    final = ctx.out_dir / output_name(original, f"upscaled-{scale}x", "mp4")
    mux_original_audio(ctx, joined, source, final)

    ctx.report(96.0, "Storing results")
    return OperationResult(
        outputs=[ProducedOutput(final, final.name, "main")],
        parameters={"model": model_label, "scale": f"{scale}x", "experimental": True},
        summary={"Scale": f"{scale}x", "Chunks": total_chunks},
    )
