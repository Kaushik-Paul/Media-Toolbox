"""Job lifecycle: queueing, execution, verification, persistence, cancellation."""
from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import timezone, datetime
from enum import Enum
from pathlib import Path

from backend.capabilities import FFmpegCapabilities
from backend.ffmpeg_runner import FFmpegCancelled, FFmpegError, FFmpegRunner, ProgressUpdate
from backend.probe import FFprobeService
from core.config import Settings
from core.activity import ActivityBusyError, ActivityCoordinator, get_activity
from core.filenames import new_job_id, sanitize_filename
from core.local_files import stage_input
from core.manifests import build_manifest
from core.media_types import mime_for
from core.models import MediaInfo, OutputFile
from core.storage.bucket import BucketStorage
from core.time_utils import expiry_times, utcnow
from operations import OPERATIONS
from operations.base import JobContext, OperationError, OperationResult

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobResult:
    prefix: str
    expires_unix: int
    outputs: list[OutputFile]
    output_paths: list[Path]
    input_size: int
    output_size: int
    processing_seconds: float
    command_previews: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


@dataclass
class JobState:
    job_id: str
    operation: str
    status: JobStatus = JobStatus.QUEUED
    percent: float = 0.0
    status_text: str = "Queued"
    error: str = ""
    error_details: str = ""
    original_filename: str = ""
    input_size: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result: JobResult | None = None


@dataclass
class Metrics:
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_cancelled: int = 0
    processing_seconds: float = 0.0
    input_bytes: int = 0
    output_bytes: int = 0
    bucket_uploads: int = 0
    bucket_upload_failures: int = 0

    def log_line(self) -> str:
        return (
            f"completed={self.jobs_completed} failed={self.jobs_failed} "
            f"cancelled={self.jobs_cancelled} cpu_seconds={self.processing_seconds:.1f} "
            f"in_bytes={self.input_bytes} out_bytes={self.output_bytes} "
            f"uploads={self.bucket_uploads} upload_failures={self.bucket_upload_failures}"
        )


class JobManager:
    def __init__(
        self,
        settings: Settings,
        storage: BucketStorage,
        probe: FFprobeService,
        capabilities: FFmpegCapabilities,
        activity: ActivityCoordinator | None = None,
    ):
        self.settings = settings
        self.storage = storage
        self.probe = probe
        self.capabilities = capabilities
        self.runner = FFmpegRunner(log_tail_lines=settings.log_tail_lines)
        self.activity = activity or get_activity()
        self._semaphore = threading.Semaphore(settings.max_concurrent_jobs)
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self.metrics = Metrics()

    # -- public API ------------------------------------------------------------

    def submit(
        self,
        operation: str,
        input_paths: list[Path],
        original_filename: str,
        params: dict,
    ) -> str:
        if operation not in OPERATIONS:
            raise OperationError(f"Unknown operation: {operation}")
        if not input_paths:
            raise OperationError("No input file provided.")
        total_input = sum(p.stat().st_size for p in input_paths if p.exists())
        max_bytes = self.settings.max_input_size_gb * (1024 ** 3)
        if total_input > max_bytes:
            raise OperationError(
                f"Input is too large ({total_input / (1024 ** 3):.1f} GB). "
                f"Limit is {self.settings.max_input_size_gb:g} GB."
            )
        job_id = new_job_id()
        state = JobState(
            job_id=job_id,
            operation=operation,
            original_filename=sanitize_filename(original_filename),
            input_size=total_input,
        )
        try:
            lease = self.activity.begin("job", f"{operation.replace('_', ' ')} job")
        except ActivityBusyError as exc:
            raise OperationError(str(exc)) from exc
        self.activity.set_cancel_callback(lease.token, state.cancel_event.set)
        with self._lock:
            self._jobs[job_id] = state
        try:
            thread = threading.Thread(
                target=self._run,
                args=(state, [Path(p) for p in input_paths], params, lease.token),
                daemon=True,
            )
            thread.start()
        except Exception:
            self.activity.finish(lease.token)
            raise
        return job_id

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        state = self.get(job_id)
        if state is None or state.status in (
            JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED
        ):
            return False
        state.cancel_event.set()
        return True

    # -- internals ---------------------------------------------------------------

    def _set(self, state: JobState, status: JobStatus, text: str = "") -> None:
        state.status = status
        if text:
            state.status_text = text

    def _check_disk(self, input_size: int) -> None:
        free = shutil.disk_usage(self.settings.work_dir.parent if not self.settings.work_dir.exists()
                                 else self.settings.work_dir).free
        required = self.settings.min_free_disk_gb * (1024 ** 3) + input_size * 2
        if free < required:
            raise OperationError(
                "Not enough free disk space to run this job safely.",
                f"free={free / (1024 ** 3):.1f}GB required~={required / (1024 ** 3):.1f}GB",
            )

    def _run(
        self, state: JobState, input_paths: list[Path], params: dict, activity_token: str
    ) -> None:
        job_id = state.job_id
        work_dir = self.settings.work_dir / job_id
        try:
            with self._semaphore:
                if state.cancel_event.is_set():
                    raise FFmpegCancelled()
                self._set(state, JobStatus.RUNNING, "Preparing")
                state.started_at = time.time()
                self._check_disk(state.input_size)

                work_dir.mkdir(parents=True, exist_ok=True)
                local_inputs: list[Path] = []
                for i, src in enumerate(input_paths):
                    dest = work_dir / f"input_{i}{src.suffix.lower()}"
                    stage_input(src, dest)
                    local_inputs.append(dest)

                try:
                    media_info = self.probe.probe(local_inputs[0])
                    # Outputs are named after the user's file, not the work-dir copy.
                    media_info.filename = state.original_filename
                except Exception as exc:
                    raise OperationError(
                        "Could not analyze the input file.",
                        f"ffprobe: {exc}",
                    ) from exc

                def on_progress(update: ProgressUpdate) -> None:
                    state.percent = min(99.0, update.percent)
                    speed = f"{update.speed:.2f}x" if update.speed else "-"
                    state.status_text = (
                        f"Processing... {update.processed_seconds:.1f}s media, speed {speed}"
                    )

                ctx = JobContext(
                    job_id=job_id,
                    work_dir=work_dir,
                    settings=self.settings,
                    probe=self.probe,
                    runner=self.runner,
                    capabilities=self.capabilities,
                    cancel_event=state.cancel_event,
                    on_progress=on_progress,
                    media_info=media_info,
                )

                log.info(
                    "job=%s source=%s operation=%s stage=start",
                    job_id[:8], self.settings.source, state.operation,
                )
                result: OperationResult = OPERATIONS[state.operation].run(ctx, local_inputs, params)

                self._set(state, JobStatus.FINALIZING, "Verifying output")
                state.percent = 99.0
                job_result = self._persist(state, ctx, media_info, result)
                state.result = job_result
                state.percent = 100.0
                state.finished_at = time.time()
                self._set(state, JobStatus.COMPLETED, "Complete")
                self.metrics.jobs_completed += 1
                self.metrics.processing_seconds += job_result.processing_seconds
                self.metrics.input_bytes += state.input_size
                self.metrics.output_bytes += job_result.output_size
                self.metrics.bucket_uploads += 1
                log.info(
                    "job=%s source=%s operation=%s stage=done elapsed=%.1f status=success",
                    job_id[:8], self.settings.source, state.operation,
                    job_result.processing_seconds,
                )
        except FFmpegCancelled:
            state.finished_at = time.time()
            self._set(state, JobStatus.CANCELLED, "Cancelled")
            self.metrics.jobs_cancelled += 1
            log.info("job=%s operation=%s status=cancelled", job_id[:8], state.operation)
        except OperationError as exc:
            state.finished_at = time.time()
            state.error = str(exc)
            state.error_details = exc.details
            self._set(state, JobStatus.FAILED, "Failed")
            self.metrics.jobs_failed += 1
            log.warning("job=%s operation=%s status=failed error=%s", job_id[:8], state.operation, exc)
        except FFmpegError as exc:
            state.finished_at = time.time()
            state.error = "FFmpeg failed to process this file."
            state.error_details = "\n".join(exc.log_tail[-15:]) or str(exc)
            self._set(state, JobStatus.FAILED, "Failed")
            self.metrics.jobs_failed += 1
            log.warning("job=%s operation=%s status=failed rc=%s", job_id[:8], state.operation, exc.returncode)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            state.finished_at = time.time()
            state.error = "Unexpected internal error."
            state.error_details = str(exc)
            self._set(state, JobStatus.FAILED, "Failed")
            self.metrics.jobs_failed += 1
            log.exception("job=%s operation=%s status=failed unexpected", job_id[:8], state.operation)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            self.activity.finish(activity_token)

    def _persist(
        self,
        state: JobState,
        ctx: JobContext,
        media_info: MediaInfo,
        result: OperationResult,
    ) -> JobResult:
        completed, expires, expires_unix = expiry_times(self.settings.retention_hours)
        outputs: list[OutputFile] = []
        move_list: list[tuple[Path, str]] = []
        for produced in result.outputs:
            path = produced.local_path
            if not path.exists() or path.stat().st_size == 0:
                raise OperationError("Internal error: missing produced output.", produced.filename)
            outputs.append(
                OutputFile(
                    id=produced.output_id,
                    filename=produced.filename,
                    mime_type=mime_for(produced.filename),
                    size=path.stat().st_size,
                )
            )
            move_list.append((path, produced.filename))

        manifest = build_manifest(
            job_id=state.job_id,
            source=self.settings.source,
            operation=state.operation,
            original_filename=state.original_filename,
            original_size=state.input_size,
            created_at=datetime.fromtimestamp(state.created_at, tz=timezone.utc),
            completed_at=completed,
            expires_at=expires,
            expires_unix=expires_unix,
            outputs=outputs,
            parameters=result.parameters,
            media_info=media_info,
            app_version=self.settings.app_version,
        )
        try:
            dest = self.storage.save_job(manifest, move_list)
        except Exception as exc:
            self.metrics.bucket_upload_failures += 1
            raise OperationError("Failed to store the result.", str(exc)) from exc

        return JobResult(
            prefix=dest.name,
            expires_unix=expires_unix,
            outputs=outputs,
            output_paths=[dest / o.filename for o in outputs],
            input_size=state.input_size,
            output_size=sum(o.size for o in outputs),
            processing_seconds=state.finished_at - state.started_at if state.finished_at else time.time() - state.started_at,
            command_previews=result.command_previews,
            summary=result.summary,
        )
