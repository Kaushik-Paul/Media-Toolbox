"""Filesystem-backed bucket storage.

On Hugging Face Spaces the private Storage Bucket is mounted as a read/write
volume (default /data/media-bucket). Locally, any directory can be used, which
keeps development and testing simple while exercising identical code paths.

Layout:
    <bucket_root>/jobs/<expires_unix>_<job_id>/manifest.json
    <bucket_root>/jobs/<expires_unix>_<job_id>/<output files>
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from core.filenames import job_prefix as make_prefix
from core.manifests import MANIFEST_NAME, read_manifest, write_manifest
from core.models import JobManifest
from core.storage.retention import is_expired, parse_job_prefix

log = logging.getLogger(__name__)

JOBS_DIR = "jobs"


class JobNotFoundError(Exception):
    pass


class JobExpiredError(Exception):
    pass


class BucketStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_root = self.root / JOBS_DIR
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    # -- write ---------------------------------------------------------------

    def save_job(self, manifest: JobManifest, output_files: list[tuple[Path, str]]) -> Path:
        """Move verified local outputs into the bucket and write the manifest.

        output_files: list of (local_path, bucket_filename).
        """
        prefix = make_prefix(manifest.expires_unix, manifest.job_id)
        dest = self.jobs_root / prefix
        dest.mkdir(parents=True, exist_ok=False)
        try:
            for local_path, bucket_name in output_files:
                shutil.move(str(local_path), str(dest / bucket_name))
            write_manifest(dest, manifest)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return dest

    # -- read ----------------------------------------------------------------

    def iter_prefixes(self) -> list[str]:
        if not self.jobs_root.exists():
            return []
        return sorted(
            (p.name for p in self.jobs_root.iterdir() if p.is_dir()),
            reverse=True,  # newest expiry first as a cheap default ordering
        )

    def _manifest_path(self, prefix: str) -> Path:
        if parse_job_prefix(prefix) is None:
            raise JobNotFoundError(f"Malformed job prefix: {prefix!r}")
        path = self.jobs_root / prefix / MANIFEST_NAME
        if not path.exists():
            raise JobNotFoundError(prefix)
        return path

    def get_manifest(self, prefix: str) -> JobManifest:
        try:
            return read_manifest(self._manifest_path(prefix))
        except JobNotFoundError:
            raise
        except Exception as exc:
            raise JobNotFoundError(f"Unreadable manifest for {prefix}: {exc}") from exc

    def list_jobs(self, include_expired: bool = False, now: float | None = None) -> list[tuple[str, JobManifest]]:
        """Return (prefix, manifest) pairs, newest completed first, hiding expired jobs."""
        results: list[tuple[str, JobManifest]] = []
        for prefix in self.iter_prefixes():
            parsed = parse_job_prefix(prefix)
            if parsed is None:
                continue
            expires_unix, _ = parsed
            if not include_expired and is_expired(expires_unix, now):
                continue
            try:
                results.append((prefix, self.get_manifest(prefix)))
            except JobNotFoundError:
                continue
        results.sort(key=lambda item: item[1].completed_at, reverse=True)
        return results

    def resolve_output(self, prefix: str, file_id: str, now: float | None = None) -> tuple[Path, JobManifest]:
        """Resolve an output file while enforcing its manifest expiry."""
        manifest = self.get_manifest(prefix)
        if is_expired(manifest.expires_unix, now):
            raise JobExpiredError(prefix)
        match = next((o for o in manifest.outputs if o.id == file_id), None)
        if match is None:
            raise JobNotFoundError(f"{prefix}/{file_id}")
        path = self.jobs_root / prefix / match.filename
        if not path.exists():
            raise JobNotFoundError(f"{prefix}/{file_id} (file missing)")
        return path, manifest

    # -- delete ---------------------------------------------------------------

    def delete_job(self, prefix: str) -> bool:
        if parse_job_prefix(prefix) is None:
            return False
        target = self.jobs_root / prefix
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return not target.exists()

    # -- health ----------------------------------------------------------------

    def check_writable(self) -> bool:
        try:
            probe = self.root / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except Exception:
            return False
