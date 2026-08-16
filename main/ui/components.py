"""Shared UI building blocks: upload info card, progress, result card, wiring."""
from __future__ import annotations

import html
import time
from pathlib import Path

import gradio as gr

from backend.job_manager import JobStatus
from backend.services import get_services
from core.media_types import MediaKind, kind_from_extension
from core.models import MediaInfo
from core.storage.retention import seconds_until
from core.time_utils import format_countdown, format_hms, format_size
from operations.base import OperationError

# ---------------------------------------------------------------------------
# Upload + probe
# ---------------------------------------------------------------------------


def file_paths(file_value) -> list[Path]:
    """Normalize a gr.File value (str | list | None) into Paths."""
    if not file_value:
        return []
    values = file_value if isinstance(file_value, list) else [file_value]
    return [Path(v) for v in values if v]


def media_info_card(info: MediaInfo) -> str:
    e = html.escape
    parts = [
        f"<div class='media-info-card'>",
        f"<b>{e(info.filename)}</b> <span class='dim'>{format_size(info.size)}"
        f" &middot; {format_hms(info.duration_seconds)} &middot; {e(info.format_name)}</span>",
    ]
    for s in info.streams_of("video"):
        fps = f" @ {s.fps:g} fps" if s.fps else ""
        pix = f" ({e(s.pix_fmt)})" if s.pix_fmt else ""
        parts.append(
            f"<br>Video: {e(s.codec_name)} {s.width or '?'}&times;{s.height or '?'}{fps}{pix}"
        )
    for s in info.streams_of("audio"):
        ch = {1: "mono", 2: "stereo"}.get(s.channels or 0, f"{s.channels}ch" if s.channels else "")
        rate = f"{s.sample_rate} Hz" if s.sample_rate else ""
        lang = f" [{e(s.language)}]" if s.language else ""
        parts.append(f"<br>Audio: {e(s.codec_name)} {rate} {ch}{lang}")
    subs = info.subtitle_streams
    if subs:
        labels = ", ".join(f"{s.language or 'und'} ({s.codec_name})" for s in subs)
        parts.append(f"<br>Subtitles: {e(labels)}")
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
        "width": (info.primary_video.width if info.primary_video else None),
        "height": (info.primary_video.height if info.primary_video else None),
        "subtitles": [
            {"index": i, "codec": s.codec_name, "language": s.language or "und"}
            for i, s in enumerate(info.subtitle_streams)
        ],
    }
    return media_info_card(info), state


# ---------------------------------------------------------------------------
# Progress + result rendering
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


def result_card_html(state) -> str:
    res = state.result
    reduction = ""
    if res.input_size:
        saved = (1 - res.output_size / res.input_size) * 100
        reduction = f"<div class='stat'><b>{saved:.1f}%</b><span>Reduction</span></div>"
    extra = "".join(
        f"<div class='stat'><b>{html.escape(str(v))}</b><span>{html.escape(str(k))}</span></div>"
        for k, v in (res.summary or {}).items()
    )
    remaining = format_countdown(seconds_until(res.expires_unix))
    return f"""
<div class='result-card'>
  <b style='font-size:1.05rem'>&#10003; Complete</b>
  <div class='result-stats'>
    <div class='stat'><b>{format_size(res.input_size)}</b><span>Input</span></div>
    <div class='stat'><b>{format_size(res.output_size)}</b><span>Output</span></div>
    {reduction}
    <div class='stat'><b>{format_hms(res.processing_seconds)}</b><span>Processing time</span></div>
    {extra}
    <div class='stat'><b>{remaining}</b><span>Expires in</span></div>
  </div>
</div>"""


def _preview_updates(paths: list[Path]):
    video = gr.update(visible=False, value=None)
    audio = gr.update(visible=False, value=None)
    image = gr.update(visible=False, value=None)
    if paths:
        kind = kind_from_extension(paths[0].name)
        if kind == MediaKind.VIDEO:
            video = gr.update(visible=True, value=str(paths[0]))
        elif kind == MediaKind.AUDIO:
            audio = gr.update(visible=True, value=str(paths[0]))
        elif kind == MediaKind.IMAGE:
            image = gr.update(visible=True, value=str(paths[0]))
    return video, audio, image


# ---------------------------------------------------------------------------
# Operation scaffolding
# ---------------------------------------------------------------------------


class OpUI:
    """Standard per-operation components and event wiring."""

    OUTPUTS_ORDER = (
        "progress", "result_group", "result_html", "result_files",
        "preview_video", "preview_audio", "preview_image", "cmd_md", "job_state",
    )

    def __init__(self, run_label: str = "Convert"):
        with gr.Row():
            self.run_btn = gr.Button(run_label, variant="primary", scale=3)
            self.cancel_btn = gr.Button("Cancel", variant="stop", scale=1, visible=False)
        self.progress = gr.HTML(value="")
        with gr.Group(visible=False) as self.result_group:
            self.result_html = gr.HTML()
            self.preview_video = gr.Video(visible=False, label="Preview")
            self.preview_audio = gr.Audio(visible=False, label="Preview")
            self.preview_image = gr.Image(visible=False, label="Preview")
            self.result_files = gr.File(label="Downloads", file_count="multiple", interactive=False)
            with gr.Accordion("Advanced details", open=False):
                self.cmd_md = gr.Markdown()
            self.delete_btn = gr.Button("Delete now", variant="stop", size="sm")
        self.job_state = gr.State({})

    def outputs(self) -> list:
        return [getattr(self, name) for name in self.OUTPUTS_ORDER]

    def wire(self, operation: str, file_components: list, param_components: dict[str, gr.Component]):
        services = get_services()
        param_keys = list(param_components.keys())
        inputs = list(file_components) + [param_components[k] for k in param_keys]

        def _run(*values):
            yield from self._execute(operation, values[: len(file_components)],
                                     dict(zip(param_keys, values[len(file_components):])))

        event = self.run_btn.click(fn=_run, inputs=inputs, outputs=self.outputs())

        def _cancel(state):
            if state and state.get("job_id"):
                services.jobs.cancel(state["job_id"])

        self.cancel_btn.click(fn=_cancel, inputs=[self.job_state], outputs=[], cancels=[event])

        def _delete(state):
            if state and state.get("prefix"):
                services.storage.delete_job(state["prefix"])
            return (
                gr.update(visible=False), gr.update(value=None),
                gr.update(visible=False, value=None), gr.update(visible=False, value=None),
                gr.update(visible=False, value=None), "", {},
            )

        self.delete_btn.click(
            fn=_delete,
            inputs=[self.job_state],
            outputs=[self.result_group, self.result_files, self.preview_video,
                     self.preview_audio, self.preview_image, self.result_html, self.job_state],
        )
        return event

    def _execute(self, operation: str, file_values, params: dict):
        services = get_services()
        paths: list[Path] = []
        for value in file_values:
            paths.extend(file_paths(value))
        if not paths:
            yield (error_html("Upload a file first."), gr.update(visible=False),
                   "", None, *self._hidden_previews(), "", {})
            return
        original = paths[0].name
        try:
            job_id = services.jobs.submit(operation, paths, original, params)
        except OperationError as exc:
            yield (error_html(str(exc), exc.details), gr.update(visible=False),
                   "", None, *self._hidden_previews(), "", {})
            return

        started = time.monotonic()
        state_box = {"job_id": job_id}
        while True:
            state = services.jobs.get(job_id)
            if state is None:
                yield (error_html("Job state lost (server restarted?)."), gr.update(visible=False),
                       "", None, *self._hidden_previews(), "", state_box)
                return
            if state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                break
            yield (
                progress_html(state.percent, state.status_text, time.monotonic() - started),
                gr.update(visible=False), "", None,
                *self._hidden_previews(), "", state_box,
            )
            time.sleep(0.5)

        if state.status == JobStatus.COMPLETED and state.result:
            state_box["prefix"] = state.result.prefix
            video, audio, image = _preview_updates(state.result.output_paths)
            cmds = "\n\n".join(f"```bash\n{c}\n```" for c in state.result.command_previews)
            yield (
                progress_html(100, "Complete", time.monotonic() - started),
                gr.update(visible=True),
                result_card_html(state),
                [str(p) for p in state.result.output_paths],
                video, audio, image,
                cmds,
                state_box,
            )
        elif state.status == JobStatus.CANCELLED:
            yield (error_html("Job cancelled."), gr.update(visible=False),
                   "", None, *self._hidden_previews(), "", state_box)
        else:
            yield (error_html(state.error or "Job failed.", state.error_details),
                   gr.update(visible=False), "", None, *self._hidden_previews(), "", state_box)

    @staticmethod
    def _hidden_previews():
        return (
            gr.update(visible=False, value=None),
            gr.update(visible=False, value=None),
            gr.update(visible=False, value=None),
        )


def upload_row(label: str = "Drop media here", file_types: list[str] | None = None,
               file_count: str = "single"):
    """Standard upload + auto-probe info card. Returns (file, info_html, info_state)."""
    file_input = gr.File(label=label, file_count=file_count, file_types=file_types, type="filepath")
    info_html = gr.HTML()
    info_state = gr.State({})
    file_input.change(fn=probe_upload, inputs=[file_input], outputs=[info_html, info_state])
    return file_input, info_html, info_state
