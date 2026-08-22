"""Visual identity and persistent light/dark preference."""
from __future__ import annotations

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
.theme-toggle-wrap { display: flex; justify-content: flex-end; margin: -0.35rem 0 0.45rem; }
#theme-toggle {
    border: 1px solid var(--border-color-primary); border-radius: 999px;
    padding: 0.42rem 0.78rem; background: var(--background-fill-secondary);
    color: var(--body-text-color); cursor: pointer; font: inherit;
    display: inline-flex; align-items: center; gap: 0.45rem;
}
#theme-toggle:hover { border-color: var(--color-accent); transform: translateY(-1px); }
.theme-toggle-icon { font-size: 1.15rem; line-height: 1; }

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
.result-preview { display: block; max-width: 100%; max-height: 32rem; border-radius: 10px; margin-top: 0.8rem; }
.result-audio { display: block; width: 100%; margin-top: 0.35rem; }
.download-list { margin: 0.7rem 0 0.25rem; }
.error-card {
    border: 1px solid #ef4444; border-radius: 12px; padding: 0.9rem 1.1rem;
    background: rgba(239, 68, 68, 0.08);
}

@media (max-width: 640px) {
    .app-header h1 { font-size: 1.5rem; }
    .result-stats { gap: 1rem; }
}
"""

# Gradio selects its palette from the __theme query parameter. Persist the
# visitor's explicit choice and reload only when the palette actually changes.
THEME_JS = """
(() => {
    const storageKey = "media-toolbox-theme";
    const readSaved = () => {
        try { return localStorage.getItem(storageKey); } catch (_) { return null; }
    };
    const writeSaved = (value) => {
        try { localStorage.setItem(storageKey, value); } catch (_) {}
    };
    const systemTheme = () => window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
    const url = new URL(window.location.href);
    const saved = readSaved();
    const desired = saved === "dark" || saved === "light" ? saved : systemTheme();
    if (url.searchParams.get("__theme") !== desired) {
        url.searchParams.set("__theme", desired);
        window.location.replace(url.toString());
        return;
    }

    const refreshLabel = () => {
        const label = document.querySelector("#theme-toggle .theme-toggle-label");
        if (label) label.textContent = desired === "dark" ? "Light mode" : "Dark mode";
    };
    document.addEventListener("click", (event) => {
        const button = event.target.closest && event.target.closest("#theme-toggle");
        if (!button) return;
        event.preventDefault();
        const next = desired === "dark" ? "light" : "dark";
        writeSaved(next);
        const nextUrl = new URL(window.location.href);
        nextUrl.searchParams.set("__theme", next);
        window.location.replace(nextUrl.toString());
    }, true);
    new MutationObserver(refreshLabel).observe(document.documentElement, {childList: true, subtree: true});
    refreshLabel();
})();
"""
