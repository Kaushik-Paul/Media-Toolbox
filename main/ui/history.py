"""History tab: recent jobs from the shared bucket, with download and delete."""
from __future__ import annotations

import html
import gradio as gr

from backend.services import get_services
from core.storage.retention import seconds_until
from core.time_utils import format_countdown, format_size

HEADERS = ["Operation", "File", "Size", "Source", "Expires in", "Completed (UTC)"]


def _load_jobs():
    services = get_services()
    rows = []
    prefixes = []
    for prefix, manifest in services.storage.list_jobs():
        total = sum(o.size for o in manifest.outputs)
        rows.append([
            manifest.operation.replace("_", " ").title(),
            manifest.original_filename,
            format_size(total),
            manifest.source.upper(),
            format_countdown(seconds_until(manifest.expires_unix)),
            manifest.completed_at,
        ])
        prefixes.append(prefix)
    return rows, prefixes


def history_tab():
    gr.Markdown("Jobs from this Space and the AI Toolbox. Outputs are kept for 24 hours.")
    refresh_btn = gr.Button("Refresh", size="sm")
    table = gr.Dataframe(headers=HEADERS, value=[], interactive=False, wrap=True, label="Recent jobs")
    prefixes_state = gr.State([])

    with gr.Group(visible=False) as detail_group:
        detail_md = gr.Markdown()
        detail_files = gr.HTML()
        delete_btn = gr.Button("Delete this job", variant="stop", size="sm")
    selected_prefix = gr.State("")

    def _refresh():
        rows, prefixes = _load_jobs()
        return rows, prefixes

    refresh_btn.click(fn=_refresh, inputs=[], outputs=[table, prefixes_state])

    def _select(prefixes, evt: gr.SelectData):
        if not prefixes or evt.index[0] >= len(prefixes):
            return gr.update(visible=False), None, "", ""
        prefix = prefixes[evt.index[0]]
        services = get_services()
        try:
            manifest = services.storage.get_manifest(prefix)
        except Exception:
            return gr.update(visible=False), None, "", ""
        links = "".join(
            f"<li><a href='/api/jobs/{prefix}/download/{o.id}' download>"
            f"{html.escape(o.filename)}</a> <span class='dim'>({format_size(o.size)})</span></li>"
            for o in manifest.outputs
        )
        detail = (
            f"**{manifest.operation.replace('_', ' ').title()}** — `{manifest.original_filename}`  \n"
            f"Source: {manifest.source.upper()} · Completed: {manifest.completed_at} · "
            f"Expires in {format_countdown(seconds_until(manifest.expires_unix))}"
        )
        return gr.update(visible=True), f"<ul class='download-list'>{links}</ul>", detail, prefix

    table.select(fn=_select, inputs=[prefixes_state], outputs=[detail_group, detail_files, detail_md, selected_prefix])

    def _delete(prefix):
        if prefix:
            get_services().storage.delete_job(prefix)
        rows, prefixes = _load_jobs()
        return rows, prefixes, gr.update(visible=False), None, ""

    delete_btn.click(fn=_delete, inputs=[selected_prefix],
                     outputs=[table, prefixes_state, detail_group, detail_files, detail_md])
