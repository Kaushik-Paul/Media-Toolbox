"""Filename sanitization and output naming."""
from __future__ import annotations

import re
import uuid

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")
_MAX_LEN = 120


def sanitize_filename(name: str | None, fallback: str = "media") -> str:
    """Strip directories and unsafe characters from a user-provided filename."""
    if not name:
        return fallback
    name = name.replace("\\", "/").split("/")[-1]
    name = _SAFE_CHARS.sub("_", name).strip(" .")
    if not name or name in (".", ".."):
        return fallback
    if len(name) > _MAX_LEN:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[: _MAX_LEN - len(ext) - 1] + "." + ext
        else:
            name = name[:_MAX_LEN]
    return name


def stem_of(filename: str) -> str:
    stem, dot, _ = filename.rpartition(".")
    return stem if dot else filename


def output_name(original: str, suffix: str, ext: str) -> str:
    """e.g. ('vacation.mov', 'compressed', 'mp4') -> 'vacation-compressed.mp4'"""
    base = stem_of(sanitize_filename(original))
    ext = ext.lstrip(".")
    return f"{base}-{suffix}.{ext}" if suffix else f"{base}.{ext}"


def new_job_id() -> str:
    return uuid.uuid4().hex


def job_prefix(expires_unix: int, job_id: str) -> str:
    """Bucket directory name: '<expires_unix>_<job_id>'."""
    return f"{expires_unix}_{job_id}"
