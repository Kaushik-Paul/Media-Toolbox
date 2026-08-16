"""Operation registry mapping operation names to implementations."""
from __future__ import annotations

from operations import (
    advanced,
    audio,
    compatibility,
    compress,
    concatenate,
    convert,
    crop,
    fps,
    gif,
    merge,
    metadata,
    resize,
    rotate,
    screenshot,
    speed,
    subtitles,
    target_size,
    trim,
)

# operation name -> module with run(ctx, inputs, params) -> OperationResult
OPERATIONS = {
    "compress_video": compress,
    "target_size": target_size,
    "resize_video": resize,
    "convert_format": convert,
    "trim": trim,
    "fps_convert": fps,
    "rotate_flip": rotate,
    "crop_video": crop,
    "change_speed": speed,
    "merge_av": merge,
    "concatenate": concatenate,
    "video_to_gif": gif,
    "screenshot": screenshot,
    "remove_audio": audio,
    "extract_audio": audio,
    "convert_audio": audio,
    "compress_audio": audio,
    "audio_sample_rate": audio,
    "audio_channels": audio,
    "audio_normalize": audio,
    "audio_trim": audio,
    "audio_speed": audio,
    "subtitles_extract": subtitles,
    "subtitles_add": subtitles,
    "subtitles_burn": subtitles,
    "make_compatible": compatibility,
    "optimize_streaming": compatibility,
    "remove_metadata": metadata,
    "advanced_ffmpeg": advanced,
}

# Operations handled by a shared module dispatch on this key.
DISPATCH_KEY = "mode"
