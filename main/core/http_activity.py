"""ASGI middleware that applies the exclusive activity gate to uploads."""
from __future__ import annotations

from core.activity import ActivityBusyError, ActivityCoordinator


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

        async def cancellable_receive():
            if lease.cancel_event.is_set():
                return {"type": "http.disconnect"}
            message = await receive()
            if lease.cancel_event.is_set():
                return {"type": "http.disconnect"}
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
