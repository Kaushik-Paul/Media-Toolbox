"""FastAPI routes: health, capabilities, job listing, download, delete."""
from __future__ import annotations

import subprocess

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.services import get_services
from core.storage.bucket import JobExpiredError, JobNotFoundError
from core.storage.retention import seconds_until
from core.time_utils import format_countdown

router = APIRouter()
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


@router.get("/_health")
def health() -> dict:
    s = get_services()
    def _ok(binary: str) -> bool:
        try:
            subprocess.run([binary, "-version"], capture_output=True, timeout=10, shell=False)
            return True
        except Exception:
            return False

    return {
        "status": "ok",
        "ffmpeg": _ok(s.settings.ffmpeg_path),
        "ffprobe": _ok(s.settings.ffprobe_path),
        "bucket": s.storage.check_writable(),
        "dev_bucket_fallback": s.dev_bucket,
    }


@router.get("/api/capabilities")
def capabilities() -> dict:
    s = get_services()
    return {
        "ffmpeg_version": s.capabilities.version,
        **s.capabilities.summary(),
    }


@router.get("/api/jobs")
def list_jobs() -> dict:
    s = get_services()
    items = []
    for prefix, manifest in s.storage.list_jobs():
        items.append({
            "prefix": prefix,
            "job_id": manifest.job_id,
            "source": manifest.source,
            "operation": manifest.operation,
            "original_filename": manifest.original_filename,
            "completed_at": manifest.completed_at,
            "expires_at": manifest.expires_at,
            "expires_in": format_countdown(seconds_until(manifest.expires_unix)),
            "outputs": [o.model_dump() for o in manifest.outputs],
        })
    return {"jobs": items}


@router.get("/api/jobs/{prefix}")
def job_detail(prefix: str) -> dict:
    s = get_services()
    try:
        manifest = s.storage.get_manifest(prefix)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    return manifest.model_dump()


@router.get("/api/jobs/{prefix}/download/{file_id}")
def download(prefix: str, file_id: str) -> FileResponse:
    s = get_services()
    try:
        path, manifest = s.storage.resolve_output(prefix, file_id)
    except JobExpiredError:
        raise HTTPException(status_code=410, detail="This job has expired.")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    out = next(o for o in manifest.outputs if o.id == file_id)
    response = FileResponse(path, filename=out.filename, media_type=out.mime_type)
    # Starlette defaults to 64 KiB chunks. Larger sequential reads reduce
    # Python/ASGI and mounted-bucket overhead for multi-gigabyte downloads.
    response.chunk_size = DOWNLOAD_CHUNK_SIZE
    return response


@router.delete("/api/jobs/{prefix}")
@router.post("/api/jobs/{prefix}/delete")
def delete_job(prefix: str) -> dict:
    s = get_services()
    if not s.storage.delete_job(prefix):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"deleted": prefix}
