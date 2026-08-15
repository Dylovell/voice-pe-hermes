"""
Speech-to-text using faster-whisper.

Wraps the synchronous faster-whisper API in an async interface by running
transcription in a thread-pool executor to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

from config import config

logger = logging.getLogger(__name__)


class STTEngine:
    """Async wrapper around a faster-whisper model for speech transcription."""

    def __init__(self) -> None:
        self._model: Optional[WhisperModel] = None
        self._model_size: str = config.stt_model
        self._device: str = config.stt_device
        self._compute: str = config.stt_compute

    async def load(self) -> None:
        """Initialise the whisper model (lazy-loaded on first use)."""
        if self._model is not None:
            return
        loop = asyncio.get_running_loop()
        logger.info(
            "Loading faster-whisper model '%s' on %s (compute=%s) ...",
            self._model_size,
            self._device,
            self._compute,
        )
        self._model = await loop.run_in_executor(
            None,
            partial(
                WhisperModel,
                self._model_size,
                device=self._device,
                compute_type=self._compute,
            ),
        )
        logger.info("STT model loaded successfully.")

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
    ) -> str:
        """Transcribe raw PCM float32 audio to text.

        Args:
            audio_bytes: Raw PCM audio as 32-bit floats in little-endian
                         byte order.
            sample_rate: Sample rate of the input audio (Hz).

        Returns:
            Transcribed text.  Returns an empty string if no speech was
            detected.
        """
        if self._model is None:
            await self.load()

        # Narrowing: _model is guaranteed not-None after load()
        model: WhisperModel = self._model  # type: ignore[assignment]

        # Convert bytes to numpy float32 array.
        audio_np: np.ndarray = np.frombuffer(audio_bytes, dtype=np.float32).copy()

        logger.debug(
            "Transcribing %d samples (%0.1f seconds) ...",
            len(audio_np),
            len(audio_np) / sample_rate if sample_rate else 0,
        )

        # Run the synchronous transcription in a thread-pool executor.
        loop = asyncio.get_running_loop()
        segments, info = await loop.run_in_executor(
            None,
            partial(
                model.transcribe,
                audio_np,
                beam_size=5,
                language="en",
                condition_on_previous_text=False,
                vad_filter=True,  # skip silence automatically
            ),
        )

        detected_language = getattr(info, "language", "unknown")
        logger.debug(
            "Detected language: %s (probability: %0.2f)",
            detected_language,
            getattr(info, "language_probability", 0),
        )

        # Collect all segment text.
        text_parts: list[str] = []
        segment_list = list(segments)
        for segment in segment_list:
            text_parts.append(segment.text.strip())

        full_text = " ".join(text_parts).strip()
        logger.info("Transcription result (%d chars): %s", len(full_text), full_text)
        return full_text

    async def unload(self) -> None:
        """Release the model (calls del to free GPU/CPU memory)."""
        if self._model is not None:
            logger.info("Unloading STT model ...")
            del self._model
            self._model = None
