"""Faster-Whisper speech-to-text backend (local, CTranslate2-based)."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional

from openjarvis.core.registry import SpeechRegistry
from openjarvis.speech._stubs import Segment, SpeechBackend, TranscriptionResult

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment, misc]

try:
    import ctranslate2
except ImportError:
    ctranslate2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@SpeechRegistry.register("faster-whisper")
class FasterWhisperBackend(SpeechBackend):
    """Local speech-to-text using Faster-Whisper (CTranslate2)."""

    backend_id = "faster-whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Optional[WhisperModel] = None
        self._last_error: Optional[str] = None

    def _resolve_compute_type(self) -> str:
        """Pick a CTranslate2 compute type supported by the configured device."""
        if ctranslate2 is None:
            return self._compute_type

        try:
            supported = set(ctranslate2.get_supported_compute_types(self._device))
        except Exception as exc:
            logger.debug(
                "Could not inspect CTranslate2 compute types for %s: %s",
                self._device,
                exc,
            )
            return self._compute_type

        if self._compute_type in supported:
            return self._compute_type

        preferences = (
            ("int8", "float32", "int8_float32", "int16")
            if self._compute_type == "float16"
            else ("float32", "int8", "int8_float32", "int16")
        )
        fallback = next((value for value in preferences if value in supported), None)
        if fallback is None:
            return self._compute_type

        logger.warning(
            "CTranslate2 compute_type=%r is not supported on device=%r; "
            "using %r instead",
            self._compute_type,
            self._device,
            fallback,
        )
        return fallback

    def _ensure_model(self) -> WhisperModel:
        """Lazy-load the Whisper model on first use."""
        if self._model is None:
            if WhisperModel is None:
                self._last_error = (
                    "faster-whisper is not installed. "
                    "Install with: uv sync --extra desktop"
                )
                raise ImportError(self._last_error)
            compute_type = self._resolve_compute_type()
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=compute_type,
            )
        self._last_error = None
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using Faster-Whisper."""
        try:
            model = self._ensure_model()

            # Write audio to a temp file (faster-whisper needs a file path).
            # delete=False + manual unlink: on Windows an open
            # NamedTemporaryFile holds an exclusive handle, so PyAV's reopen
            # of tmp.name inside model.transcribe() fails with EACCES.
            suffix = f".{format}" if not format.startswith(".") else format
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                with tmp:
                    tmp.write(audio)

                kwargs = {
                    # Strip non-speech audio (silence, hum, background noise)
                    # with Silero VAD before decoding. Without this, feeding
                    # near-silent/noisy clips to Whisper makes the decoder
                    # loop on a repeated word/phrase ("o que o que o que...").
                    "vad_filter": True,
                    "vad_parameters": {
                        "min_silence_duration_ms": 500,
                        # Default 0.5 lets brief noise blips (clicks, hum,
                        # fan) through as "speech". Raised, plus a minimum
                        # continuous-speech duration, so short noise doesn't
                        # reach the decoder and get hallucinated into words.
                        "threshold": 0.6,
                        "min_speech_duration_ms": 250,
                    },
                    # Default True re-feeds each segment's text as context for
                    # the next, which compounds a repetition loop once one
                    # starts. Segments here are short single utterances, not
                    # a long lecture, so cross-segment context isn't needed.
                    "condition_on_previous_text": False,
                }
                if language:
                    kwargs["language"] = language

                segments_iter, info = model.transcribe(tmp.name, **kwargs)
                segments_list = list(segments_iter)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError as unlink_exc:
                    logger.debug(
                        "Could not remove temp audio file %s: %s",
                        tmp.name,
                        unlink_exc,
                    )
        except Exception as exc:
            self._last_error = str(exc)
            raise

        # If VAD found almost no actual speech in the clip, the decoder is
        # running on scraps of noise -- whatever text it produced is a
        # hallucination, not a transcription. Drop it rather than act on it.
        duration_after_vad = getattr(info, "duration_after_vad", None)
        if duration_after_vad is not None and duration_after_vad < 0.3:
            segments_list = []

        # Build result
        text = "".join(seg.text for seg in segments_list).strip()
        segments = [
            Segment(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                confidence=None,
            )
            for seg in segments_list
        ]

        self._last_error = None
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            confidence=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", 0.0),
            segments=segments,
        )

    def health(self) -> bool:
        """Check if model is loaded or loadable."""
        try:
            self._ensure_model()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.debug("Faster-Whisper health check failed: %s", exc)
            return False

    def last_error(self) -> Optional[str]:
        """Return the last model load or transcription error, if any."""
        return self._last_error

    def supported_formats(self) -> List[str]:
        """Supported audio formats (same as ffmpeg/Whisper)."""
        return ["wav", "mp3", "m4a", "ogg", "flac", "webm"]
