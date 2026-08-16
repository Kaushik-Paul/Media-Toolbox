"""Build and serialize job manifests."""
from __future__ import annotations

import json
from pathlib import Path

from core.models import JobManifest, MediaInfo, OutputFile
from core.time_utils import iso_z

MANIFEST_NAME = "manifest.json"


def build_manifest(
    *,
    job_id: str,
    source: str,
    operation: str,
    original_filename: str,
    original_size: int,
    created_at,
    completed_at,
    expires_at,
    expires_unix: int,
    outputs: list[OutputFile],
    parameters: dict,
    media_info: MediaInfo | None,
    app_version: str,
) -> JobManifest:
    return JobManifest(
        job_id=job_id,
        source=source,
        operation=operation,
        original_filename=original_filename,
        original_size=original_size,
        created_at=iso_z(created_at),
        completed_at=iso_z(completed_at),
        expires_at=iso_z(expires_at),
        expires_unix=expires_unix,
        outputs=outputs,
        parameters=parameters,
        media_info=media_info.summary_dict() if media_info else {},
        app_version=app_version,
    )


def write_manifest(directory: Path, manifest: JobManifest) -> Path:
    path = directory / MANIFEST_NAME
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_manifest(path: Path) -> JobManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return JobManifest.model_validate(data)
