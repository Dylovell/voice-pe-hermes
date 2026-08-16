"""
Pluggable text-to-speech backends for the Voice PE server.

Supports three backends:
- ``piper``: Local Piper TTS via subprocess (CPU, fast, fully offline).
- ``edge``: Microsoft Edge TTS via the ``edge-tts`` library (free, network).
- ``openai``: Any OpenAI-compatible TTS endpoint (for self-hosted high-quality
  TTS or cloud providers like ElevenLabs / OpenAI).

All backends return 48 kHz 16-bit signed PCM audio as raw bytes.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import struct
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from config import TTSBackendName, config

logger = logging.getLogger(__name__)

# ── Abstract base ──────────────────────────────────────────────────────


class TTSBackend(ABC):
    """Abstract base for all TTS backends."""

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesise *text* and return 48 kHz 16-bit signed PCM audio.

        Args:
            text: The text to speak.

        Returns:
            Raw PCM audio bytes (16-bit signed, little-endian, 48 kHz).
            Returns ``b\"\"`` on failure.
        """
        ...


# ── Piper backend ──────────────────────────────────────────────────────


class PiperBackend(TTSBackend):
    """Local Piper TTS via ``piper`` subprocess.

    Expects ``piper`` to be installed and available on ``PATH`` (or set the
    ``PIPER_BINARY`` environment variable to the full path of the binary).
    A voice model file may be specified via ``PIPER_VOICE_MODEL``; otherwise
    the default ``en_US-lessac-medium`` voice is used.
    """

    def __init__(self) -> None:
        self._binary = _env_path(
            "PIPER_BINARY", ["piper", "piper-tts"]
        )
        self._model = _env_path(
            "PIPER_VOICE_MODEL",
            [
                "en_US-lessac-medium",
                str(
                    Path.home()
                    / ".local"
                    / "share"
                    / "piper-voices"
                    / "en"
                    / "en_US"
                    / "lessac"
                    / "medium"
                    / "en_US-lessac-medium.onnx"
                ),
            ],
        )
        logger.info(
            "Piper backend: binary=%s, model=%s", self._binary, self._model
        )

    async def synthesize(self, text: str) -> bytes:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                "--model",
                self._model,
                "--output-raw",
                "--length-scale",
                "1.0",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=text.encode("utf-8")), timeout=30
            )

            if proc.returncode != 0:
                logger.error(
                    "Piper error (rc=%d): %s",
                    proc.returncode,
                    stderr.decode("utf-8", errors="replace")[:500],
                )
                return b""

            # Piper outputs 16-bit signed PCM at 22 050 Hz by default.
            # We need 48 kHz, so resample.
            pcm_22k = _ensure_pcm16(stdout)
            return _resample_pcm16(pcm_22k, 22050, 48000)

        except asyncio.TimeoutError:
            logger.warning("Piper subprocess timed out after 30s")
            return b""
        except FileNotFoundError:
            logger.error("Piper binary not found at %s", self._binary)
            return b""


# ── Edge TTS backend ───────────────────────────────────────────────────


class EdgeBackend(TTSBackend):
    """Microsoft Edge TTS via the ``edge-tts`` library.

    Uses the voice specified in ``config.tts_voice`` (default:
    ``en-US-AriaNeural``).
    """

    def __init__(self) -> None:
        self._voice = config.tts_voice
        logger.info("Edge TTS backend: voice=%s", self._voice)

    async def synthesize(self, text: str) -> bytes:
        try:
            import edge_tts
        except ImportError:
            logger.error(
                "edge-tts not installed. Install with: pip install edge-tts"
            )
            return b""

        try:
            communicate = edge_tts.Communicate(text, self._voice)
            # stream to a temp file so we can read it back as raw PCM
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            await communicate.save(tmp_path)

            # Decode MP3 to 48 kHz 16-bit PCM via ffmpeg
            pcm = await _ffmpeg_decode(
                tmp_path,
                out_sample_rate=48000,
                out_format="s16le",
            )
            Path(tmp_path).unlink(missing_ok=True)
            return _ensure_pcm16(pcm)

        except Exception as exc:
            logger.error("Edge TTS error: %s", exc)
            return b""


# ── OpenAI-compatible backend ──────────────────────────────────────────


class OpenAIBackend(TTSBackend):
    """OpenAI-compatible TTS endpoint.

    Points at the URL configured in ``tts_openai_base_url``.  This can be:
    * The official OpenAI TTS API (https://api.openai.com/v1).
    * A self-hosted endpoint (e.g. Fish Audio S2-Pro, Coqui, etc.).
    * Any service that implements the OpenAI TTS REST schema.
    """

    def __init__(self) -> None:
        self._base_url = config.tts_openai_base_url
        self._api_key = config.tts_openai_api_key
        self._model = config.tts_openai_model
        self._voice = config.tts_voice
        logger.info(
            "OpenAI TTS backend: base_url=%s, model=%s, voice=%s",
            self._base_url,
            self._model,
            self._voice,
        )

    async def synthesize(self, text: str) -> bytes:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.error("openai library not installed")
            return b""

        try:
            client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
            response = await client.audio.speech.create(
                model=self._model,
                voice=self._voice,
                input=text,
                response_format="pcm",  # request raw PCM directly
            )

            # The response content is raw PCM at the model's native sample rate
            # (usually 24 kHz for OpenAI).  Resample to 48 kHz.
            pcm = response.content
            if not pcm:
                return b""

            # Determine the sample rate from the response headers, if available
            sample_rate = 24000  # default assumption for OpenAI TTS
            try:
                if hasattr(response, "response") and hasattr(
                    response.response, "headers"
                ):
                    hdr = response.response.headers.get(
                        "x-tts-sample-rate", ""
                    )
                    if hdr:
                        sample_rate = int(hdr)
            except (ValueError, AttributeError):
                pass

            return _resample_pcm16(_ensure_pcm16(pcm), sample_rate, 48000)

        except Exception as exc:
            logger.error("OpenAI TTS error: %s", exc)
            return b""


# ── Factory ────────────────────────────────────────────────────────────


def create_tts_backend(
    backend_name: Optional[TTSBackendName] = None,
) -> TTSBackend:
    """Return a TTS backend instance for the given *backend_name*.

    Args:
        backend_name: One of ``\"piper\"``, ``\"edge\"``, ``\"openai\"``.
                      Defaults to ``config.tts_backend``.

    Returns:
        An initialised TTS backend instance.
    """
    name = backend_name or config.tts_backend

    backends: dict[str, type[TTSBackend]] = {
        "piper": PiperBackend,
        "edge": EdgeBackend,
        "openai": OpenAIBackend,
    }

    cls = backends.get(name)
    if cls is None:
        logger.warning(
            "Unknown TTS backend '%s', falling back to 'edge'", name
        )
        cls = EdgeBackend

    logger.info("Creating TTS backend: %s", name)
    return cls()


# ── Helpers ────────────────────────────────────────────────────────────


def _ensure_pcm16(data: bytes) -> bytes:
    """Ensure *data* is 16-bit signed PCM.

    If the data appears to be raw float32 (starts with a non-integer pattern),
    convert it.
    """
    if len(data) < 4:
        return data
    # Float32 magic: if the first 4 bytes interpreted as float32 are
    # between -1 and 1, it's likely already float32 that needs conversion.
    try:
        sample = struct.unpack_from("<f", data)[0]
        if -1.0 <= sample <= 1.0:
            # Convert float32 to int16
            import numpy as np

            arr = np.frombuffer(data, dtype=np.float32)
            # Clip to [-1, 1], replace NaN with 0, and scale to int16 range
            arr = np.clip(arr, -1.0, 1.0)
            arr = np.nan_to_num(arr)
            arr_int16 = (arr * 32767).astype(np.int16)
            return arr_int16.tobytes()
    except struct.error:
        pass
    return data


def _resample_pcm16(
    data: bytes, from_rate: int, to_rate: int
) -> bytes:
    """Resample 16-bit PCM *data* from *from_rate* to *to_rate* using ffmpeg.

    Falls back to linear interpolation if ffmpeg is unavailable.
    """
    if from_rate == to_rate or not data:
        return data

    # Try ffmpeg first
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-f", "s16le",
                "-ar", str(from_rate),
                "-ac", "1",
                "-i", "pipe:0",
                "-f", "s16le",
                "-ar", str(to_rate),
                "-ac", "1",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: simple linear interpolation (numpy)
    try:
        import numpy as np

        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        ratio = from_rate / to_rate
        orig_len = len(arr)
        new_len = int(orig_len / ratio)

        x_old = np.arange(orig_len)
        x_new = np.linspace(0, orig_len - 1, new_len)
        resampled = np.interp(x_new, x_old, arr)
        return resampled.astype(np.int16).tobytes()
    except ImportError:
        logger.warning("numpy not available for resample, returning original")
        return data


def _env_path(key: str, candidates: list[str]) -> str:
    """Return the first existing path from *candidates* or ``$key``.

    If the environment variable *key* is set, return it directly.
    Otherwise return the first candidate that exists as a file.
    """
    import os

    env_val = os.environ.get(key)
    if env_val:
        return env_val
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


async def _ffmpeg_decode(
    path: str,
    out_sample_rate: int = 48000,
    out_format: str = "s16le",
) -> bytes:
    """Decode an audio file to raw PCM using ffmpeg."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i", path,
            "-f", out_format,
            "-ar", str(out_sample_rate),
            "-ac", "1",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=30
        )
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg decode error (rc=%d): %s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace")[:200],
            )
            return b""
        return stdout or b""
    except (FileNotFoundError, asyncio.TimeoutError):
        logger.warning("ffmpeg not available or timed out")
        return b""
