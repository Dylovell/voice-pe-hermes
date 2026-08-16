"""
Main WebSocket server for voice-pe-hermes.

Receives raw PCM audio from a Voice PE device over WebSocket, runs
STT → LLM → TTS, and streams audio back.  Supports barge-in interruption
and pluggable TTS backends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
import sys
from enum import IntEnum, auto

import numpy as np

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from barge_in import BargeInDetector
from config import ServerConfig, config
from llm import LLMClient
from stt import STTEngine
from tts import TTSBackend, create_tts_backend

# ── Logging ────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)


logger = logging.getLogger("server")


# ── State machine ──────────────────────────────────────────────────────


class ConnectionState(IntEnum):
    """Voice connection state machine."""

    IDLE = 0
    LISTENING = auto()  # receiving audio from device
    PROCESSING = auto()  # STT + LLM running
    SPEAKING = auto()  # TTS audio streaming to device


# ── Voice connection handler ───────────────────────────────────────────


class VoiceConnection:
    """Manages a single WebSocket connection from a Voice PE device.

    Implements the protocol state machine and the audio pipeline.
    """

    def __init__(
        self,
        websocket: ServerConnection,
        cfg: ServerConfig,
        stt: STTEngine,
        llm: LLMClient,
        tts: TTSBackend,
    ) -> None:
        self.ws = websocket
        self.cfg = cfg
        self.stt = stt
        self.llm = llm
        self.tts = tts

        self.state = ConnectionState.IDLE
        self.audio_buffer = bytearray()
        self.sample_rate_in = cfg.sample_rate_in
        self.sample_rate_out = cfg.sample_rate_out
        self.barge_in = BargeInDetector(
            threshold=cfg.barge_in_threshold,
            min_consecutive_frames=cfg.barge_in_min_frames,
        )
        self.conversation_history: list[dict[str, str]] = []

        # Track timing for metrics
        self._utterance_start_time: float = 0.0
        self._stats = {"utterances": 0, "total_audio_sec": 0.0}

    async def run(self) -> None:
        """Main loop: read messages from the WebSocket until disconnect.

        If the device is in LISTENING state and no data arrives for
        SILENCE_TIMEOUT seconds, we auto-process the buffered audio.
        This tolerates devices that never send an explicit utterance_end.
        """
        SILENCE_TIMEOUT = 3.0  # seconds
        peer = self.ws.remote_address
        logger.info("Device connected: %s", peer)

        try:
            while True:
                try:
                    raw_message = await asyncio.wait_for(
                        self.ws.recv(), timeout=SILENCE_TIMEOUT
                    )
                    if isinstance(raw_message, bytes):
                        await self._on_audio(raw_message)
                    elif isinstance(raw_message, str):
                        await self._on_json(raw_message)
                except asyncio.TimeoutError:
                    if self.state == ConnectionState.LISTENING:
                        logger.info(
                            "Server silence timeout (%ds) — "
                            "auto-processing utterance",
                            SILENCE_TIMEOUT,
                        )
                        await self._on_utterance_end()
        except ConnectionClosed:
            logger.info("Device disconnected: %s", peer)
        except Exception:
            logger.exception("Unexpected error in connection loop")
        finally:
            self._log_stats()

    async def _on_json(self, text: str) -> None:
        """Handle a JSON control message from the device."""
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from device: %.80s", text)
            return

        msg_type = msg.get("type", "")

        if msg_type == "utterance_start":
            await self._on_utterance_start()
        elif msg_type == "utterance_end":
            await self._on_utterance_end()
        elif msg_type == "ping":
            await self._send_json({"type": "pong"})
        else:
            logger.debug("Unknown message type: %s", msg_type)

    async def _on_utterance_start(self) -> None:
        """Wake word detected / device is about to start streaming audio."""
        if self.state == ConnectionState.SPEAKING:
            # Barge-in: user spoke while we were playing TTS
            logger.info("Barge-in detected! Cutting TTS playback.")
            self.audio_buffer.clear()
            self.barge_in.reset()

        self.state = ConnectionState.LISTENING
        self._utterance_start_time = time.monotonic()
        logger.info("Utterance started")

    async def _on_utterance_end(self) -> None:
        """Device has stopped streaming (VAD silence detected)."""
        logger.debug("utterance_end received in state %s", self.state.name)
        if self.state != ConnectionState.LISTENING:
            logger.debug(
                "Ignoring utterance_end in state %s", self.state.name
            )
            return

        self.state = ConnectionState.PROCESSING
        duration = time.monotonic() - self._utterance_start_time
        self._stats["utterances"] += 1
        self._stats["total_audio_sec"] += duration

        audio_data = bytes(self.audio_buffer)
        self.audio_buffer.clear()

        # The ESP32 sends int16 PCM, but faster-whisper expects float32.
        # Convert on the server side (numpy imported at module level).
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        audio_np /= 32768.0
        max_val = float(np.max(np.abs(audio_np))) if len(audio_np) else 0
        nan_count = int(np.sum(np.isnan(audio_np)))
        logger.debug(
            "Audio conversion: %d int16 samples → %d float32 samples, "
            "max=%.4f, nan=%d",
            len(audio_np), len(audio_np),
            max_val, nan_count,
        )
        audio_data = audio_np.tobytes()

        audio_sec = len(audio_data) / (
            self.sample_rate_in * 4
        )  # 4 bytes per float32 sample
        logger.info(
            "Utterance ended (%.1f seconds of audio, %d bytes)",
            audio_sec,
            len(audio_data),
        )

        if len(audio_data) < self.sample_rate_in * 4 * 0.3:
            # Less than 300ms of audio — probably a false trigger
            logger.debug("Audio too short, ignoring")
            self.state = ConnectionState.IDLE
            return

        try:
            # 1. STT
            logger.info("Starting transcription...")
            text = await self.stt.transcribe(
                audio_data, sample_rate=self.sample_rate_in
            )
            if not text:
                logger.info("No speech detected")
                self.state = ConnectionState.IDLE
                return

            logger.info("User said: %s", text)

            # 2. LLM
            logger.info("Starting LLM processing...")
            response = await self.llm.process(
                text,
                conversation_history=list(self.conversation_history),
            )
            logger.info("LLM response: %s", response[:300])

            # Update conversation history (keep last 10 turns)
            self.conversation_history.append(
                {"role": "user", "content": text}
            )
            self.conversation_history.append(
                {"role": "assistant", "content": response}
            )
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            # 3. TTS
            logger.info("Generating TTS...")
            await self._send_json({"type": "speaking_start"})
            self.state = ConnectionState.SPEAKING

            tts_audio = await self.tts.synthesize(response)
            if tts_audio:
                # Stream TTS audio in chunks
                await self._stream_tts(tts_audio)
            else:
                logger.warning("TTS produced no audio")

        except Exception as exc:
            logger.exception("Pipeline error: %s", exc)
            await self._send_json({"type": "error", "message": str(exc)})
        finally:
            if self.state == ConnectionState.SPEAKING:
                await self._send_json({"type": "speaking_end"})
            self.state = ConnectionState.IDLE

    async def _on_audio(self, data: bytes) -> None:
        """Handle a binary audio chunk from the device."""
        if self.state == ConnectionState.SPEAKING:
            # Check for barge-in during TTS playback
            if self.barge_in.is_speech(data):
                logger.info("Barge-in speech detected during playback")
                self.state = ConnectionState.LISTENING
                self.audio_buffer.clear()
                self._utterance_start_time = time.monotonic()
                self.audio_buffer.extend(data)
                return

        # AUTO-LISTEN: If audio arrives in IDLE state, enter LISTENING state
        # immediately and buffer the data.  This is a self-healing fallback
        # for when the device's utterance_start TEXT message is delayed,
        # lost, or reordered relative to binary audio frames.  Without this,
        # audio received in IDLE is silently discarded and the server would
        # wait forever for a control message that never comes.
        if self.state == ConnectionState.IDLE:
            logger.info(
                "Audio received in IDLE — auto-entering LISTENING "
                "(%d byte chunk)",
                len(data),
            )
            self.state = ConnectionState.LISTENING
            self._utterance_start_time = time.monotonic()
            self.audio_buffer.extend(data)
            return

        if self.state == ConnectionState.LISTENING:
            self.audio_buffer.extend(data)

    async def _stream_tts(self, pcm_data: bytes) -> None:
        """Stream TTS audio chunks to the device, respecting barge-in."""
        chunk_size = self.sample_rate_out * 2 * 2  # ~200ms of audio
        offset = 0

        while offset < len(pcm_data):
            if self.state != ConnectionState.SPEAKING:
                # Barge-in was triggered, stop sending
                logger.debug("TTS stream interrupted by barge-in")
                return

            chunk = pcm_data[offset : offset + chunk_size]
            if not chunk:
                break

            await self.ws.send(chunk)
            offset += chunk_size

            # Brief yield to allow incoming messages to be processed
            await asyncio.sleep(0)

    async def _send_json(self, obj: dict) -> None:
        """Send a JSON message to the device."""
        try:
            await self.ws.send(json.dumps(obj))
        except ConnectionClosed:
            pass

    def _log_stats(self) -> None:
        """Log connection statistics."""
        s = self._stats
        if s["utterances"] > 0:
            avg = s["total_audio_sec"] / s["utterances"]
            logger.info(
                "Connection stats: %d utterances, "
                "%.1f avg audio seconds",
                s["utterances"],
                avg,
            )


# ── Server ─────────────────────────────────────────────────────────────


class VoiceServer:
    """WebSocket server for Voice PE connections.

    Manages the lifecycle of the server and dispatches connections to
    VoiceConnection handlers.  All heavyweight resources (STT, LLM, TTS)
    are shared across connections.
    """

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.stt = STTEngine()
        self.llm = LLMClient()
        self.tts = create_tts_backend(cfg.tts_backend)
        self._server = None

    async def start(self) -> None:
        """Start the WebSocket server."""
        logger.info(
            "Starting Voice PE server on %s:%d",
            self.cfg.host,
            self.cfg.port,
        )
        logger.info(
            "STT: model=%s, LLM: %s @ %s, TTS: %s",
            self.cfg.stt_model,
            self.cfg.llm_model,
            self.cfg.llm_base_url,
            self.cfg.tts_backend,
        )

        # Pre-load STT model
        await self.stt.load()

        self._server = await serve(
            self._handle_connection,
            self.cfg.host,
            self.cfg.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**22,  # 4 MB max message for long audio
        )

        logger.info("Server running. Press Ctrl+C to stop.")

        # Keep running until signalled
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received")
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows
                pass

        await stop_event.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully shut down the server."""
        logger.info("Shutting down...")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Server stopped")

    async def _handle_connection(
        self, websocket: ServerConnection
    ) -> None:
        """Handle a new WebSocket connection."""
        conn = VoiceConnection(
            websocket, self.cfg, self.stt, self.llm, self.tts
        )
        await conn.run()


# ── Entry point ────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point."""
    import argparse

    import os

    parser = argparse.ArgumentParser(
        description="Voice PE → Hermes WebSocket server",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: env HOST or 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: env PORT or 8765)",
    )
    parser.add_argument(
        "--tts",
        choices=["piper", "edge", "openai"],
        default=None,
        help="TTS backend (default: env TTS_BACKEND or edge)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=None,
        help="Enable debug logging",
    )
    parser.add_argument(
        "--stt-model",
        default=None,
        help="faster-whisper model size (default: base)",
    )

    args = parser.parse_args()

    # Override config from CLI args
    if args.host:
        os.environ["HOST"] = args.host
    if args.port:
        os.environ["PORT"] = str(args.port)
    if args.tts:
        os.environ["TTS_BACKEND"] = args.tts
    if args.verbose:
        os.environ["VERBOSE"] = "true"
    if args.stt_model:
        os.environ["STT_MODEL"] = args.stt_model

    cfg = ServerConfig()
    _setup_logging(cfg.verbose)

    server = VoiceServer(cfg)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
