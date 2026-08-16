"""Expiry parsing and checks for bucket job prefixes."""
from __future__ import annotations

import time

_PREFIX_SEP = "_"


def parse_job_prefix(name: str) -> tuple[int, str] | None:
    """Parse '<expires_unix>_<job_id>' -> (expires_unix, job_id), or None if malformed."""
    if not name or _PREFIX_SEP not in name:
        return None
    expiry_str, _, job_id = name.partition(_PREFIX_SEP)
    if not expiry_str.isdigit() or not job_id:
        return None
    return int(expiry_str), job_id


def is_expired(expires_unix: int, now: float | None = None) -> bool:
    return (now if now is not None else time.time()) >= expires_unix


def seconds_until(expires_unix: int, now: float | None = None) -> float:
    return max(0.0, expires_unix - (now if now is not None else time.time()))
