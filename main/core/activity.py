"""Process-wide exclusive activity coordination.

Uploads and jobs use the same coordinator so a Space accepts only one heavy
activity at a time.  The lease token prevents an old request from releasing a
newer activity accidentally.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ActivitySnapshot:
    busy: bool
    kind: str = ""
    label: str = ""
    started_at: float = 0.0


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
            return ActivityLease(token=token, cancel_event=cancel_event)

    def set_cancel_callback(self, token: str, callback: Callable[[], None]) -> bool:
        with self._lock:
            if token != self._token:
                return False
            self._cancel_callback = callback
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
        )


_activity = ActivityCoordinator()


def get_activity() -> ActivityCoordinator:
    return _activity
