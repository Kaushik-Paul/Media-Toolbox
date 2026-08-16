"""Time parsing, formatting, and expiry helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | float | int | None) -> float | None:
    """Parse 'HH:MM:SS.mmm', 'MM:SS', or plain seconds into float seconds.

    Returns None for empty/None input. Raises ValueError on invalid input.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Invalid time value: {value!r}")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError as exc:
        raise ValueError(f"Invalid time value: {value!r}") from exc
    return seconds


def format_hms(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_size(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def format_countdown(seconds: float) -> str:
    """Human countdown like '23h 59m' for expiry display."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return "<1m"


def expiry_times(retention_hours: int, now: datetime | None = None) -> tuple[datetime, datetime, int]:
    """Return (completed_at, expires_at, expires_unix)."""
    completed = now or utcnow()
    expires = completed + timedelta(hours=retention_hours)
    return completed, expires, int(expires.timestamp())
