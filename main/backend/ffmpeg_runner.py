"""FFmpeg subprocess runner with machine-readable progress and cancellation."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)


class FFmpegError(Exception):
    def __init__(self, message: str, returncode: int = 1, log_tail: list[str] | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.log_tail = log_tail or []


class FFmpegCancelled(Exception):
    pass


@dataclass
class ProgressUpdate:
    percent: float
    processed_seconds: float
    speed: float | None
    elapsed_seconds: float


def _parse_speed(text: str) -> float | None:
    # 'speed= 1.23x' or 'speed=N/A'
    text = text.strip().rstrip("x")
    try:
        return float(text)
    except ValueError:
        return None


class FFmpegRunner:
    def __init__(self, log_tail_lines: int = 40):
        self.log_tail_lines = log_tail_lines

    def run(
        self,
        args: list[str],
        *,
        total_duration: float | None = None,
        on_progress=None,
        cancel_event: threading.Event | None = None,
        progress_offset: float = 0.0,
        progress_span: float = 100.0,
    ) -> None:
        """Run an FFmpeg argument array. Raises FFmpegError / FFmpegCancelled.

        on_progress receives ProgressUpdate with percent mapped into
        [progress_offset, progress_offset + progress_span].
        """
        full_args = list(args)
        # Insert machine-readable progress flags right after the binary name.
        full_args[1:1] = ["-nostats", "-progress", "pipe:1"]

        log.debug("exec: %s", " ".join(full_args))
        try:
            proc = subprocess.Popen(
                full_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise FFmpegError(f"ffmpeg not found: {full_args[0]!r}") from exc

        tail: deque[str] = deque(maxlen=self.log_tail_lines)
        started = time.monotonic()
        state = {"out_seconds": 0.0, "speed": None}

        def read_stderr():
            try:
                for line in proc.stderr:
                    tail.append(line.rstrip())
            except Exception:
                pass

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        terminated_by_cancel = False
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_event is not None and cancel_event.is_set() and proc.poll() is None:
                terminated_by_cancel = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "out_time_ms":
                try:
                    state["out_seconds"] = int(value) / 1_000_000.0
                except ValueError:
                    pass
            elif key == "out_time_us" and state["out_seconds"] == 0.0:
                try:
                    state["out_seconds"] = int(value) / 1_000_000.0
                except ValueError:
                    pass
            elif key == "speed":
                state["speed"] = _parse_speed(value)
            elif key == "progress" and on_progress is not None:
                elapsed = time.monotonic() - started
                if total_duration and total_duration > 0:
                    frac = min(1.0, state["out_seconds"] / total_duration)
                else:
                    frac = 1.0 if value == "end" else 0.0
                on_progress(
                    ProgressUpdate(
                        percent=progress_offset + frac * progress_span,
                        processed_seconds=state["out_seconds"],
                        speed=state["speed"],
                        elapsed_seconds=elapsed,
                    )
                )

        returncode = proc.wait()
        stderr_thread.join(timeout=5)

        if terminated_by_cancel or (cancel_event is not None and cancel_event.is_set()):
            raise FFmpegCancelled("cancelled by user")
        if returncode != 0:
            raise FFmpegError(
                f"ffmpeg exited with code {returncode}", returncode, list(tail)
            )
        if on_progress is not None:
            on_progress(
                ProgressUpdate(
                    percent=progress_offset + progress_span,
                    processed_seconds=state["out_seconds"],
                    speed=state["speed"],
                    elapsed_seconds=time.monotonic() - started,
                )
            )


DEVNULL = os.devnull
