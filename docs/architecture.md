# Architecture: voice-pe-hermes

## Overview

voice-pe-hermes is a complete replacement for the Home Assistant Voice Preview
Edition's firmware and server-side pipeline. Instead of routing audio through
Home Assistant's Assist pipeline (which requires HA's conversation agent,
STT/TTS integrations, and wake word handling), the Voice PE talks directly to
a Python server that interfaces with Hermes Agent for LLM inference.

This eliminates HA as a mandatory intermediary, reduces latency, and gives you
full control over every component — STT model, LLM provider, TTS engine —
without HA integration complexity.

---

## System Diagram

```
┌──────────────────────────────────┐          ┌──────────────────────────────────────┐
│  Voice PE (ESP32-S3)              │          │  Server Machine (Hermes LXC / VM)   │
│                                   │          │                                      │
│  ┌────────────────────────────┐   │  ┌──────┼──┐  ┌──────────────────────────┐    │
│  │ Dual MEMS Mic Array        │   │  │      │  │  │  WebSocket Server (Python)│    │
│  │ └→ XMOS DSP (16kHz PCM)   │──┼──┼──────┼──┼──→│                          │    │
│  │                            │   │  │      │  │  │  ┌────────────────────┐  │    │
│  │ Wake Word (microWakeWord)  │   │  │      │  │  │  │ STT (faster-whisper)│  │    │
│  │ └→ detect → start stream   │   │  │      │  │  │  └────────┬───────────┘  │    │
│  │                            │   │  │      │  │  │           ▼              │    │
│  │ Button Inputs              │   │  │      │  │  │  ┌────────────────────┐  │    │
│  │  ├─ Mute (HW switch)      │   │  │      │  │  │  │ LLM Router          │  │    │
│  │  └─ Physical button       │   │  │      │  │  │  │ (Spark / Ollama /   │  │    │
│  │                            │   │  │      │  │  │  │  OpenAI-compatible) │  │    │
│  │ LED Ring (WS2812)          │   │  │      │  │  │  └────────┬───────────┘  │    │
│  │                            │   │  │      │  │  │           ▼              │    │
│  │ MAX98357 DAC + Speaker     │◄──┼──┼──────┼──┼──┤  ┌────────────────────┐  │    │
│  │                            │   │  │      │  │  │  │ TTS Engine           │  │    │
│  │ 3.5mm Audio Jack           │   │  │      │  │  │  │ (Piper / Edge /     │  │    │
│  └────────────────────────────┘   │  │      │  │  │  │  OpenAI-compatible) │  │    │
│                                   │  │      │  │  │  └────────────────────┘  │    │
└──────────────────────────────────┘  │  │      │  │  │                          │    │
                                      │  │      │  │  └──────────────────────────┘    │
                                      │  │      │  │           │                     │
                                      │  │      │  │           ▼                     │
                                      │  │      │  │  ┌──────────────────────────┐    │
                                      │  │      │  │  │  Hermes Agent            │    │
                                      │  │      │  │  │  (tools, skills, HA)     │    │
                                      │  │      │  │  └──────────────────────────┘    │
                                      └──┼──────┼──┘                                  │
                                         │      │                                      │
                                    ┌────┘      └──────┐                              │
                                    ▼                   ▼                              │
                            ┌─────────────┐    ┌─────────────┐                       │
                            │ WiFi / LAN   │    │ (Optional)  │                       │
                            │ Network      │    │ HA at       │                       │
                            │              │    │ 192.168.x.x │                       │
                            └─────────────┘    └─────────────┘                       │
                                                                                     │
┌─────────────────────────────────────────────────────────────────────────────────────┘
│  Note: Server and Hermes Agent may run on the same machine.
│  HA integration via Hermes' native HA tools — no Assist pipeline needed.
└─────────────────────────────────────────────────────────────────────────────────────
```

---

## Components

### 1. Voice PE Firmware (`firmware/`)

The firmware component replaces ESPHome's built-in `voice_assistant` component
with a custom WebSocket-based streaming client. It runs on the ESP32-S3 inside
the Home Assistant Voice Preview Edition hardware.

**Key responsibilities:**

- **Audio capture** — Read 16 kHz 16-bit PCM from the XMOS DSP via I2S
- **Wake word detection** — On-device microWakeWord inference (no cloud)
- **Streaming** — Send audio chunks to the server over WebSocket
- **Audio playback** — Receive processed audio (TTS) and play via DAC + speaker
- **LED control** — Visual feedback (listening, processing, speaking, mute)
- **Button/Mute switch** — Hardware input handling

**Audio format:**

| Direction | Sample Rate | Bit Depth | Format     |
|-----------|------------|-----------|------------|
| Mic → Server | 16 kHz    | 16-bit    | PCM (raw)  |
| Server → Speaker | 48 kHz | 16-bit   | PCM (raw)  |

The server resamples server-side TTS output from the TTS engine's native rate
to 48 kHz before sending back to the device.

### 2. Python WebSocket Server (`server/`)

The server is the brain of the system. It runs on your Hermes host (LXC, VM,
or dedicated machine) and orchestrates the full STT → LLM → TTS pipeline.

**Key responsibilities:**

- WebSocket endpoint for Voice PE connections
- STT via faster-whisper (local, GPU-accelerated)
- LLM routing to configured provider (Spark DS4, Ollama, OpenAI API)
- TTS via pluggable backend (Piper, Edge TTS, OpenAI TTS)
- Barge-in detection (interrupt TTS playback on new wake word)
- Session management and error handling

**Processing pipeline (in order):**

1. **Audio reception** — Receive PCM chunks, buffer until end-of-speech
2. **STT** — Transcribe buffered audio via faster-whisper
3. **LLM** — Forward transcription to configured LLM with system prompt
4. **TTS** — Synthesize LLM response as speech audio
5. **Stream back** — Send audio chunks back to Voice PE for playback

### 3. Hermes Agent

Hermes Agent provides the LLM backend and tool ecosystem. The server does not
call an LLM directly — it routes through Hermes' configured provider chain,
which means:

- Your existing model config (provider, model, API key) is reused
- Hermes tools (Home Assistant, web search, file access) are available
- Multi-model fallback chain works automatically

---

## WebSocket Protocol

The Voice PE and server communicate over a single persistent WebSocket
connection. The protocol uses JSON text messages for control and binary frames
for audio data.

### Control Messages (text frames)

```
Client → Server:
  {"type": "hello", "version": 1}
  {"type": "audio_start", "sample_rate": 16000}
  {"type": "mute", "muted": true|false}

Server → Client:
  {"type": "welcome", "version": 1}
  {"type": "stt_result", "text": "..."}
  {"type": "llm_start"}
  {"type": "tts_start"}
  {"type": "tts_end"}
  {"type": "error", "message": "..."}
  {"type": "state", "state": "idle|listening|processing|speaking"}
```

### Audio Data (binary frames)

Binary frames are raw PCM audio. Direction determines format:

| Direction | Sample Rate | Description |
|-----------|------------|-------------|
| Client → Server | 16 kHz 16-bit | Mic audio chunks during listening |
| Server → Client | 48 kHz 16-bit | TTS audio chunks during speaking |

### Session Flow

```
   Voice PE                    Server
      │                          │
      │──── hello ──────────────→│
      │←──── welcome ────────────│
      │                          │
  ──── Wake word detected ────   │
      │                          │
      │──── audio_start ────────→│
      │[─── PCM audio chunks ──]→│  (mic streaming)
      │←──── state: listening ───│
      │                          │
      │──── [end of speech] ────→│
      │                          ├── STT
      │←──── stt_result ─────────│
      │                          ├── LLM
      │←──── llm_start ──────────│
      │                          ├── TTS
      │←──── tts_start ──────────│
      │[─── PCM audio chunks ──]←│  (speaker streaming)
      │←──── tts_end ────────────│
      │←──── state: idle ────────│
      │                          │
  ──── (barge-in: new wake ───   │
  ────  word during playback)    │
      │                          │
      │──── audio_start ────────→│  (interrupts current TTS)
      │                          │
```

---

## Barge-in

Barge-in allows the user to interrupt the TTS response by saying the wake word
again (or pressing the button). When the Voice PE detects a wake word while
the server is streaming TTS audio:

1. Voice PE sends `audio_start` (which implicitly cancels the current stream)
2. Server stops TTS output and discards buffered audio
3. Server begins listening for new audio
4. New transcription proceeds as normal

The Voice PE firmware instantly cuts audio output on wake word detection,
giving immediate feedback to the user.

---

## Audio Quality and Latency

### Key latency contributors

| Stage | Estimated Time | Notes |
|-------|---------------|-------|
| Audio capture + buffering | ~500-1000 ms | Configurable VAD threshold |
| STT (faster-whisper) | ~200-800 ms | Depends on model size + GPU |
| LLM inference | ~300-3000 ms | Depends on model + provider |
| TTS synthesis | ~200-1000 ms | Depends on engine + voice |
| Network (LAN) | ~5-20 ms | Local network negligible |
| **Total (first word)** | **~1.5-5s** | Varies by hardware |

### Optimization strategies

- Use `tiny` or `base` faster-whisper model for faster STT
- Use streaming TTS (Piper) to send audio before full response is generated
- Keep server on the same LAN as Voice PE
- Use GPU acceleration for STT (CUDA) on the server machine
- Configure VAD silence duration to minimize end-of-speech delay

---

## Security Considerations

- **No authentication** — The WebSocket server has no built-in auth. Run it
  on a trusted LAN, or place behind a reverse proxy (nginx, Caddy) with TLS.
- **Microphone privacy** — Audio never leaves your LAN unless you configure
  a cloud TTS backend (Edge TTS calls Microsoft servers; OpenAI TTS calls
  OpenAI API). All STT and LLM processing is local.
- **Hermes credentials** — The server uses Hermes' configured provider,
  including any API keys. These are read from Hermes' `.env` / `config.yaml`.

---

## Dependencies

### Firmware (ESPHome)

- ESPHome 2025.12+ (or latest)
- microWakeWord (built-in ESPHome component)
- ESP32-S3 Arduino framework

### Server (Python)

| Dependency | Purpose |
|------------|---------|
| faster-whisper | Local STT engine |
| openai | LLM + TTS API client |
| websockets | WebSocket server |
| piper-tts | Local TTS (CPU) |
| edge-tts | Free cloud TTS |
| soundfile | Audio format handling |
| numpy | Audio processing |

---

## Project Structure

```
voice-pe-hermes/
├── firmware/
│   ├── voice-pe-hermes.yaml    # Main ESPHome config
│   └── components/             # Custom C++ components
│       └── hermes_voice/       # WebSocket voice streaming component
│           ├── __init__.py
│           └── hermes_voice.h
├── server/
│   ├── server.py               # Main WebSocket server
│   ├── requirements.txt        # Python dependencies
│   ├── stt.py                  # STT module (faster-whisper)
│   ├── llm_router.py           # LLM routing
│   ├── tts.py                  # TTS module
│   ├── vad.py                  # Voice activity detection
│   └── config.py               # Server configuration
├── docs/
│   ├── architecture.md         # This file
│   ├── flashing.md             # Firmware flashing guide
│   └── configuration.md        # Configuration reference
├── tests/
│   ├── test_server.py
│   ├── test_stt.py
│   └── test_tts.py
├── README.md
├── LICENSE
└── .gitignore
```
