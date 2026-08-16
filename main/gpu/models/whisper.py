"""Speech transcription with Whisper (openai/whisper-large-v3-turbo).

Pipeline: FFmpeg audio extraction (CPU) -> Whisper inference (GPU) ->
TXT/SRT/VTT/JSON writers (CPU) -> bucket upload (CPU).

torch/transformers are imported lazily so the app still boots when they are
unavailable; only this tool then reports itself unavailable.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from core.filenames import output_name

from gpu.backend.config import gpu
from gpu.backend.job_manager import JobContext, OperationError, OperationResult, ProducedOutput
from gpu.backend.postprocessing import (
    group_words_to_cues,
    normalize_chunks,
    write_json,
    write_srt,
    write_txt,
    write_vtt,
)
from gpu.backend.preprocessing import extract_audio

log = logging.getLogger(__name__)

# Label -> Whisper language code. "Auto" means language detection.
LANGUAGES = {
    "Auto": None,
    "English": "en",
    "Bengali": "bn",
    "Hindi": "hi",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Russian": "ru",
    "Arabic": "ar",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh",
    "Italian": "it",
    "Dutch": "nl",
    "Turkish": "tr",
    "Polish": "pl",
    "Ukrainian": "uk",
    "Tamil": "ta",
    "Telugu": "te",
    "Marathi": "mr",
    "Urdu": "ur",
}

TASKS = {"Transcribe": "transcribe", "Translate to English": "translate"}
TIMESTAMP_MODES = ("Segment", "Word-level")

_pipe = None
_pipe_lock = threading.Lock()


def _get_pipeline(model_id: str):
    """Build (once) the ASR pipeline in the main process.

    Must run OUTSIDE @spaces.GPU functions: ZeroGPU executes those in a
    throwaway fork, so a model loaded inside one would be reloaded on every
    call. CUDA emulation lets the load + `.to("cuda")` succeed in the main
    process, and each fork then inherits the loaded model.
    """
    global _pipe
    with _pipe_lock:
        if _pipe is not None:
            return _pipe
        import torch
        from transformers import pipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        log.info("loading Whisper model %s on %s", model_id, device)
        _pipe = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            torch_dtype=dtype,
            device=device,
            model_kwargs={"attn_implementation": "sdpa"},
        )
        return _pipe


def _audio_duration_seconds(audio_path: str) -> float | None:
    try:
        import soundfile as sf

        return float(sf.info(audio_path).duration)
    except Exception:
        return None


def estimate_whisper_duration(audio_path: str, language: str | None = None,
                              task: str = "transcribe", word_timestamps: bool = False) -> int:
    """Dynamic ZeroGPU duration: turbo is fast, but leave a safety margin."""
    seconds = _audio_duration_seconds(audio_path)
    if seconds is None:
        return 120
    estimate = 25 + seconds * 0.08
    if word_timestamps:
        estimate *= 1.3
    return int(max(30, min(300, estimate)))


@gpu(duration=estimate_whisper_duration)
def transcribe(audio_path: str, language: str | None = None,
               task: str = "transcribe", word_timestamps: bool = False) -> dict:
    """Run Whisper on a 16 kHz mono WAV. Returns the raw pipeline result."""
    import numpy as np
    import soundfile as sf

    # In a ZeroGPU fork the pipeline is inherited from the main process; the
    # fallback only triggers in local dev when transcribe() is called directly.
    pipe = _pipe if _pipe is not None else _get_pipeline(_current_model_id())
    audio, sr = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if not np.isfinite(audio).all():
        audio = np.nan_to_num(audio)

    generate_kwargs: dict = {"task": task}
    if language:
        generate_kwargs["language"] = language
    result = pipe(
        {"raw": audio, "sampling_rate": sr},
        chunk_length_s=30,
        batch_size=8,
        return_timestamps="word" if word_timestamps else True,
        generate_kwargs=generate_kwargs,
    )
    return {"text": result.get("text", ""), "chunks": result.get("chunks") or []}


# transcribe() is decorated, so it cannot close over settings; the model id is
# supplied by run() through this module-level indirection.
_MODEL_ID = {"value": "openai/whisper-large-v3-turbo"}


def _current_model_id() -> str:
    return _MODEL_ID["value"]


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    _MODEL_ID["value"] = ctx.gpu_settings.whisper_model

    language_label = str(params.get("language") or "Auto")
    if language_label not in LANGUAGES:
        raise OperationError(f"Unsupported language: {language_label}")
    task_label = str(params.get("task") or "Transcribe")
    task = TASKS.get(task_label)
    if task is None:
        raise OperationError(f"Unsupported task: {task_label}")
    word_timestamps = str(params.get("timestamps") or "Segment") == "Word-level"

    if ctx.media_info is not None and not ctx.media_info.has_audio:
        raise OperationError("This file has no audio track to transcribe.")

    ctx.report(2.0, "Extracting audio")
    audio_path = extract_audio(ctx, inputs[0], ctx.work_dir / "audio_16k.wav",
                               sample_rate=16000, channels=1,
                               progress_span=(2.0, 12.0))

    ctx.check_cancelled()
    # Load in the main process so the ZeroGPU fork inherits the model.
    _get_pipeline(ctx.gpu_settings.whisper_model)
    ctx.report(15.0, "Transcribing on GPU (ZeroGPU quota is used)")
    result = transcribe(str(audio_path), LANGUAGES[language_label], task, word_timestamps)

    ctx.check_cancelled()
    ctx.report(90.0, "Writing transcript files")
    total_duration = ctx.media_info.duration_seconds if ctx.media_info else None
    if word_timestamps:
        cues = group_words_to_cues(normalize_chunks(result, total_duration))
    else:
        cues = normalize_chunks(result, total_duration)
    text = (result.get("text") or "").strip()
    if not text and not cues:
        raise OperationError("No speech was detected in this file.")

    original = ctx.media_info.filename if ctx.media_info else inputs[0].name
    out = ctx.out_dir
    txt = write_txt(out / output_name(original, "transcript", "txt"), text)
    srt = write_srt(out / output_name(original, "subtitles", "srt"), cues)
    vtt = write_vtt(out / output_name(original, "subtitles", "vtt"), cues)
    js = write_json(out / output_name(original, "transcript", "json"), {
        "text": text,
        "language": language_label,
        "task": task,
        "timestamps": "word" if word_timestamps else "segment",
        "segments": cues,
    })

    ctx.report(96.0, "Storing results")
    return OperationResult(
        outputs=[
            ProducedOutput(txt, txt.name, "txt"),
            ProducedOutput(srt, srt.name, "srt"),
            ProducedOutput(vtt, vtt.name, "vtt"),
            ProducedOutput(js, js.name, "json"),
        ],
        parameters={
            "model": ctx.gpu_settings.whisper_model,
            "language": language_label,
            "task": task_label,
            "timestamps": "word" if word_timestamps else "segment",
        },
        summary={"Segments": len(cues), "Words": len(text.split())},
    )
