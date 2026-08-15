# Configuration Reference

This document describes all configuration options for the voice-pe-hermes
system — both the firmware side (ESPHome YAML) and the server side
(environment variables, command line, config file).

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
webhook:
  - websocket:
      url: "ws://192.168.1.199:8765"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | — | WebSocket URL of the Python server |
| `url` (wss) | string | — | WebSocket Secure URL (wss://) if using TLS |

The server defaults to port **8765**. If you change the server port, update
this URL accordingly.

### Wake Word

```yaml
micro_wake_word:
  model: "jarvis"
  probability_cutoff: 0.5
  on_wake_word_detected:
    - lambda: |-
        id(hermes_voice).start_streaming();
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

### Audio

```yaml
# I2S microphone (XMOS DSP)
i2s_audio:
  - id: i2s_mic
    i2s_lrclk: GPIO33
    i2s_bclk: GPIO11
    i2s_din: GPIO12

# I2S speaker (MAX98357 DAC)
i2s_audio:
  - id: i2s_speaker
    i2s_lrclk: GPIO20
    i2s_bclk: GPIO21
    i2s_dout: GPIO18

microphone:
  - platform: i2s_audio
    id: voice_pe_mic
    i2s_audio_id: i2s_mic
    channel: left
    bits_per_sample: 16
    sample_rate: 16000

speaker:
  - platform: i2s_audio
    id: voice_pe_speaker
    i2s_audio_id: i2s_speaker
    channel: mono
    bits_per_sample: 16
    sample_rate: 48000
```

| Component | Field | Default | Description |
|-----------|-------|---------|-------------|
| Microphone | `sample_rate` | `16000` | Mic sample rate (must be 16 kHz for wake word) |
| Microphone | `bits_per_sample` | `16` | Bit depth |
| Speaker | `sample_rate` | `48000` | Speaker sample rate (server resamples to this) |
| Speaker | `bits_per_sample` | `16` | Bit depth |

### Custom Hermes Voice Component

```yaml
hermes_voice:
  led_ring: voice_pe_led_ring
  microphone: voice_pe_mic
  speaker: voice_pe_speaker
  mute_switch: voice_pe_mute_switch
  button: voice_pe_button
  vad_mode: "auto"
  vad_threshold: 0.3
  vad_silence_duration: 800ms
  max_listening_duration: 30s
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `led_ring` | light ID | — | LED ring light entity (required) |
| `microphone` | mic ID | — | Microphone entity (required) |
| `speaker` | speaker ID | — | Speaker entity (required) |
| `mute_switch` | switch ID | — | Hardware mute switch |
| `button` | button ID | — | Physical button |
| `vad_mode` | string | `"auto"` | VAD mode: `"auto"`, `"manual"`, `"disabled"` |
| `vad_threshold` | float | `0.3` | VAD energy threshold (0-1, lower = more sensitive) |
| `vad_silence_duration` | time | `800ms` | Silence duration before end-of-speech |
| `max_listening_duration` | time | `30s` | Max recording time (safety cutoff) |

---

## Server Configuration

The Python WebSocket server can be configured via environment variables, a
`.env` file, or command-line arguments.

### Command Line

```bash
cd server
python server.py [options]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8765` | WebSocket port |
| `--stt-model` | `tiny` | faster-whisper model size (`tiny`, `base`, `small`, `medium`, `large`) |
| `--stt-device` | `auto` | STT device (`cpu`, `cuda`, `auto`) |
| `--tts-backend` | `piper` | TTS backend (`piper`, `edge`, `openai`) |
| `--tts-voice` | varies | TTS voice name |
| `--llm-provider` | `hermes` | LLM provider (`hermes`, `openai`, or direct URL) |
| `--llm-model` | — | Model name override |
| `--log-level` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_VOICE_HOST` | `0.0.0.0` | Server bind address |
| `HERMES_VOICE_PORT` | `8765` | WebSocket port |
| `HERMES_VOICE_STT_MODEL` | `tiny` | faster-whisper model |
| `HERMES_VOICE_STT_DEVICE` | `auto` | STT compute device |
| `HERMES_VOICE_TTS_BACKEND` | `piper` | TTS backend |
| `HERMES_VOICE_TTS_VOICE` | — | TTS voice name |
| `HERMES_VOICE_LLM_PROVIDER` | `hermes` | LLM provider |
| `HERMES_VOICE_LLM_MODEL` | — | LLM model override |
| `HERMES_VOICE_LLM_SYSTEM_PROMPT` | — | Custom LLM system prompt |
| `HERMES_VOICE_LOG_LEVEL` | `info` | Log level |
| `OPENAI_API_KEY` | — | For OpenAI TTS/LLM |
| `HERMES_CONFIG` | `~/.hermes/config.yaml` | Path to Hermes config |

### TTS Backend Comparison

| Backend | Type | Quality | Latency | Required |
|---------|------|---------|---------|----------|
| `piper` | Local CPU | Medium | Low | `piper-tts` Python package |
| `edge` | Cloud (free) | High | Medium | `edge-tts` Python package, internet |
| `openai` | Cloud (paid) | Very high | High | `openai` Python package, API key |

**Piper** — Fully local, runs on CPU, low latency. Use this if local-only
processing is important. Voice quality is serviceable but not as natural as
cloud options.

**Edge TTS** — Free Microsoft neural TTS. High quality voices, requires
internet access. No API key needed. Best balance of quality and cost.

**OpenAI TTS** — Highest quality, lowest latency for cloud TTS. Requires
an OpenAI API key and incurs per-character costs.

### STT Model Sizes

| Model | Size | Speed | Accuracy | VRAM |
|-------|------|-------|----------|------|
| `tiny` | 39 MB | Fastest | Acceptable | ~1 GB |
| `base` | 74 MB | Fast | Good | ~1 GB |
| `small` | 244 MB | Medium | Better | ~2 GB |
| `medium` | 769 MB | Slow | High | ~5 GB |
| `large` | 1.55 GB | Slowest | Highest | ~10 GB |

`tiny` and `base` are recommended for real-time voice applications. Larger
models improve accuracy but add noticeable latency.

### VAD (Voice Activity Detection) Options

```json
{
  "vad_mode": "auto",
  "vad_threshold": 0.3,
  "vad_silence_duration_ms": 800,
  "vad_min_speech_duration_ms": 200,
  "vad_min_silence_duration_ms": 100,
  "vad_buffer_size_ms": 300,
  "vad_use_webrtc": true
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vad_mode` | `auto` | `auto` (WebRTC VAD), `energy` (energy threshold), `disabled` (push-to-talk only) |
| `vad_threshold` | `0.3` | Energy threshold for energy-mode VAD (0-1) |
| `vad_silence_duration_ms` | `800` | ms of silence before declaring end-of-speech |
| `vad_min_speech_duration_ms` | `200` | Minimum speech duration to prevent noise triggers |
| `vad_min_silence_duration_ms` | `100` | Minimum silence within speech before resampling |
| `vad_use_webrtc` | `true` | Use WebRTC VAD for more accurate voice detection |

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

webhook:
  - websocket:
      url: "ws://192.168.1.199:8765"

micro_wake_word:
  model: "jarvis"
  on_wake_word_detected:
    - lambda: |-
        id(hermes_voice).start_streaming();

hermes_voice:
  led_ring: voice_pe_led_ring
  microphone: voice_pe_mic
  speaker: voice_pe_speaker
```

### Minimal Server Config (using Hermes provider)

```bash
# Using defaults — inherits LLM config from Hermes
export HERMES_VOICE_STT_MODEL=base
export HERMES_VOICE_TTS_BACKEND=piper
python server.py
```

### Server with OpenAI TTS + Custom LLM

```bash
export HERMES_VOICE_TTS_BACKEND=openai
export HERMES_VOICE_TTS_VOICE=alloy
export HERMES_VOICE_LLM_PROVIDER=openai
export HERMES_VOICE_LLM_MODEL=gpt-4o-mini
export OPENAI_API_KEY=sk-...
python server.py --port 8765
```
