"""Stem separation with Demucs v4 (htdemucs).

Pipeline: FFmpeg audio extraction (CPU) -> Demucs inference (GPU) ->
per-stem format conversion + optional ZIP (CPU) -> bucket upload (CPU).

Demucs is an isolated, optional dependency (PLAN.md section 48): the whole app
keeps working with ENABLE_DEMUCS=false, and all demucs imports happen inside
functions so a dependency conflict can never break the other tools.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from core.filenames import output_name

from gpu.backend.config import gpu
from gpu.backend.job_manager import JobContext, OperationError, OperationResult, ProducedOutput
from gpu.backend.postprocessing import convert_audio, zip_outputs
from gpu.backend.preprocessing import extract_audio

log = logging.getLogger(__name__)

MODES = ("Vocals + Instrumental", "4 Stem")
FORMATS = ("WAV", "FLAC", "MP3")

_model = None
_model_lock = threading.Lock()


def _get_model(name: str):
    """Load (once) a CPU-resident Demucs model in the main process.

    Must run OUTSIDE @spaces.GPU functions (ZeroGPU forks are throwaway);
    The decorated fork moves its inherited copy to the real GPU.
    """
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        from demucs.pretrained import get_model

        log.info("loading Demucs model %s", name)
        _model = get_model(name)
        _model.eval()
        return _model


def estimate_demucs_duration(audio_path: str, out_dir: str = "",
                             mode: str = "Vocals + Instrumental",
                             model_name: str = "htdemucs") -> int:
    """Dynamic ZeroGPU duration with a safety margin.

    Takes the same arguments as separate() (ZeroGPU calls it with them).
    """
    try:
        import soundfile as sf

        seconds = float(sf.info(audio_path).duration)
    except Exception:
        return 180
    estimate = 40 + seconds * 0.5
    if mode == "4 Stem":
        estimate *= 1.2
    return int(max(60, min(600, estimate)))


@gpu(duration=estimate_demucs_duration)
def separate(audio_path: str, out_dir: str, mode: str = "Vocals + Instrumental",
             model_name: str = "htdemucs") -> dict[str, str]:
    """Separate stems from a WAV file. Returns {stem_name: wav_path}."""
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile

    # In a ZeroGPU fork the model is inherited from the main process; the
    # fallback only triggers in local dev when separate() is called directly.
    model = _model if _model is not None else _get_model(model_name)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    wav = AudioFile(audio_path).read(
        streams=0, samplerate=model.samplerate, channels=model.audio_channels
    )
    original = wav.clone()
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / ref.std()

    with torch.no_grad():
        sources = apply_model(
            model, wav[None], device=device, shifts=1, split=True,
            overlap=0.25, progress=False,
        )[0]
    sources = sources * ref.std() + ref.mean()

    stems: dict[str, object] = {}
    source_names = list(model.sources)
    if mode == "Vocals + Instrumental":
        vocals = sources[source_names.index("vocals")]
        stems["vocals"] = vocals
        stems["instrumental"] = original - vocals
    else:
        for name, stem in zip(source_names, sources):
            stems[name] = stem

    import soundfile as sf

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, stem in stems.items():
        path = out / f"{name}.wav"
        data = stem.detach().cpu().numpy().transpose(1, 0)  # (samples, channels)
        sf.write(str(path), data, model.samplerate, subtype="PCM_16")
        written[name] = str(path)
    return written


def run(ctx: JobContext, inputs: list[Path], params: dict) -> OperationResult:
    if not ctx.gpu_settings.enable_demucs:
        raise OperationError("Stem separation is disabled on this deployment (ENABLE_DEMUCS=false).")

    mode = str(params.get("mode") or "Vocals + Instrumental")
    if mode not in MODES:
        raise OperationError(f"Unsupported separation mode: {mode}")
    fmt = str(params.get("format") or "FLAC").lower()
    if fmt not in ("wav", "flac", "mp3"):
        raise OperationError(f"Unsupported output format: {fmt}")
    make_zip = bool(params.get("zip"))

    if ctx.media_info is not None and not ctx.media_info.has_audio:
        raise OperationError("This file has no audio track to separate.")

    ctx.report(2.0, "Extracting audio")
    audio_path = extract_audio(ctx, inputs[0], ctx.work_dir / "audio.wav",
                               sample_rate=44100, channels=2,
                               progress_span=(2.0, 10.0))

    ctx.check_cancelled()
    # Cache on CPU in the main process so the ZeroGPU fork inherits the model.
    _get_model(ctx.gpu_settings.demucs_model)
    ctx.report(12.0, "Separating stems on GPU (ZeroGPU quota is used)")
    stems_dir = ctx.work_dir / "stems"
    stems = separate(str(audio_path), str(stems_dir), mode, ctx.gpu_settings.demucs_model)

    ctx.check_cancelled()
    ctx.report(85.0, f"Encoding {fmt.upper()} outputs")
    original = ctx.media_info.filename if ctx.media_info else inputs[0].name
    out = ctx.out_dir
    outputs: list[ProducedOutput] = []
    produced_paths: list[Path] = []
    for name, wav_path in stems.items():
        final = out / output_name(original, name, fmt)
        converted = convert_audio(ctx, Path(wav_path), final, fmt)
        produced_paths.append(converted)
        outputs.append(ProducedOutput(converted, converted.name, name))

    if make_zip and len(produced_paths) > 1:
        zip_path = zip_outputs(produced_paths, out / output_name(original, "stems", "zip"))
        outputs.append(ProducedOutput(zip_path, zip_path.name, "zip"))

    ctx.report(96.0, "Storing results")
    return OperationResult(
        outputs=outputs,
        parameters={
            "model": ctx.gpu_settings.demucs_model,
            "mode": mode,
            "format": fmt.upper(),
        },
        summary={"Stems": len(stems)},
    )
