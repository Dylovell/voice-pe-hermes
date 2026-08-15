# Configuration Reference

This document describes all configuration options for the voice-pe-hermes
system — both the firmware side (ESPHome YAML) and the server side
(environment variables).

---

## Firmware Configuration

The firmware is configured via ESPHome YAML files. The main file is
`firmware/voice-pe-hermes.yaml`. For local overrides, create a copy with your
settings:

```bash
cp firmware/voice-pe-hermes.yaml firmware/voice-pe-hermes.local.yaml
```

### WiFi

```yaml
wifi:
  ssid: !secret wifi_ssid          # Your 2.4 GHz WiFi SSID
  password: !secret wifi_password   # WiFi password

  # Optional: static IP instead of DHCP
  manual_ip:
    static_ip: 192.168.1.50
    gateway: 192.168.1.1
    subnet: 255.255.255.0
    dns1: 192.168.1.1

  # Optional: fallback AP for initial setup
  ap:
    ssid: "Voice PE Fallback Hotspot"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ssid` | string | — | WiFi network name (2.4 GHz only) |
| `password` | string | — | WiFi password |
| `manual_ip.static_ip` | string | DHCP | Static IP for the device |
| `manual_ip.gateway` | string | DHCP | Network gateway |
| `manual_ip.subnet` | string | DHCP | Subnet mask |
| `manual_ip.dns1` | string | DHCP | Primary DNS server |

> **Note:** Voice PE only supports 2.4 GHz WiFi. The ESP32-S3 does not
> support 5 GHz.

### WebSocket Server Connection

```yaml
web_socket_voice:
  server_host: "192.168.1.199"   # IP or hostname of the server machine
  server_port: 8765              # Port of the WebSocket server
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_host` | string | — | IP or hostname of the Python server |
| `server_port` | uint16 | `8765` | Port of the WebSocket server |

### Wake Word

```yaml
micro_wake_word:
  model: "jarvis"
  probability_cutoff: 0.5
  on_wake_word_detected:
    - then:
        - lambda: |-
            id(web_socket_voice_component).start_stream();
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `"jarvis"` | Wake word model: `"jarvis"`, `"alexa"`, `"hey_jarvis"`, or custom |
| `probability_cutoff` | float | `0.5` | Detection sensitivity (lower = more sensitive, more false positives) |

Available built-in models:
- `"jarvis"` — "Jarvis"
- `"alexa"` — "Alexa"
- `"hey_jarvis"` — "Hey Jarvis"

### LED Ring

```yaml
light:
  - platform: esp32_rmt_led_strip
    name: "Voice PE LED Ring"
    pin: GPIO48
    num_leds: 12
    rgb_order: GRB
    default_transition_length: 0s
    effects:
      - pulse:
          name: "Idle"
          transition_length: 1s
          update_interval: 500ms
          min_brightness: 20%
          max_brightness: 80%
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pin` | int | `GPIO48` | Data pin for the LED ring |
| `num_leds` | int | `12` | Number of LEDs in the ring |
| `rgb_order` | string | `"GRB"` | Color order (Voice PE uses GRB) |
| `default_transition_length` | time | `0s` | Default transition time for color changes |

**State Colors:**

| State | Color | Description |
|-------|-------|-------------|
| Idle | Pulse blue (breathing) | Waiting for wake word |
| Listening | Solid blue | Mic active, streaming to server |
| Processing | Solid green | STT + LLM inference |
| Speaking | Solid yellow | TTS playback |
| Muted | Solid red | Mic muted (HW switch) |
| Error | Flashing red | Connection or processing error |

### Custom WebSocket Voice Component

```yaml
web_socket_voice:
  server_host: "192.168.1.199"
  server_port: 8765
  microphone: voice_pe_mic
  speaker: voice_pe_speaker
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_host` | string | — | Server hostname or IP (required) |
| `server_port` | uint16 | `8765` | Server WebSocket port |
| `microphone` | mic ID | — | Microphone entity (required) |
| `speaker` | speaker ID | — | Speaker entity (required) |

---

## Server Configuration

The Python WebSocket server is configured entirely via environment variables.
No command-line flags or config files are used — all settings are read from
the environment at startup.

### Quick Start

```bash
# Minimal — uses all defaults (connects to Spark DS4 at 192.168.1.201:8888)
cd server
pip install -r requirements.txt
python server.py

# Override the LLM endpoint
LLM_BASE_URL="http://localhost:11434/v1" LLM_MODEL="llama3" python server.py
```

### Environment Variable Reference

#### Network

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address for the WebSocket server |
| `PORT` | `8765` | WebSocket server port |

#### STT (faster-whisper)

| Variable | Default | Description |
|----------|---------|-------------|
| `STT_MODEL` | `"base"` | Model size: `"tiny"`, `"base"`, `"small"`, `"medium"`, `"large"` |
| `STT_DEVICE` | `"cpu"` | Compute device: `"cpu"`, `"cuda"`, `"auto"` |
| `STT_COMPUTE` | `"int8"` | Compute type: `"int8"`, `"float16"`, `"float32"` |
| `SAMPLE_RATE_IN` | `16000` | Input audio sample rate from Voice PE |

**Model size tradeoffs:**

| Model | Size | Speed | Accuracy | VRAM |
|-------|------|-------|----------|------|
| `tiny` | 39 MB | Fastest | Acceptable | ~1 GB |
| `base` | 74 MB | Fast | Good | ~1 GB |
| `small` | 244 MB | Medium | Better | ~2 GB |
| `medium` | 769 MB | Slow | High | ~5 GB |
| `large` | 1.55 GB | Slowest | Highest | ~10 GB |

`base` is the default. Use `tiny` for lowest latency on CPU. Use `small` or
larger only when GPU (CUDA) acceleration is available.

#### LLM (OpenAI-compatible API)

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BASE_URL` | `"http://192.168.1.201:8888/v1"` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `"deepseek-v4-flash-abliterated"` | Model name to use |
| `LLM_API_KEY` | `"sk-none"` | API key (if required by provider) |
| `LLM_SYSTEM_PROMPT` | Hermes-style prompt | System prompt for the LLM |
| `LLM_MAX_TOKENS` | `512` | Maximum response tokens |
| `LLM_TEMPERATURE` | `0.7` | Response temperature (0.0 - 1.0) |

The default configuration targets the Spark DS4 abliterated DeepSeek-V4-Flash
at `192.168.1.201:8888` (a local homelab setup with dual DGX Spark nodes).
Change `LLM_BASE_URL` and `LLM_MODEL` to use any OpenAI-compatible endpoint:

```bash
# Ollama
LLM_BASE_URL="http://192.168.1.210:11434/v1" LLM_MODEL="qwen3.8-27b"

# OpenAI
LLM_BASE_URL="https://api.openai.com/v1" LLM_MODEL="gpt-4o-mini" LLM_API_KEY="sk-..."

# vLLM
LLM_BASE_URL="http://localhost:8000/v1" LLM_MODEL="mistral-7b"
```

#### TTS (Pluggable)

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_BACKEND` | `"edge"` | TTS engine: `"piper"`, `"edge"`, `"openai"` |
| `TTS_VOICE` | `"en-US-AriaNeural"` | Voice name (depends on backend) |
| `TTS_OPENAI_BASE_URL` | `"http://192.168.1.201:8888/v1"` | Base URL for OpenAI-compatible TTS |
| `TTS_OPENAI_API_KEY` | `"sk-none"` | API key for OpenAI TTS |
| `TTS_OPENAI_MODEL` | `"tts-1"` | TTS model name for OpenAI backend |
| `SAMPLE_RATE_OUT` | `48000` | Output audio sample rate to Voice PE speaker |

**TTS Backend Comparison:**

| Backend | Type | Quality | Latency | Dependency | Notes |
|---------|------|---------|---------|------------|-------|
| `edge` | Cloud (free) | High | Medium | `edge-tts` | Microsoft neural TTS, no API key needed, requires internet |
| `piper` | Local CPU | Medium | Low | `piper-tts` | Fully local, CPU-only, lower quality |
| `openai` | Cloud (paid) | Very high | High | `openai` | OpenAI API key required, per-character cost |

**Voice names by backend:**

- **Edge TTS:** `"en-US-AriaNeural"`, `"en-US-JennyNeural"`, `"en-US-GuyNeural"`,
  `"en-GB-SoniaNeural"`, `"en-AU-NatashaNeural"` (many more available)
- **Piper:** Voice name = path to `.onnx` model file, or a named voice from the
  piper voices catalog (e.g. `"en_US-less-medium"`)
- **OpenAI:** `"alloy"`, `"echo"`, `"fable"`, `"onyx"`, `"nova"`, `"shimmer"`

#### Barge-in / VAD

| Variable | Default | Description |
|----------|---------|-------------|
| `BARGE_IN_THRESHOLD` | `0.02` | Energy threshold for barge-in detection (0.0-1.0) |
| `BARGE_IN_MIN_FRAMES` | `3` | Minimum consecutive speech frames before triggering barge-in |
| `VAD_CHUNK_SECONDS` | `0.03` | Audio chunk duration (seconds) for VAD analysis |

The barge-in system monitors the microphone input energy during TTS playback.
If speech energy exceeds `BARGE_IN_THRESHOLD` for at least
`BARGE_IN_MIN_FRAMES` consecutive frames, the current TTS output is
interrupted and the server starts a new listen cycle.

#### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `VERBOSE` | `""` (false) | Set to `"1"`, `"true"`, or `"yes"` for debug-level logging |

---

## Examples

### Minimal Firmware Config

```yaml
esphome:
  name: voice-pe-hermes
  platform: ESP32
  board: esp32-s3-devkitc-1

wifi:
  ssid: "MyNetwork"
  password: "MyPassword"

web_socket_voice:
  server_host: "192.168.1.199"
  server_port: 8765
  microphone: voice_pe_mic
  speaker: voice_pe_speaker

micro_wake_word:
  model: "jarvis"
  on_wake_word_detected:
    - then:
        - lambda: |-
            id(web_socket_voice_component).start_stream();
```

### Local-Only Server (Piper TTS + Ollama)

```bash
TTS_BACKEND=piper \
LLM_BASE_URL="http://192.168.1.210:11434/v1" \
LLM_MODEL="qwen3.8-27b" \
STT_DEVICE=cpu \
python server.py
```

### Low-Latency Server (GPU STT + Edge TTS)

```bash
STT_MODEL=tiny \
STT_DEVICE=cuda \
TTS_BACKEND=edge \
TTS_VOICE=en-US-AriaNeural \
python server.py
```

### OpenAI Cloud Pipeline

```bash
LLM_BASE_URL="https://api.openai.com/v1" \
LLM_MODEL="gpt-4o-mini" \
LLM_API_KEY="sk-..." \
TTS_BACKEND=openai \
TTS_VOICE=alloy \
OPENAI_API_KEY="sk-..." \
python server.py
```
