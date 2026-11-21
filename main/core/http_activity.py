"""ASGI middleware that applies the exclusive activity gate to uploads."""
from __future__ import annotations

from core.activity import ActivityBusyError, ActivityCoordinator
from core.time_utils import format_size


def _content_length(scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                length = int(value)
            except ValueError:
                return None
            return length if length > 0 else None
    return None


class ExclusiveUploadMiddleware:
    """Allow one streamed Gradio upload and make it cooperatively cancellable."""

    def __init__(self, app, activity: ActivityCoordinator):
        self.app = app
        self.activity = activity

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").rstrip("/")
        is_upload = (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and path in ("/upload", "/gradio_api/upload")
        )
        if not is_upload:
            await self.app(scope, receive, send)
            return

        try:
            lease = self.activity.begin("upload", "media upload")
        except ActivityBusyError as exc:
            body = str(exc).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 409,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        response_started = False
        total = _content_length(scope)
        received = 0
        self.activity.update(
            lease.token,
            0.0 if total else None,
            f"Receiving {format_size(total)}" if total else "Receiving file",
        )

        async def cancellable_receive():
            nonlocal received
            if lease.cancel_event.is_set():
                return {"type": "http.disconnect"}
            message = await receive()
            if lease.cancel_event.is_set():
                return {"type": "http.disconnect"}
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if total:
                    self.activity.update(
                        lease.token,
                        min(100.0, received * 100.0 / total),
                        f"{format_size(received)} of {format_size(total)} received",
                    )
                else:
                    self.activity.update(lease.token, None, f"{format_size(received)} received")
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, cancellable_receive, tracked_send)
        except Exception:
            if not lease.cancel_event.is_set() or response_started:
                raise
            body = b"Upload cancelled"
            await send({
                "type": "http.response.start",
                "status": 499,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
        finally:
            self.activity.finish(lease.token)
