"""Validation for Advanced FFmpeg mode.

The user supplies only FFmpeg *arguments*; the application always controls the
input and output filenames. Anything that could add inputs, reach the network,
read arbitrary files, or escape the working directory is rejected.
"""
from __future__ import annotations

import shlex

# Tokens that are never allowed as standalone arguments.
FORBIDDEN_ARGS = {
    "-i", "-filter_complex_script", "-filter_script", "-f",  # -f could enable lavfi/pipe tricks
}

# Substrings that indicate network protocols, file indirection, or path escape.
FORBIDDEN_SUBSTRINGS = (
    "http://", "https://", "ftp://", "rtmp://", "rtsp://", "rtp://",
    "udp://", "tcp://", "srt://", "file:", "concat:", "subfile:",
    "pipe:", "tee:", "lavfi", "../", "/etc/", "/proc/", "/dev/",
)


class AdvancedArgsError(ValueError):
    pass


def validate_advanced_args(arg_string: str) -> list[str]:
    """Split and validate user-supplied FFmpeg arguments.

    Returns the argument list to splice between input and output.
    Raises AdvancedArgsError on anything forbidden.
    """
    if not arg_string or not arg_string.strip():
        raise AdvancedArgsError("No FFmpeg arguments provided.")
    try:
        args = shlex.split(arg_string)
    except ValueError as exc:
        raise AdvancedArgsError(f"Could not parse arguments: {exc}") from exc
    if not args:
        raise AdvancedArgsError("No FFmpeg arguments provided.")
    if len(args) > 200:
        raise AdvancedArgsError("Too many arguments (max 200).")

    for arg in args:
        low = arg.lower()
        if low in FORBIDDEN_ARGS:
            raise AdvancedArgsError(f"Argument not allowed: {arg}")
        if low.startswith("-i") and low != "-ignore_unknown":
            raise AdvancedArgsError("Adding inputs is not allowed in advanced mode.")
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in low:
                raise AdvancedArgsError(f"Forbidden pattern in arguments: {bad}")
        if arg.startswith("/"):
            raise AdvancedArgsError("Absolute paths are not allowed in arguments.")
    return args


def validate_output_extension(ext: str, allowed: set[str]) -> str:
    ext = (ext or "").strip().lower().lstrip(".")
    if not ext or not ext.isalnum() or len(ext) > 8:
        raise AdvancedArgsError("Invalid output extension.")
    if ext not in allowed:
        raise AdvancedArgsError(f"Extension '.{ext}' is not allowed.")
    return ext
