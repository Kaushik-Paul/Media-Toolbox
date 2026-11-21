"""Process-wide exclusive activity coordination.

Uploads and jobs use the same coordinator so a Space accepts only one heavy
activity at a time.  The lease token prevents an old request from releasing a
newer activity accidentally.

Whoever holds the lease reports progress with ``update()``; ``snapshot()``
derives a best-effort ETA from the observed progress rate so the shared
"Toolbox activity" panel can show how far along the current task is.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable


# An ETA needs a little history before the observed rate means anything.
_ETA_MIN_OBSERVED_SECONDS = 3.0
_ETA_MIN_OBSERVED_PERCENT = 0.5


@dataclass(frozen=True)
class ActivitySnapshot:
    busy: bool
    kind: str = ""
    label: str = ""
    started_at: float = 0.0
    percent: float | None = None
    detail: str = ""
    eta_seconds: float | None = None


@dataclass(frozen=True)
class ActivityLease:
    token: str
    cancel_event: threading.Event


class ActivityBusyError(RuntimeError):
    pass


class ActivityCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = ""
        self._kind = ""
        self._label = ""
        self._started_at = 0.0
        self._cancel_event: threading.Event | None = None
        self._cancel_callback: Callable[[], None] | None = None
        self._reset_progress()

    def _reset_progress(self) -> None:
        self._percent: float | None = None
        self._detail = ""
        # (time, percent) when progress first moved, and when it last changed.
        self._anchor: tuple[float, float] | None = None
        self._last_change: tuple[float, float] | None = None

    def begin(
        self,
        kind: str,
        label: str,
        cancel_callback: Callable[[], None] | None = None,
    ) -> ActivityLease:
        with self._lock:
            if self._token:
                raise ActivityBusyError(
                    f"Toolbox is busy: {self._label or self._kind}. "
                    "Wait for it to finish or use the password-protected force cancel."
                )
            token = uuid.uuid4().hex
            cancel_event = threading.Event()
            self._token = token
            self._kind = kind
            self._label = label
            self._started_at = time.time()
            self._cancel_event = cancel_event
            self._cancel_callback = cancel_callback
            self._reset_progress()
            return ActivityLease(token=token, cancel_event=cancel_event)

    def set_cancel_callback(self, token: str, callback: Callable[[], None]) -> bool:
        with self._lock:
            if token != self._token:
                return False
            self._cancel_callback = callback
            return True

    def update(
        self,
        token: str,
        percent: float | None = None,
        detail: str | None = None,
    ) -> bool:
        """Report progress (0-100) and/or a short status line for the lease."""
        now = time.time()
        with self._lock:
            if token != self._token:
                return False
            if detail is not None:
                self._detail = detail
            if percent is not None:
                percent = min(100.0, max(0.0, float(percent)))
                previous = self._percent
                self._percent = percent
                if percent > 0 and self._anchor is None:
                    self._anchor = (now, percent)
                    self._last_change = (now, percent)
                elif previous is None or percent != previous:
                    self._last_change = (now, percent)
            return True

    def finish(self, token: str) -> bool:
        with self._lock:
            if token != self._token:
                return False
            self._token = ""
            self._kind = ""
            self._label = ""
            self._started_at = 0.0
            self._cancel_event = None
            self._cancel_callback = None
            self._reset_progress()
            return True

    def cancel_current(self) -> ActivitySnapshot:
        with self._lock:
            snapshot = self._snapshot_unlocked()
            event = self._cancel_event
            callback = self._cancel_callback
        if event is not None:
            event.set()
        if callback is not None:
            callback()
        return snapshot

    def snapshot(self) -> ActivitySnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> ActivitySnapshot:
        return ActivitySnapshot(
            busy=bool(self._token),
            kind=self._kind,
            label=self._label,
            started_at=self._started_at,
            percent=self._percent,
            detail=self._detail,
            eta_seconds=self._eta_unlocked(time.time()),
        )

    def _eta_unlocked(self, now: float) -> float | None:
        """Linear ETA from the progress rate since progress first moved.

        Returns None while there is too little history, or once the estimate
        has run out without progress moving (e.g. a long opaque GPU stage):
        an honest "estimating" beats a stale countdown stuck at zero.
        """
        if self._anchor is None or self._last_change is None or self._percent is None:
            return None
        t0, p0 = self._anchor
        t1, p1 = self._last_change
        if p1 >= 100.0:
            return None
        observed = t1 - t0
        gained = p1 - p0
        if observed < _ETA_MIN_OBSERVED_SECONDS or gained < _ETA_MIN_OBSERVED_PERCENT:
            return None
        remaining = (100.0 - p1) * observed / gained - (now - t1)
        return remaining if remaining > 0 else None


_activity = ActivityCoordinator()


def get_activity() -> ActivityCoordinator:
    return _activity
