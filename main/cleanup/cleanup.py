"""Mounted-bucket maintenance utility: delete logically expired job prefixes.

This remains useful for manual/local maintenance. Production physical retention
is handled daily by ``main/cloud_cleanup/main.py`` without an HF Job.

Usage:
    python cleanup/cleanup.py --bucket /data/media-bucket
    python cleanup/cleanup.py --bucket /data/media-bucket --dry-run

Exit code 0 means the cleanup pass completed (individual failures are logged
and retried on the next run; cleanup is idempotent).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cleanup")

JOBS_DIR = "jobs"


def parse_prefix(name: str) -> int | None:
    """'<expires_unix>_<job_id>' -> expires_unix, or None if malformed."""
    expiry_str, sep, job_id = name.partition("_")
    if not sep or not expiry_str.isdigit() or not job_id:
        return None
    return int(expiry_str)


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def run_cleanup(bucket_root: Path, dry_run: bool = False, now: float | None = None) -> int:
    now = now if now is not None else time.time()
    jobs_root = bucket_root / JOBS_DIR
    if not jobs_root.exists():
        log.info("No jobs directory at %s; nothing to do.", jobs_root)
        return 0

    scanned = expired = failures = 0
    reclaimed = 0
    for child in sorted(jobs_root.iterdir()):
        if not child.is_dir():
            continue
        scanned += 1
        expiry = parse_prefix(child.name)
        if expiry is None:
            log.info("Skipping malformed prefix: %s", child.name)
            continue
        if expiry > now:
            continue
        expired += 1
        size = dir_size(child)
        if dry_run:
            log.info("[dry-run] would delete %s (%d bytes)", child.name, size)
            reclaimed += size
            continue
        try:
            shutil.rmtree(child)
            reclaimed += size
            log.info("Deleted expired job %s (%d bytes)", child.name, size)
        except Exception as exc:
            failures += 1
            log.error("Failed to delete %s: %s (will retry next run)", child.name, exc)

    log.info(
        "cleanup done: scanned=%d expired=%d reclaimed_bytes=%d failures=%d dry_run=%s",
        scanned, expired, reclaimed, failures, dry_run,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete expired media-toolbox jobs.")
    parser.add_argument("--bucket", default="/data/media-bucket",
                        help="Bucket mount path (default: /data/media-bucket)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted without deleting")
    args = parser.parse_args(argv)
    return run_cleanup(Path(args.bucket), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
