"""
Configuration for the Voice PE WebSocket server.

All settings are loaded from environment variables with sensible defaults.
Override any value by setting the corresponding environment variable before
starting the server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


TTSBackendName = Literal["piper", "edge", "openai"]


@dataclass
class ServerConfig:
    """Immutable-style configuration container loaded from the environment."""

    # ── Network ──────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(
        default_factory=lambda: int(os.getenv("PORT", "8765"))
    )

    # ── STT (faster-whisper) ─────────────────────────────────────────────
    stt_model: str = field(
        default_factory=lambda: os.getenv("STT_MODEL", "base")
    )
    stt_device: str = field(
        default_factory=lambda: os.getenv("STT_DEVICE", "cpu")
    )
    stt_compute: str = field(
        default_factory=lambda: os.getenv("STT_COMPUTE", "int8")
    )
    sample_rate_in: int = int(os.getenv("SAMPLE_RATE_IN", "16000"))

    # ── LLM (OpenAI-compatible) ──────────────────────────────────────────
    llm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LLM_BASE_URL", "http://192.168.1.201:8888/v1"
        )
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv(
            "LLM_MODEL", "deepseek-v4-flash-abliterated"
        )
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "sk-none")
    )
    llm_system_prompt: str = field(
        default_factory=lambda: os.getenv(
            "LLM_SYSTEM_PROMPT",
            "You are Hermes, a helpful AI assistant. Answer concisely and "
            "conversationally. Keep responses under 100 words unless asked "
            "for detail.",
        )
    )
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "512"))
    )
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.7"))
    )

    # ── TTS ──────────────────────────────────────────────────────────────
    tts_backend: TTSBackendName = field(
        default_factory=lambda: os.getenv("TTS_BACKEND", "edge")  # type: ignore[assignment]
    )
    tts_voice: str = field(
        default_factory=lambda: os.getenv(
            "TTS_VOICE", "en-US-AriaNeural"
        )
    )
    tts_openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "TTS_OPENAI_BASE_URL", "http://192.168.1.201:8888/v1"
        )
    )
    tts_openai_api_key: str = field(
        default_factory=lambda: os.getenv(
            "TTS_OPENAI_API_KEY", "sk-none"
        )
    )
    tts_openai_model: str = field(
        default_factory=lambda: os.getenv("TTS_OPENAI_MODEL", "tts-1")
    )
    sample_rate_out: int = int(os.getenv("SAMPLE_RATE_OUT", "48000"))

    # ── Barge-in / VAD ───────────────────────────────────────────────────
    barge_in_threshold: float = field(
        default_factory=lambda: float(
            os.getenv("BARGE_IN_THRESHOLD", "0.02")
        )
    )
    # Minimum consecutive speech frames before triggering barge-in
    barge_in_min_frames: int = field(
        default_factory=lambda: int(
            os.getenv("BARGE_IN_MIN_FRAMES", "3")
        )
    )
    # Chunk duration in seconds for VAD analysis
    vad_chunk_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("VAD_CHUNK_SECONDS", "0.03")
        )
    )

    # ── Verbose logging ──────────────────────────────────────────────────
    verbose: bool = field(
        default_factory=lambda: os.getenv("VERBOSE", "").lower()
        in ("1", "true", "yes")
    )


# Global singleton — import this in every module that needs config.
config = ServerConfig()
