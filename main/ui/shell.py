"""Application-wide controls shared by the CPU and GPU Spaces."""
from __future__ import annotations

import hmac
import html
import time

import gradio as gr

from backend.services import get_services
from core.time_utils import format_hms


THEME_TOGGLE = """
<div class="theme-toggle-wrap">
  <button id="theme-toggle" type="button" aria-label="Toggle light and dark mode">
    <span class="theme-toggle-icon" aria-hidden="true">◐</span>
    <span class="theme-toggle-label">Theme</span>
  </button>
</div>
"""


def global_controls() -> None:
    """Theme toggle and password-protected force cancellation."""
    gr.HTML(THEME_TOGGLE)
    with gr.Accordion("Toolbox activity / force cancel", open=False):
        gr.Markdown(
            "Only one upload or conversion can run at a time. Force cancel is "
            "protected by the same `TOOLBOX_PASSWORD` used by the GPU login."
        )
        with gr.Row():
            password = gr.Textbox(
                label="Cancel password", type="password", max_lines=1, scale=3
            )
            status_btn = gr.Button("Check activity", scale=1)
            cancel_btn = gr.Button("Force cancel current", variant="stop", scale=1)
        status = gr.HTML()

        def _status_html() -> str:
            current = get_services().activity.snapshot()
            if not current.busy:
                return "<div class='media-info-card'><b>Available</b></div>"
            elapsed = max(0.0, time.time() - current.started_at)
            return (
                "<div class='media-info-card'><b>Busy:</b> "
                f"{html.escape(current.label or current.kind)} "
                f"<span class='dim'>({format_hms(elapsed)} elapsed)</span></div>"
            )

        status_btn.click(fn=_status_html, inputs=[], outputs=[status], queue=False)

        def _cancel(value: str) -> str:
            expected = get_services().settings.cancel_password
            if not expected:
                return (
                    "<div class='error-card'><b>Force cancel is disabled.</b> "
                    "Set the TOOLBOX_PASSWORD Space secret.</div>"
                )
            if not hmac.compare_digest(value or "", expected):
                return "<div class='error-card'><b>Incorrect password.</b></div>"
            current = get_services().activity.cancel_current()
            if not current.busy:
                return "<div class='media-info-card'><b>Nothing is running.</b></div>"
            return (
                "<div class='media-info-card'><b>Cancellation requested:</b> "
                f"{html.escape(current.label or current.kind)}. The workspace will "
                "become available as soon as the active worker exits.</div>"
            )

        cancel_btn.click(fn=_cancel, inputs=[password], outputs=[status], queue=False)
