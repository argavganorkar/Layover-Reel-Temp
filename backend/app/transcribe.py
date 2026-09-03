"""
Phase 3: local speech-to-text transcription using faster-whisper.

Runs entirely on-device (CPU) - no API key, no per-minute cost, works
offline once the model is downloaded once. Produces both segment-level
timestamps (good context for Phase 4's clip selection) and word-level
timestamps (needed for Phase 8's caption sync).

NOTE ON MODEL DOWNLOAD: the first time a given model size is used,
faster-whisper downloads its weights from Hugging Face Hub. That needs
outbound internet access on whatever machine runs this. After that first
download it's cached locally (~/.cache/huggingface) and works offline.
"""
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from faster_whisper import WhisperModel

logger = logging.getLogger("reelmaker.transcribe")

# tiny < base < small < medium < large-v3 : bigger = more accurate, slower.
# "small" is a good accuracy/speed balance on CPU for podcast-style audio.
DEFAULT_MODEL_SIZE = "small"

_model_cache: dict[str, WhisperModel] = {}


def get_model(model_size: str = DEFAULT_MODEL_SIZE) -> WhisperModel:
    """Load (and cache) a Whisper model. int8 quantization keeps CPU RAM/time reasonable."""
    if model_size not in _model_cache:
        logger.info("Loading Whisper model '%s' (first use downloads it)...", model_size)
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_cache[model_size]


def transcribe_audio(
    audio_path: Path,
    model_size: str = DEFAULT_MODEL_SIZE,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict[str, Any]:
    """
    Transcribe an audio file and return segment + word level timestamps.

    on_progress, if given, is called with a 0-100 float as transcription
    proceeds (estimated from how far through the audio's duration we are -
    useful for showing progress on a 1-2 hour file).
    """
    model = get_model(model_size)

    segments_iter, info = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        vad_filter=True,  # skip long silences - common in podcasts/interviews
    )

    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    text_parts: list[str] = []

    duration = info.duration or 1.0  # avoid div by zero
    for seg in segments_iter:
        text = seg.text.strip()
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        text_parts.append(text)
        if seg.words:
            for w in seg.words:
                words.append(
                    {
                        "word": w.word.strip(),
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                    }
                )
        if on_progress:
            on_progress(min(99.0, round((seg.end / duration) * 100, 1)))

    if on_progress:
        on_progress(100.0)

    return {
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "duration_seconds": round(duration, 2),
        "model_size": model_size,
        "segments": segments,
        "words": words,
        "text": " ".join(text_parts).strip(),
    }
