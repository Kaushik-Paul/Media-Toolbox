"""Application-wide controls shared by the CPU and GPU Spaces."""
from __future__ import annotations

import hmac
import html
import time

import gradio as gr

from backend.services import get_services
from core.activity import ActivitySnapshot
from core.time_utils import format_hms

ACTIVITY_REFRESH_SECONDS = 2.0


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
            "Only one upload or conversion can run at a time. Check activity shows "
            "live progress and an estimated time remaining. Force cancel is "
            "protected by the same `TOOLBOX_PASSWORD` used by the GPU login."
        )
        with gr.Row(equal_height=True, elem_classes=["activity-actions"]):
            password = gr.Textbox(
                label="Cancel password",
                placeholder="Cancel password",
                show_label=False,
                type="password",
                max_lines=1,
                scale=2,
                min_width=220,
            )
            status_btn = gr.Button("Check activity", scale=1, min_width=180)
            cancel_btn = gr.Button(
                "Force cancel current", variant="stop", scale=1, min_width=220
            )
        status = gr.HTML()
        # Live refresh while something is running; switches itself off once the
        # toolbox is available again so idle pages make no background requests.
        refresh = gr.Timer(ACTIVITY_REFRESH_SECONDS, active=False)

        def _refresh() -> tuple[str, gr.Timer]:
            current = get_services().activity.snapshot()
            return _status_html(current), gr.Timer(active=current.busy)

        status_btn.click(fn=_refresh, inputs=[], outputs=[status, refresh], queue=False)
        refresh.tick(
            fn=_refresh, inputs=[], outputs=[status, refresh],
            queue=False, show_progress="hidden",
        )

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


def _status_html(current: ActivitySnapshot) -> str:
    """Busy/available card with a progress bar, elapsed time, and ETA."""
    if not current.busy:
        return "<div class='media-info-card'><b>Available</b></div>"
    elapsed = max(0.0, time.time() - current.started_at)
    label = html.escape((current.label or current.kind).capitalize())
    detail = (
        f"<div class='dim'>{html.escape(current.detail)}</div>" if current.detail else ""
    )
    if current.percent is None:
        bar = ""
        progress = "Progress unavailable"
    else:
        pct = min(100.0, max(0.0, current.percent))
        bar = (
            "<div class='progress-bar-outer activity-bar'>"
            f"<div class='progress-bar-inner' style='width:{pct:.1f}%'></div></div>"
        )
        progress = f"{pct:.1f}%"
    if current.eta_seconds is not None:
        eta = f"<span>~{format_hms(current.eta_seconds)} remaining</span>"
    elif current.percent is None:
        eta = ""
    elif current.percent >= 100.0:
        eta = "<span>Finishing up</span>"
    else:
        eta = "<span>Estimating time remaining…</span>"
    return (
        "<div class='progress-wrap activity-status'>"
        f"<div class='activity-head'><b>Busy:</b> {label}</div>"
        f"{detail}{bar}"
        "<div class='progress-stats'>"
        f"<span><b>{progress}</b></span>"
        f"<span>{format_hms(elapsed)} elapsed</span>"
        f"{eta}"
        "</div></div>"
    )
