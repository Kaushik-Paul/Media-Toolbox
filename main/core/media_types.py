"""Media type detection from filenames, extensions, and probe data."""
from __future__ import annotations

from enum import Enum

VIDEO_EXTS = {"mp4", "mkv", "mov", "webm", "avi", "m4v", "ts", "flv", "wmv", "mpg", "mpeg", "3gp"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "opus", "ogg", "flac", "wav", "wma", "aiff", "mka"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "tiff", "gif"}
SUBTITLE_EXTS = {"srt", "ass", "ssa", "vtt", "sub"}

AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "wav": "audio/wav",
}
VIDEO_MIME = {"mp4": "video/mp4", "mkv": "video/x-matroska", "mov": "video/quicktime", "webm": "video/webm"}
IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
SUBTITLE_MIME = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa", "txt": "text/plain", "json": "application/json", "zip": "application/zip"}


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    SUBTITLE = "subtitle"
    OTHER = "other"


def ext_of(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return ext.lower() if dot else ""


def kind_from_extension(filename: str) -> MediaKind:
    ext = ext_of(filename)
    if ext in VIDEO_EXTS:
        return MediaKind.VIDEO
    if ext in AUDIO_EXTS:
        return MediaKind.AUDIO
    if ext in IMAGE_EXTS:
        return MediaKind.IMAGE
    if ext in SUBTITLE_EXTS:
        return MediaKind.SUBTITLE
    return MediaKind.OTHER


def mime_for(filename: str) -> str:
    ext = ext_of(filename)
    for table in (VIDEO_MIME, AUDIO_MIME, IMAGE_MIME, SUBTITLE_MIME):
        if ext in table:
            return table[ext]
    return "application/octet-stream"


def is_probed_media(filename: str) -> bool:
    """Whether an output file should be validated with ffprobe."""
    return kind_from_extension(filename) in (MediaKind.VIDEO, MediaKind.AUDIO, MediaKind.IMAGE)
