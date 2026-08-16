"""Shared UI building blocks for the GPU Space.

Downloads and previews always go through the expiry-checked API routes
(``/api/jobs/{prefix}/download/{file_id}``); physical bucket paths are never
exposed to Gradio components (PLAN.md rule 29).
"""
from __future__ import annotations

import html
import time
from pathlib import Path
from typing import NamedTuple

import gradio as gr

from core.models import MediaInfo
from core.storage.retention import seconds_until
from core.time_utils import format_countdown, format_hms, format_size

from gpu.backend.job_manager import JobStatus, OperationError
from gpu.backend.services import get_services


def download_url(prefix: str, file_id: str) -> str:
    return f"/api/jobs/{prefix}/download/{file_id}"


# ---------------------------------------------------------------------------
# Upload + probe
# ---------------------------------------------------------------------------


class UploadContext(NamedTuple):
    file: gr.File
    info: gr.HTML
    info_state: gr.State


def file_paths(file_value) -> list[Path]:
    if not file_value:
        return []
    values = file_value if isinstance(file_value, list) else [file_value]
    return [Path(v) for v in values if v]


def media_info_card(info: MediaInfo) -> str:
    e = html.escape
    parts = [
        "<div class='media-info-card'>",
        f"<b>{e(info.filename)}</b> <span class='dim'>{format_size(info.size)}"
        f" &middot; {format_hms(info.duration_seconds)} &middot; {e(info.format_name)}</span>",
    ]
    for s in info.streams_of("video"):
        fps = f" @ {s.fps:g} fps" if s.fps else ""
        parts.append(f"<br>Video: {e(s.codec_name)} {s.width or '?'}&times;{s.height or '?'}{fps}")
    for s in info.streams_of("audio"):
        ch = {1: "mono", 2: "stereo"}.get(s.channels or 0, f"{s.channels}ch" if s.channels else "")
        rate = f"{s.sample_rate} Hz" if s.sample_rate else ""
        parts.append(f"<br>Audio: {e(s.codec_name)} {rate} {ch}")
    parts.append("</div>")
    return "".join(parts)


def probe_upload(file_value):
    """gr.File change handler -> (info card HTML, state dict)."""
    paths = file_paths(file_value)
    if not paths:
        return "", {}
    services = get_services()
    try:
        info = services.probe.probe(paths[0])
    except Exception as exc:
        return (
            f"<div class='error-card'><b>Could not analyze this file.</b><br>"
            f"<span class='dim'>{html.escape(str(exc))[:300]}</span></div>",
            {},
        )
    state = {
        "filename": info.filename,
        "duration": info.duration_seconds,
        "has_video": info.has_video,
        "has_audio": info.has_audio,
    }
    return media_info_card(info), state


def upload_row(label: str = "Drop media here", file_types: list[str] | None = None) -> UploadContext:
    file_input = gr.File(label=label, file_count="single", file_types=file_types, type="filepath")
    info_html = gr.HTML()
    info_state = gr.State({})
    file_input.change(fn=probe_upload, inputs=[file_input], outputs=[info_html, info_state])
    return UploadContext(file_input, info_html, info_state)


# ---------------------------------------------------------------------------
# Progress / error / result rendering
# ---------------------------------------------------------------------------


def progress_html(percent: float, status_text: str, elapsed: float | None = None) -> str:
    percent = max(0.0, min(100.0, percent))
    elapsed_txt = format_hms(elapsed) if elapsed else "-"
    return f"""
<div class='progress-wrap'>
  <div style='display:flex;justify-content:space-between;margin-bottom:0.4rem'>
    <b>{html.escape(status_text)}</b><span>{percent:.0f}%</span>
  </div>
  <div class='progress-bar-outer'><div class='progress-bar-inner' style='width:{percent:.1f}%'></div></div>
  <div class='progress-stats'><span>Elapsed: {elapsed_txt}</span></div>
</div>"""


def error_html(message: str, details: str = "") -> str:
    detail_html = ""
    if details:
        detail_html = (
            f"<details style='margin-top:0.4rem'><summary>Technical details</summary>"
            f"<pre style='white-space:pre-wrap;font-size:0.78rem'>{html.escape(details[:3000])}</pre></details>"
        )
    return f"<div class='error-card'><b>{html.escape(message)}</b>{detail_html}</div>"


def _preview_html(prefix: str, output) -> str:
    url = download_url(prefix, output.id)
    name = html.escape(output.filename)
    if output.mime_type.startswith("video/"):
        return (f"<video controls preload='metadata' style='max-width:100%;border-radius:8px' "
                f"src='{url}'></video>")
    if output.mime_type.startswith("audio/"):
        return f"<div style='margin:0.3rem 0'><span class='dim'>{name}</span><br><audio controls src='{url}' style='width:100%'></audio></div>"
    if output.mime_type.startswith("image/"):
        return f"<img src='{url}' alt='{name}' style='max-width:100%;border-radius:8px'>"
    return ""


def result_card_html(state) -> str:
    res = state.result
    extra = "".join(
        f"<div class='stat'><b>{html.escape(str(v))}</b><span>{html.escape(str(k))}</span></div>"
        for k, v in (res.summary or {}).items()
    )
    remaining = format_countdown(seconds_until(res.expires_unix))
    downloads = "".join(
        f"<li><a href='{download_url(res.prefix, o.id)}' download>{html.escape(o.filename)}</a>"
        f" <span class='dim'>({format_size(o.size)})</span></li>"
        for o in res.outputs
    )
    previews = "".join(
        _preview_html(res.prefix, o)
        for o in res.outputs
        if o.mime_type.split("/")[0] in ("video", "image")
    )
    audio_previews = "".join(
        _preview_html(res.prefix, o) for o in res.outputs if o.mime_type.startswith("audio/")
    )
    return f"""
<div class='result-card'>
  <b style='font-size:1.05rem'>&#10003; Complete</b>
  <div class='result-stats'>
    <div class='stat'><b>{format_size(res.input_size)}</b><span>Input</span></div>
    <div class='stat'><b>{format_size(res.output_size)}</b><span>Output</span></div>
    <div class='stat'><b>{format_hms(res.processing_seconds)}</b><span>Processing time</span></div>
    {extra}
    <div class='stat'><b>{remaining}</b><span>Expires in</span></div>
  </div>
  {previews}
  {audio_previews}
  <ul style='margin:0.6rem 0 0.2rem'>{downloads}</ul>
  <div class='expiry-note'>Links expire 24 hours after completion.</div>
</div>"""


# ---------------------------------------------------------------------------
# Operation scaffolding
# ---------------------------------------------------------------------------


class OpUI:
    """Run/cancel/delete buttons plus polling wiring for one GPU operation."""

    def __init__(self, run_label: str = "Run"):
        with gr.Row():
            self.run_btn = gr.Button(run_label, variant="primary", scale=3)
            self.cancel_btn = gr.Button("Cancel", variant="stop", scale=1, visible=False)
        self.progress = gr.HTML(value="")
        with gr.Group(visible=False) as self.result_group:
            self.result_html = gr.HTML()
            self.delete_btn = gr.Button("Delete now", variant="stop", size="sm")
        self.job_state = gr.State({})

    def outputs(self) -> list:
        return [self.progress, self.result_group, self.result_html, self.job_state]

    def wire(self, operation, file_components: list, param_components: dict[str, gr.Component]):
        """operation: fixed name, or callable(params) -> name for mode-dispatched tools."""
        services = get_services()
        param_keys = list(param_components.keys())
        inputs = list(file_components) + [param_components[k] for k in param_keys]

        def _run(*values):
            params = dict(zip(param_keys, values[len(file_components):]))
            op_name = operation(params) if callable(operation) else operation
            yield from self._execute(op_name, values[: len(file_components)], params)

        event = self.run_btn.click(fn=_run, inputs=inputs, outputs=self.outputs())

        def _cancel(state):
            if state and state.get("job_id"):
                services.jobs.cancel(state["job_id"])

        self.cancel_btn.click(fn=_cancel, inputs=[self.job_state], outputs=[], cancels=[event])

        def _delete(state):
            if state and state.get("prefix"):
                services.storage.delete_job(state["prefix"])
            return gr.update(visible=False), "", {}

        self.delete_btn.click(
            fn=_delete,
            inputs=[self.job_state],
            outputs=[self.result_group, self.result_html, self.job_state],
        )
        return event

    def _execute(self, operation: str, file_values, params: dict):
        services = get_services()
        paths: list[Path] = []
        for value in file_values:
            paths.extend(file_paths(value))
        if not paths:
            yield error_html("Upload a file first."), gr.update(visible=False), "", {}
            return
        original = paths[0].name
        try:
            job_id = services.jobs.submit(operation, paths, original, params)
        except OperationError as exc:
            yield error_html(str(exc), exc.details), gr.update(visible=False), "", {}
            return

        started = time.monotonic()
        state_box = {"job_id": job_id}
        while True:
            state = services.jobs.get(job_id)
            if state is None:
                yield (error_html("Job state lost (server restarted?)."),
                       gr.update(visible=False), "", state_box)
                return
            if state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                break
            yield (progress_html(state.percent, state.status_text, time.monotonic() - started),
                   gr.update(visible=False), "", state_box)
            time.sleep(0.5)

        if state.status == JobStatus.COMPLETED and state.result:
            state_box["prefix"] = state.result.prefix
            yield (
                progress_html(100, "Complete", time.monotonic() - started),
                gr.update(visible=True),
                result_card_html(state),
                state_box,
            )
        elif state.status == JobStatus.CANCELLED:
            yield error_html("Job cancelled."), gr.update(visible=False), "", state_box
        else:
            yield (error_html(state.error or "Job failed.", state.error_details),
                   gr.update(visible=False), "", state_box)
