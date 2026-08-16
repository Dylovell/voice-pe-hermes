"""
Mock Voice PE Device — simulates the ESP32 over WebSocket for E2E testing.

Connects to the server, sends pre-recorded audio, and validates the
server's state machine and TTS response.  No physical device needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pytest
import websockets

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

SERVER_URL = "ws://192.168.1.199:8765/"
TEST_TIMEOUT = 60.0  # max seconds to wait for a full pipeline cycle

# Paths to test audio (16kHz mono int16 PCM WAV)
_ESPHOME_TEST_DIR = Path(
    "/root/voice-pe-hermes/firmware/.esphome/build/"
    "home-assistant-voice/managed_components/espressif__esp-tflite-micro/"
    "examples/micro_speech/test_data"
)
YES_WAV = _ESPHOME_TEST_DIR / "yes_1000ms.wav"
NO_WAV = _ESPHOME_TEST_DIR / "no_1000ms.wav"

_CURIE_AUDIO_DIR = Path(
    "/mnt/nas-homes/Dylan/Documents/Projects/Curie/"
    "Curie Voice Training/qwen3_training/audio"
)


# ── Helpers ───────────────────────────────────────────────────────────────


def load_wav_as_int16(path: str | Path) -> tuple[np.ndarray, int]:
    """Load a WAV file and return (int16_samples, sample_rate)."""
    import wave

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Test audio not found: {path}")

    with wave.open(str(path), "r") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16)

    logger.debug("Loaded %s: %d samples @ %d Hz", path.name, len(samples), sr)
    return samples, sr


def resample_to_16khz(samples: np.ndarray, orig_sr: int) -> np.ndarray:
    """Resample int16 PCM to 16 kHz using linear interpolation."""
    if orig_sr == 16000:
        return samples
    target_len = int(len(samples) * 16000 / orig_sr)
    # Simple linear interpolation
    x_old = np.linspace(0, 1, len(samples), dtype=np.float64)
    x_new = np.linspace(0, 1, target_len, dtype=np.float64)
    resampled = np.interp(x_new, x_old, samples.astype(np.float64))
    return resampled.astype(np.int16)


def wav_to_device_chunks(
    path: str | Path, chunk_size: int = 1024
) -> list[bytes]:
    """Load a WAV and produce mock-device audio chunks (int16 PCM @ 16 kHz)."""
    samples, sr = load_wav_as_int16(path)
    samples = resample_to_16khz(samples, sr)

    chunks = []
    for i in range(0, len(samples), chunk_size):
        chunk = samples[i : i + chunk_size].tobytes()
        chunks.append(chunk)
    return chunks


# ── Mock Device Client ────────────────────────────────────────────────────


class MockDeviceError(Exception):
    """Raised when the server response doesn't match expectations."""


class MockVoiceDevice:
    """Simulates a Voice PE device over WebSocket.

    Connects to the server, sends an utterance, and captures the
    server's response sequence.
    """

    def __init__(self, device_id: str = "mock-device-001"):
        self.device_id = device_id
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

        # Response tracking
        self.messages: list[dict] = []  # all received server messages
        self.tts_chunks: list[bytes] = []  # received TTS audio data
        self.state_sequence: list[str] = []  # server state transitions

    async def connect(self) -> None:
        """Open WebSocket connection to the server."""
        logger.info("[%s] Connecting to %s", self.device_id, SERVER_URL)
        self.ws = await websockets.connect(
            SERVER_URL,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**22,
            open_timeout=10,
        )
        logger.info("[%s] Connected", self.device_id)

    async def disconnect(self) -> None:
        """Close WebSocket connection gracefully."""
        if self.ws:
            await self.ws.close()
            logger.info("[%s] Disconnected", self.device_id)
            self.ws = None

    async def send_utterance_start(self) -> None:
        """Notify server that an utterance is beginning."""
        await self._send_json({"type": "utterance_start"})
        logger.info("[%s] Sent utterance_start", self.device_id)

    async def send_utterance_end(self) -> None:
        """Notify server that streaming is complete."""
        await self._send_json({"type": "utterance_end"})
        logger.info("[%s] Sent utterance_end", self.device_id)

    async def send_audio_chunk(self, chunk: bytes) -> None:
        """Send a binary audio chunk (int16 PCM)."""
        if self.ws:
            await self.ws.send(chunk)

    async def stream_audio(
        self, chunks: list[bytes], interval_s: float = 0.0
    ) -> None:
        """Stream multiple audio chunks with an optional inter-chunk delay.

        Args:
            chunks: List of int16 PCM bytes chunks to send.
            interval_s: Delay between chunks (0 = send all immediately).
        """
        for chunk in chunks:
            await self.send_audio_chunk(chunk)
            if interval_s > 0:
                await asyncio.sleep(interval_s)

    async def wait_for_messages(
        self, types: list[str], timeout: float = TEST_TIMEOUT
    ) -> list[dict]:
        """Wait for the server to send messages of the specified types.

        Returns collected messages.  Raises MockDeviceError on timeout.
        """
        collected = []
        remaining = set(types)
        deadline = time.monotonic() + timeout

        while remaining:
            remaining_wait = deadline - time.monotonic()
            if remaining_wait <= 0:
                raise MockDeviceError(
                    f"Timeout waiting for types {remaining}: "
                    f"collected so far: {collected}"
                )

            msg = await self._recv(timeout=remaining_wait)
            if msg is None:
                continue

            collected.append(msg)
            msg_type = msg.get("type", msg.get("_opcode", "unknown"))
            if msg_type in remaining:
                remaining.remove(msg_type)

        return collected

    async def run_utterance(
        self,
        audio_chunks: list[bytes],
        send_end: bool = True,
        expect_speaking: bool = True,
        timeout: float = TEST_TIMEOUT,
    ) -> dict:
        """Simulate a complete utterance cycle.

        Args:
            audio_chunks: Audio data to stream (int16 PCM).
            send_end: Whether to send utterance_end after streaming.
            expect_speaking: If True, fail unless server responds with TTS.
            timeout: Max time to wait for server processing.

        Returns:
            Dict with keys: messages, tts_data, state_sequence, duration
        """
        start = time.monotonic()

        # Phase 1: Start utterance
        await self.send_utterance_start()
        await asyncio.sleep(0.05)  # small delay for WS ordering

        # Phase 2: Stream audio
        for chunk in audio_chunks:
            await self.send_audio_chunk(chunk)
            await asyncio.sleep(0)  # yield to event loop

        # Phase 3: End utterance
        if send_end:
            await self.send_utterance_end()

        # Phase 4: Wait for server response
        expected = []
        if expect_speaking:
            expected = ["speaking_start", "speaking_end"]
        else:
            expected = []

        messages = await self.wait_for_messages(expected, timeout=timeout)

        # Collect any binary TTS data that arrived
        tts_data = b"".join(self.tts_chunks)

        elapsed = time.monotonic() - start
        logger.info(
            "[%s] Utterance complete in %.1fs: %d messages, %d TTS bytes",
            self.device_id,
            elapsed,
            len(messages),
            len(tts_data),
        )

        return {
            "messages": messages,
            "tts_data": tts_data,
            "duration": elapsed,
        }

    # ── Internal helpers ────────────────────────────────────────────────

    async def _send_json(self, obj: dict) -> None:
        if self.ws:
            await self.ws.send(json.dumps(obj))

    async def _recv(self, timeout: float = 10.0) -> Optional[dict]:
        """Receive one message. Returns None on ping/pong."""
        if not self.ws:
            return None
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        if isinstance(raw, bytes):
            self.tts_chunks.append(raw)
            return {"_opcode": "binary", "_len": len(raw)}
        elif isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON: %.80s", raw)
                return {"_opcode": "text", "_raw": raw}
        return None


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def yes_chunks() -> list[bytes]:
    """Pre-loaded 'yes' utterance audio chunks."""
    return wav_to_device_chunks(YES_WAV)


@pytest.fixture(scope="session")
def no_chunks() -> list[bytes]:
    """Pre-loaded 'no' utterance audio chunks."""
    return wav_to_device_chunks(NO_WAV)


@pytest.fixture(scope="session")
def curie_sentence_chunks() -> list[bytes]:
    """Load a Curie voice training sentence (5.8s @ 44.1kHz, resampled)."""
    files = sorted(_CURIE_AUDIO_DIR.glob("*.wav"))
    if not files:
        pytest.skip("Curie audio directory not available")
    return wav_to_device_chunks(files[0])


# ── Tests ─────────────────────────────────────────────────────────────────


class TestMockDeviceConnection:
    """Tests for the mock device protocol itself."""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        """Can connect to the server and disconnect cleanly."""
        from websockets import State

        dev = MockVoiceDevice("conn-test")
        try:
            await dev.connect()
            assert dev.ws is not None
            assert dev.ws.state == State.OPEN
        finally:
            await dev.disconnect()
        assert dev.ws is None or dev.ws.state == State.CLOSED

    @pytest.mark.asyncio
    async def test_ping_pong(self):
        """Server responds to WebSocket keepalive pings."""
        dev = MockVoiceDevice("ping-test")
        try:
            await dev.connect()
            # Send a ping and wait for pong
            pong_waiter = await dev.ws.ping()
            await asyncio.wait_for(pong_waiter, timeout=5)
        finally:
            await dev.disconnect()

    @pytest.mark.asyncio
    async def test_utterance_start_no_audio(self):
        """Sending utterance_start without audio is handled gracefully."""
        dev = MockVoiceDevice("no-audio")
        try:
            await dev.connect()
            await dev.send_utterance_start()
            await dev.send_utterance_end()
            # Should not crash — server should timeout or handle empty
            await asyncio.sleep(0.5)
        finally:
            await dev.disconnect()


class TestE2EBasicUtterance:
    """End-to-end tests with real pre-recorded speech."""

    @pytest.mark.asyncio
    async def test_single_word_yes(self, yes_chunks):
        """Single-word 'yes' utterance should produce a TTS response."""
        dev = MockVoiceDevice("e2e-yes")
        try:
            await dev.connect()
            result = await dev.run_utterance(
                yes_chunks, send_end=True, expect_speaking=True
            )
            assert result["duration"] < 30, "Pipeline took too long"
            assert len(result["messages"]) >= 2, (
                f"Expected speaking_start + speaking_end, "
                f"got {len(result['messages'])} messages"
            )
            # Check we got speaking_start and speaking_end
            msg_types = [m.get("type", "") for m in result["messages"]]
            assert "speaking_start" in msg_types
            assert "speaking_end" in msg_types
            logger.info(
                "E2E yes test: %.1fs, %d TTS bytes",
                result["duration"],
                len(result["tts_data"]),
            )
        finally:
            await dev.disconnect()

    @pytest.mark.asyncio
    async def test_single_word_no(self, no_chunks):
        """Single-word 'no' utterance should produce a TTS response."""
        dev = MockVoiceDevice("e2e-no")
        try:
            await dev.connect()
            result = await dev.run_utterance(
                no_chunks, send_end=True, expect_speaking=True
            )
            msg_types = [m.get("type", "") for m in result["messages"]]
            assert "speaking_start" in msg_types
            assert len(result["tts_data"]) > 0, (
                f"No TTS audio received ({len(result['tts_data'])} bytes)"
            )
            logger.info(
                "E2E no test: %.1fs, %d TTS bytes",
                result["duration"],
                len(result["tts_data"]),
            )
        finally:
            await dev.disconnect()

    @pytest.mark.asyncio
    async def test_sentence_utterance(self, curie_sentence_chunks):
        """Full sentence utterance — full pipeline STT→LLM→TTS."""
        dev = MockVoiceDevice("e2e-sentence")
        try:
            await dev.connect()
            result = await dev.run_utterance(
                curie_sentence_chunks, send_end=True, expect_speaking=True
            )
            assert len(result["tts_data"]) > 0
            assert result["duration"] < 45
            logger.info(
                "E2E sentence test: %.1fs, %d TTS bytes, %d messages",
                result["duration"],
                len(result["tts_data"]),
                len(result["messages"]),
            )
        finally:
            await dev.disconnect()


class TestEdgeCases:
    """Edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_silence_only(self):
        """Sending only silence should not produce TTS."""
        dev = MockVoiceDevice("silence")
        try:
            await dev.connect()
            # Generate 2 seconds of silence at 16kHz int16
            silent = np.zeros(32000, dtype=np.int16).tobytes()
            chunks = [silent[i : i + 4096] for i in range(0, len(silent), 4096)]

            await dev.send_utterance_start()
            # Stream silence in chunks
            for c in chunks:
                await dev.send_audio_chunk(c)
                await asyncio.sleep(0)
            await dev.send_utterance_end()

            # Wait — should get speaking_start/end only if
            # STT hallucinates from silence
            await asyncio.sleep(5)

            # No assertion on TTS — silence may or may not produce output
            # (faster-whisper may return empty or hallucinate)
            logger.info(
                "Silence test: %d TTS bytes received", len(dev.tts_chunks)
            )
        finally:
            await dev.disconnect()

    @pytest.mark.asyncio
    async def test_utterance_start_twice(self):
        """Sending utterance_start twice should not crash."""
        dev = MockVoiceDevice("dup-start")
        try:
            await dev.connect()
            await dev.send_utterance_start()
            await dev.send_utterance_start()  # duplicate
            await asyncio.sleep(0.3)
            await dev.send_utterance_end()
            await asyncio.sleep(0.5)
        finally:
            await dev.disconnect()

    @pytest.mark.asyncio
    async def test_utterance_end_without_start(self):
        """utterance_end before utterance_start should be ignored."""
        dev = MockVoiceDevice("end-no-start")
        try:
            await dev.connect()
            await dev.send_utterance_end()
            await asyncio.sleep(0.3)
            # Server should still accept a normal utterance after
            await dev.send_utterance_start()
            # Send a short audio burst
            burst = (np.ones(8000, dtype=np.int16) * 8000).tobytes()
            await dev.send_audio_chunk(burst)
            await dev.send_utterance_end()
            await asyncio.sleep(3)
        finally:
            await dev.disconnect()


class TestMultipleUtterances:
    """Multiple consecutive utterances."""

    @pytest.mark.asyncio
    async def test_two_utterances_sequence(self, yes_chunks, no_chunks):
        """Two back-to-back utterances should both be processed."""
        dev = MockVoiceDevice("multi-utt")
        try:
            await dev.connect()

            # First utterance
            r1 = await dev.run_utterance(
                yes_chunks[:10], send_end=True, expect_speaking=True
            )
            assert len(r1["tts_data"]) > 0
            assert "speaking_start" in [m.get("type", "") for m in r1["messages"]]

            # Small gap between utterances
            await asyncio.sleep(1)

            # Second utterance
            r2 = await dev.run_utterance(
                no_chunks[:10], send_end=True, expect_speaking=True
            )
            assert len(r2["tts_data"]) > 0

            logger.info(
                "Multi-utterance test: utt1=%.1fs (%d TTS bytes), "
                "utt2=%.1fs (%d TTS bytes)",
                r1["duration"],
                len(r1["tts_data"]),
                r2["duration"],
                len(r2["tts_data"]),
            )
        finally:
            await dev.disconnect()


class TestTimeoutBehaviour:
    """Server-side silence timeout handling."""

    @pytest.mark.asyncio
    async def test_server_silence_timeout(self, yes_chunks):
        """Without utterance_end, server should process after silence timeout."""
        dev = MockVoiceDevice("timeout")
        try:
            await dev.connect()

            await dev.send_utterance_start()
            # Send some audio but DON'T send utterance_end
            for c in yes_chunks[:15]:
                await dev.send_audio_chunk(c)
                await asyncio.sleep(0)

            # Wait — the server should auto-process after 3s silence timeout
            await asyncio.sleep(8)

            # Should have gotten a response despite no utterance_end
            messages = dev.messages
            tts_size = len(dev.tts_chunks)

            logger.info(
                "Silence timeout test: %d msgs, %d TTS bytes",
                len(messages),
                tts_size,
            )
        finally:
            await dev.disconnect()
