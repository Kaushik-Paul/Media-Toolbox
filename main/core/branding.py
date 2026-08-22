"""Shared public branding used by the CPU and GPU web entrypoints."""
from __future__ import annotations

from pathlib import Path


FAVICON_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "kaushik-website-logo-favicon-96x96.png"
)
FAVICON_HEAD = (
    '<link rel="icon" type="image/png" sizes="96x96" href="/favicon.ico">'
    '<link rel="shortcut icon" type="image/png" href="/favicon.ico">'
)
