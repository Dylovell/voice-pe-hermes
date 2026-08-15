# voice-pe-hermes

Custom firmware for the **Home Assistant Voice Preview Edition** that connects directly to **Hermes Agent** via WebSocket — no Home Assistant Assist pipeline needed.

The Voice PE handles the hardware (mic array, speaker, wake word, LED ring, buttons) while Hermes does the thinking: STT via faster-whisper, LLM via your configured provider, and TTS via a pluggable backend.

## Architecture

```
Voice PE (custom firmware)         Hermes VM (Python server)
┌──────────────────────┐           ┌──────────────────────────┐
│  Dual mic + XMOS DSP  │──WebSocket──→│  STT (faster-whisper)     │
│  Wake word (on-device)│           │  LLM (Spark/Ollama/etc) │
│  Speaker + DAC        │←──WebSocket──│  TTS (Piper/Edge/OpenAI) │
│  LED ring + buttons   │           │  Barge-in detection      │
└──────────────────────┘           └──────────────────────────┘
                                              │
                                              ▼
                                       Hermes Agent
                                    (tools, skills, HA)
```

## Quick Start

```bash
# 1. Flash the firmware to your Voice PE
esphome compile firmware/voice-pe-hermes.yaml
esphome upload firmware/voice-pe-hermes.yaml

# 2. Start the server
cd server
pip install -r requirements.txt
python server.py

# 3. Say "Hey Jarvis" (or your configured wake word)
#    Start talking to Hermes directly
```

## Features

- **Fully local** — wake word, STT, LLM, and TTS all run on your hardware
- **Barge-in** — interrupt Hermes mid-response, it'll listen and adjust
- **Pluggable TTS** — use Piper (local/CPU), Edge (free), or any OpenAI-compatible endpoint for high-quality voice
- **Wake word** — on-device detection via microWakeWord (no cloud needed)
- **Stock hardware preserved** — all buttons, LED effects, mute switch, and audio jack work as before
- **No HA dependency** — the Voice PE talks directly to Hermes. HA integration still works through Hermes' existing native HA tools

## Components

| Component | Directory | Description |
|-----------|-----------|-------------|
| **Firmware** | `firmware/` | Custom ESPHome YAML + C++ WebSocket voice component |
| **Server** | `server/` | Python WebSocket server (STT, LLM, TTS, barge-in) |
| **Docs** | `docs/` | Configuration, flashing, and architecture guides |

## Requirements

- Home Assistant Voice Preview Edition hardware
- Hermes Agent (any LLM backend)
- Python 3.11+ on the server machine
- ESPHome CLI for firmware compilation

## License

MIT
