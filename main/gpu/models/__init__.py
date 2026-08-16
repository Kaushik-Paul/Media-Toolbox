"""GPU inference models (Whisper, Demucs, Real-ESRGAN).

Heavy dependencies (torch, transformers, demucs) are imported lazily inside
functions so the application stays importable and usable when a model is
disabled or its dependency is unavailable (PLAN.md rule 20).
"""
