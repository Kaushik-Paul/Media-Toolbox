"""Daily Google Cloud cleanup for the private Media Toolbox HF bucket."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import functions_framework
from huggingface_hub import batch_bucket_files, list_bucket_tree


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("media_toolbox_cleanup")
DELETE_BATCH_SIZE = 500


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _chunks(items: list[str], size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def cleanup_bucket(*, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Delete complete job folders whose bucket age exceeds RETENTION_DAYS.

    Hugging Face performs each delete server-side, so media bytes never travel
    through the Cloud Run function. Malformed and non-job folders are skipped.
    """
    token = os.getenv("HF_TOKEN", "").strip()
    bucket_id = os.getenv("HF_BUCKET_ID", "kaushikpaul/media-toolbox").strip()
    retention_days = _positive_int("RETENTION_DAYS", 30)
    if not token:
        raise RuntimeError("HF_TOKEN is not configured")
    if not bucket_id or "/" not in bucket_id:
        raise RuntimeError("HF_BUCKET_ID must be in owner/name form")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(days=retention_days)

    scanned = expired = deleted_files = failures = 0
    for item in list_bucket_tree(
        bucket_id,
        prefix="jobs",
        recursive=False,
        token=token,
    ):
        if getattr(item, "type", "") != "directory":
            continue
        scanned += 1
        path = str(item.path).rstrip("/")
        name = PurePosixPath(path).name
        expiry_text, separator, job_id = name.partition("_")
        if not separator or not expiry_text.isdigit() or not job_id:
            log.warning("Skipping malformed job prefix: %s", path)
            continue

        uploaded_at = item.uploaded_at
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
        if uploaded_at > cutoff:
            continue

        expired += 1
        try:
            files = [
                str(child.path)
                for child in list_bucket_tree(
                    bucket_id,
                    prefix=path,
                    recursive=True,
                    token=token,
                )
                if getattr(child, "type", "") == "file"
            ]
            if dry_run:
                log.info("[dry-run] would delete %s (%d files)", path, len(files))
                continue
            for batch in _chunks(files, DELETE_BATCH_SIZE):
                batch_bucket_files(bucket_id, delete=batch, token=token)
                deleted_files += len(batch)
            log.info("Deleted %s (%d files)", path, len(files))
        except Exception:  # noqa: BLE001 - continue so other prefixes are cleaned
            failures += 1
            log.exception("Failed to delete %s; a later daily run will retry it", path)

    result = {
        "bucket": bucket_id,
        "retention_days": retention_days,
        "dry_run": dry_run,
        "scanned": scanned,
        "expired": expired,
        "deleted_files": deleted_files,
        "failures": failures,
        "cutoff": cutoff.isoformat(),
    }
    log.info("Cleanup complete: %s", result)
    if failures:
        raise RuntimeError(f"Cleanup completed with {failures} failed job prefix(es)")
    return result


@functions_framework.http
def cleanup_media_bucket(request):
    """Authenticated HTTP entry point invoked once daily by Cloud Scheduler."""
    dry_run = _truthy(request.args.get("dry_run"))
    return cleanup_bucket(dry_run=dry_run), 200
