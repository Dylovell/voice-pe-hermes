"""Tests for barge-in / voice activity detection."""

from __future__ import annotations

import math

import numpy as np
import pytest

# Make config overridable during tests
import config as config_module


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Override config defaults for deterministic tests."""
    # We need to reset the module-level singleton
    # Use a lightweight override by patching the module
    class TestConfig:
        barge_in_threshold = 0.02
        barge_in_min_frames = 3
        vad_chunk_seconds = 0.03

    monkeypatch.setattr(config_module, "config", TestConfig())
    yield


from barge_in import BargeInDetector


# ── Helpers ──────────────────────────────────────────────────────────────


def _sine(freq_hz: float, duration_s: float, sample_rate: int = 16000,
          amplitude: float = 1.0) -> np.ndarray:
    """Generate a pure sine tone as float32 samples."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def _silence(duration_s: float, sample_rate: int = 16000) -> bytes:
    """Generate silence as float32 PCM bytes."""
    return np.zeros(int(sample_rate * duration_s), dtype=np.float32).tobytes()


def _noise(duration_s: float, sample_rate: int = 16000,
           amplitude: float = 1.0) -> bytes:
    """Generate white noise as float32 PCM bytes."""
    return (amplitude * np.random.randn(int(sample_rate * duration_s)).astype(np.float32)).tobytes()


# ── Construction ─────────────────────────────────────────────────────────


class TestConstruction:
    """BargeInDetector construction and default parameter handling."""

    def test_default_params(self):
        """Uses config defaults when no args provided."""
        d = BargeInDetector()
        assert d.threshold == 0.02
        assert d.min_consecutive_frames == 3

    def test_custom_params(self):
        """Custom parameters override config defaults."""
        d = BargeInDetector(threshold=0.5, min_consecutive_frames=5)
        assert d.threshold == 0.5
        assert d.min_consecutive_frames == 5

    def test_zero_threshold(self):
        """Zero threshold means every non-zero sample is speech."""
        d = BargeInDetector(threshold=0.0, min_consecutive_frames=1)
        chunk = np.array([0.0, 0.001, 0.0], dtype=np.float32)
        assert d.is_speech(chunk.tobytes()) is True


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary and edge-case behaviour."""

    def test_empty_input(self):
        """Empty audio returns False, not crash."""
        d = BargeInDetector()
        assert d.is_speech(b"") is False

    def test_single_sample_silence(self):
        """Single zero sample is not speech."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        chunk = np.zeros(1, dtype=np.float32)
        assert d.is_speech(chunk.tobytes()) is False

    def test_single_sample_loud(self):
        """Single loud sample with min_frames=1 triggers speech."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        chunk = np.array([0.5], dtype=np.float32)
        assert d.is_speech(chunk.tobytes()) is True

    def test_numpy_array_input(self):
        """Accepts numpy arrays directly (not just bytes)."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        chunk = np.array([0.5], dtype=np.float32)
        assert d.is_speech(chunk) is True

    def test_all_zeros(self):
        """Completely silent audio never triggers."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)
        for _ in range(10):
            assert d.is_speech(_silence(0.03)) is False


# ── Threshold behaviour ─────────────────────────────────────────────────


class TestThreshold:
    """Speech detection threshold behaviour."""

    def test_below_threshold_never_triggers(self):
        """Audio slightly below threshold never counts as speech."""
        d = BargeInDetector(threshold=0.1, min_consecutive_frames=3)
        # RMS = 0.07 < 0.1 — should be silence
        chunk = _sine(440, 0.03, amplitude=0.1)
        for _ in range(10):
            assert d.is_speech(chunk) is False

    def test_above_threshold_triggers_after_min_frames(self):
        """Audio above threshold triggers only after min_consecutive_frames."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)
        loud = _sine(440, 0.03, amplitude=0.5)

        # First two frames: not yet triggered
        assert d.is_speech(loud) is False  # count=1
        assert d.is_speech(loud) is False  # count=2
        # Third frame: triggers
        assert d.is_speech(loud) is True   # count=3

    def test_frame_counter_resets_on_silence(self):
        """Consecutive speech counter resets when a silent frame arrives."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)
        loud = _sine(440, 0.03, amplitude=0.5)
        silent = _silence(0.03)

        # Build up counter
        assert d.is_speech(loud) is False    # count=1
        assert d.is_speech(loud) is False    # count=2
        # Silence resets counter
        assert d.is_speech(silent) is False  # count=0
        # Need 3 more loud frames to trigger again
        assert d.is_speech(loud) is False    # count=1
        assert d.is_speech(loud) is False    # count=2
        assert d.is_speech(loud) is True     # count=3

    def test_exactly_at_threshold(self):
        """RMS exactly equal to threshold is NOT speech (strict >, not >=)."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        # RMS = sqrt(mean(samples^2)). For constant 0.02, RMS = 0.02 exactly
        chunk = np.full(160, 0.02, dtype=np.float32)
        # The _rms method uses strict > threshold, so exact equality is not speech
        assert d.is_speech(chunk) is False


# ── Reset behaviour ─────────────────────────────────────────────────────


class TestReset:
    """State reset between utterances."""

    def test_reset_clears_counters(self):
        """reset() zeros consecutive-frame counter, stats start fresh."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=2)
        loud = _sine(440, 0.03, amplitude=0.5)

        d.is_speech(loud)  # count=1
        d.is_speech(loud)  # count=2 → True
        assert d.speech_ratio() > 0

        d.reset()
        # speech_ratio is 0 after reset (no frames processed)
        assert d.speech_ratio() == 0.0

        # First frame after reset starts fresh: count=1, needs 2
        assert d.is_speech(loud) is False  # count=1
        # Ratio: 1 speech frame out of 1 total
        assert d.speech_ratio() == 1.0

    def test_reset_stats(self):
        """speech_ratio returns 0 after reset."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        loud = _sine(440, 0.03, amplitude=0.5)

        for _ in range(5):
            d.is_speech(loud)
        assert d.speech_ratio() == 1.0

        d.reset()
        assert d.speech_ratio() == 0.0


# ── speech_ratio ────────────────────────────────────────────────────────


class TestSpeechRatio:
    """Speech ratio statistics."""

    def test_ratio_zero_when_no_data(self):
        """speech_ratio is 0.0 before any frames processed."""
        d = BargeInDetector()
        assert d.speech_ratio() == 0.0

    def test_ratio_after_mixed_frames(self):
        """speech_ratio reflects actual proportion of speech frames."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        loud = _sine(440, 0.03, amplitude=0.5)
        silent = _silence(0.03)

        d.is_speech(loud)     # speech frame
        d.is_speech(silent)   # silent frame
        d.is_speech(loud)     # speech frame

        # 2 speech out of 3 total
        assert d.speech_ratio() == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_ratio_handles_single_frame(self):
        """speech_ratio handles a single frame correctly."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=1)
        assert d.speech_ratio() == 0.0
        d.is_speech(_sine(440, 0.03, amplitude=0.5))
        assert d.speech_ratio() == 1.0


# ── Real-world audio edge cases ─────────────────────────────────────────


class TestRealWorld:
    """Tests with realistic audio scenarios."""

    def test_sudden_loud_noise_triggers_barge_in(self):
        """A sudden burst of loud audio should trigger barge-in."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)

        # 5 seconds of silence
        for _ in range(50):
            assert d.is_speech(_silence(0.1)) is False

        # Sudden loud burst
        burst = _sine(1000, 0.05, amplitude=0.8)
        assert d.is_speech(burst) is False     # frame 1
        assert d.is_speech(burst) is False     # frame 2
        assert d.is_speech(burst) is True      # frame 3 — barge-in triggered!

    def test_quiet_music_not_barge_in(self):
        """Low-level background audio should not trigger barge-in."""
        d = BargeInDetector(threshold=0.5, min_consecutive_frames=3)
        quiet = _sine(440, 0.03, amplitude=0.3)  # RMS ≈ 0.21

        for _ in range(10):
            assert d.is_speech(quiet) is False

    def test_white_noise_triggers_if_loud_enough(self):
        """Loud white noise should trigger barge-in."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)
        loud_noise = _noise(0.1, amplitude=0.5)

        assert d.is_speech(loud_noise) is False  # frame 1
        assert d.is_speech(loud_noise) is False  # frame 2
        assert d.is_speech(loud_noise) is True   # frame 3

    def test_varying_amplitude(self):
        """Amplitude fading in and out — should only trigger during loud parts."""
        d = BargeInDetector(threshold=0.1, min_consecutive_frames=2)
        sample_rate = 16000

        # Generate audio that ramps up then down
        t = np.linspace(0, 1.0, sample_rate)
        envelope = np.clip(4 * t * (1 - t), 0, 1)  # triangle envelope
        audio = (envelope * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        chunk_size = sample_rate // 33  # ~30ms chunks
        triggered = False
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i + chunk_size]
            if len(chunk) < chunk_size // 2:
                break
            result = d.is_speech(chunk)
            if result:
                triggered = True

        # Should have triggered somewhere in the middle (loudest part)
        assert triggered, (
            "Barge-in should trigger during the loud mid-section "
            "but never did"
        )


# ── Performance sanity ──────────────────────────────────────────────────


class TestPerformance:
    """Basic performance guard — detection is meant to be lightweight."""

    def test_benchmark_real_time_factor(self, benchmark=False):
        """Detection should be << 1ms per frame."""
        d = BargeInDetector(threshold=0.02, min_consecutive_frames=3)
        chunk = _sine(440, 0.03, amplitude=0.5).tobytes()
        _ = d.is_speech(chunk)  # warmup

        import time
        start = time.perf_counter()
        for _ in range(100):
            d.is_speech(chunk)
        elapsed = time.perf_counter() - start

        # 100 frames should take under 10ms (0.1ms per frame)
        assert elapsed < 0.01, (
            f"Detection too slow: {elapsed:.4f}s for 100 frames "
            f"({elapsed/100*1000:.3f}ms/frame)"
        )
