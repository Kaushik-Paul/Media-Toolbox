"""Visual identity shared by the CPU Space (and later the GPU Space)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

import gradio as gr

THEME = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="slate",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
).set(
    body_background_fill="*neutral_50",
    body_background_fill_dark="*neutral_950",
    block_radius="12px",
    button_primary_background_fill="*primary_600",
    button_primary_background_fill_hover="*primary_500",
)

CSS = """
.app-header { text-align: center; margin-bottom: 0.5rem; }
.app-header h1 { font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.1rem; }
.app-header p { color: var(--body-text-color-subdued); margin-top: 0; }

.media-info-card {
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    background: var(--background-fill-secondary);
    font-size: 0.95rem;
    line-height: 1.55;
}
.media-info-card .dim { color: var(--body-text-color-subdued); }

.progress-wrap {
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    padding: 1rem 1.1rem;
    background: var(--background-fill-secondary);
}
.progress-bar-outer {
    height: 14px; border-radius: 8px; overflow: hidden;
    background: var(--neutral-200, #e5e7eb);
}
.dark .progress-bar-outer { background: #1f2937; }
.progress-bar-inner {
    height: 100%; border-radius: 8px;
    background: linear-gradient(90deg, #6366f1, #8b5cf6);
    transition: width 0.4s ease;
}
.progress-stats { display: flex; gap: 1.4rem; flex-wrap: wrap; margin-top: 0.55rem;
    font-size: 0.88rem; color: var(--body-text-color-subdued); }

.result-card {
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    padding: 1rem 1.2rem;
    background: var(--background-fill-secondary);
}
.result-stats { display: flex; gap: 1.6rem; flex-wrap: wrap; margin: 0.4rem 0 0.2rem; }
.result-stats .stat { display: flex; flex-direction: column; }
.result-stats .stat b { font-size: 1.05rem; }
.result-stats .stat span { font-size: 0.8rem; color: var(--body-text-color-subdued); }

.expiry-note { font-size: 0.85rem; color: var(--body-text-color-subdued); }
.error-card {
    border: 1px solid #ef4444; border-radius: 12px; padding: 0.9rem 1.1rem;
    background: rgba(239, 68, 68, 0.08);
}

@media (max-width: 640px) {
    .app-header h1 { font-size: 1.5rem; }
    .result-stats { gap: 1rem; }
}
"""

# Gradio follows the visitor's system/browser theme unless the URL carries
# ?__theme=dark. Gradio 6 injects this snippet verbatim as a <script> body,
# so it must be an IIFE: a bare function expression would never be called.
FORCE_DARK_JS = """
(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("__theme") !== "dark") {
        url.searchParams.set("__theme", "dark");
        window.location.replace(url.toString());
    }
})();
"""


class ForceDarkThemeMiddleware:
    """Redirect Gradio page loads to ?__theme=dark.

    Server-side (unlike FORCE_DARK_JS) so it also covers the login page,
    whose config does not include custom js.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") in ("GET", "HEAD")
            and scope.get("path", "").rstrip("/") == ""
        ):
            params = parse_qsl(
                scope.get("query_string", b"").decode(), keep_blank_values=True
            )
            if ("__theme", "dark") not in params:
                params = [(k, v) for k, v in params if k != "__theme"]
                params.append(("__theme", "dark"))
                location = "/?" + urlencode(params)
                await send({
                    "type": "http.response.start",
                    "status": 302,
                    "headers": [
                        (b"location", location.encode()),
                        (b"content-length", b"0"),
                    ],
                })
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)
