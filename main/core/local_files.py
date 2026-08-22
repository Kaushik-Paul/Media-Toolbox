"""Efficient local staging helpers."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def stage_input(source: Path, destination: Path) -> None:
    """Stage an immutable input without duplicating bytes when possible.

    Gradio's upload directory and job work directory normally share a
    filesystem, so a hard link is instantaneous.  Cross-device and restricted
    filesystems safely fall back to a buffered copy.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)
