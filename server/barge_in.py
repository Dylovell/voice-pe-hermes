"""
Voice activity detection (VAD) for barge-in / interruption handling.

Uses a simple energy-based approach: an audio chunk is classified as speech
when its root-mean-square (RMS) energy exceeds a configurable threshold.
This is intentionally lightweight — no neural VAD model needed for a
directional microphone array that already applies its own AEC and NS.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

from config import config

logger = logging.getLogger(__name__)


class BargeInDetector:
    """Energy-based voice activity detector for interruption handling.

    Attributes:
        threshold: RMS amplitude threshold above which audio is considered
                   speech (0.0 – 1.0 for float32 samples).
        min_consecutive_frames: How many consecutive speech frames must be
                                observed before ``is_speech()`` returns True.
        _speech_frame_count: Running counter of consecutive speech frames.
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        min_consecutive_frames: Optional[int] = None,
    ) -> None:
        self.threshold = (
            threshold
            if threshold is not None
            else config.barge_in_threshold
        )
        self.min_consecutive_frames = (
            min_consecutive_frames
            if min_consecutive_frames is not None
            else config.barge_in_min_frames
        )
        self._speech_frame_count: int = 0
        self._total_frames: int = 0
        self._speech_frames: int = 0

    def _rms(self, chunk: np.ndarray) -> float:
        """Compute the root-mean-square amplitude of a numpy array."""
        if chunk.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(chunk.astype(np.float64)))))

    def is_speech(self, audio_chunk: bytes | np.ndarray) -> bool:
        """Determine whether *audio_chunk* contains speech.

        Args:
            audio_chunk: Raw PCM float32 bytes *or* a numpy float32 array.
                         If bytes are provided they are interpreted as
                         32-bit floats in little-endian order.

        Returns:
            True if the chunk contains speech according to the current
            threshold and consecutive-frame policy.
        """
        if isinstance(audio_chunk, bytes):
            chunk: np.ndarray = np.frombuffer(
                audio_chunk, dtype=np.float32
            )
        else:
            chunk = audio_chunk

        if chunk.size == 0:
            return False

        energy = self._rms(chunk)
        is_speech = energy > self.threshold

        self._total_frames += 1

        if is_speech:
            self._speech_frame_count += 1
            self._speech_frames += 1
        else:
            self._speech_frame_count = 0

        result = self._speech_frame_count >= self.min_consecutive_frames

        if result and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Barge-in: speech detected (energy=%0.4f, "
                "threshold=%0.4f, consecutive_frames=%d/%d)",
                energy,
                self.threshold,
                self._speech_frame_count,
                self.min_consecutive_frames,
            )

        return result

    def speech_ratio(self) -> float:
        """Return the fraction of frames classified as speech since reset."""
        if self._total_frames == 0:
            return 0.0
        return self._speech_frames / self._total_frames

    def reset(self) -> None:
        """Reset the consecutive-frame counter and statistics."""
        self._speech_frame_count = 0
        self._total_frames = 0
        self._speech_frames = 0
