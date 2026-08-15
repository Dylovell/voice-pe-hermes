# Flashing Guide: voice-pe-hermes Firmware

Step-by-step guide to flash the custom Hermes voice firmware onto your Home
Assistant Voice Preview Edition.

---

## Prerequisites

### Hardware

- Home Assistant Voice Preview Edition (ESP32-S3 based)
- USB-C cable (data-capable — not charge-only)
- Access to the physical button and mute switch on the device

### Software

- Python 3.11+ (for ESPHome CLI)
- ESPHome CLI (`pip install esphome`)
- Git (to clone the repository)
- A serial/USB driver for the ESP32-S3 (included with ESPHome on most systems)

---

## Step 1: Install ESPHome CLI

```bash
# Install or update ESPHome
pip install --upgrade esphome

# Verify installation
esphome --version
```

> ESPHome 2025.12 or later is recommended. The custom component requires
> ESPHome's WebSocket client support and microWakeWord.

### Troubleshooting Installation

**Permission denied on /dev/ttyUSB0 or /dev/ttyACM0:**
```bash
# Add your user to the dialout group
sudo usermod -a -G dialout $USER
# Log out and back in, or run:
newgrp dialout
```

**macOS:**
```bash
# CP210x driver for some USB-to-serial chips
brew install silicon-labs-usb-to-uart-driver
```

---

## Step 2: Clone the Repository

```bash
git clone https://github.com/Dylovell/voice-pe-hermes.git
cd voice-pe-hermes
```

---

## Step 3: Configure the Firmware

Copy the default config and edit to match your environment:

```bash
cp firmware/voice-pe-hermes.yaml firmware/voice-pe-hermes.local.yaml
```

Edit `firmware/voice-pe-hermes.local.yaml` and set the following values:

### Required Settings

```yaml
wifi:
  ssid: "YourWiFiNetwork"       # Your 2.4 GHz WiFi SSID
  password: "YourWiFiPassword"

# WebSocket server address where the Python server runs
# This is the machine running server.py — NOT the Hermes LXC address
# if they are on different machines.
webhook:
  - websocket:
      url: "ws://192.168.1.199:8765"  # Replace with your server IP:port
```

### Wake Word

```yaml
micro_wake_word:
  model: "jarvis"          # Built-in: "jarvis", "alexa", "hey_jarvis"
  on_wake_word_detected:
    - lambda: |-
        id(hermes_voice).start_streaming();
```

The Voice PE comes pre-loaded with several microWakeWord models. If you don't
specify a model, the default "jarvis" is used.

### Optional Settings

```yaml
# LED brightness (0-255)
light:
  - platform: esp32_rmt_led_strip  # Actually WS2812 on Voice PE
      name: "Voice PE LED"
      pin: GPIO48
      num_leds: 12
      rgb_order: GRB
      default_transition_length: 0s

# Audio gain adjustments (if you need more/less mic sensitivity)
# These are usually fine at defaults.
```

---

## Step 4: Compile the Firmware

```bash
# Compile using your local config
esphome compile firmware/voice-pe-hermes.local.yaml
```

The first compile will download toolchains and dependencies. This takes
**2-5 minutes** depending on your internet connection and hardware.

If compilation succeeds, you'll see:
```
INFO Successfully compiled program.
```

---

## Step 5: Flash the Firmware

### Put the Voice PE in Flash Mode

1. **Disconnect USB** from the Voice PE
2. **Hold** the physical button on the device
3. **Connect USB** while holding the button
4. **Wait 2 seconds**, then release the button
5. The LED ring may flash briefly — the device is now in flash mode

If auto-detection fails, you may need to specify the port:
```bash
# Find the correct port
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# Flash with explicit port
esphule upload firmware/voice-pe-hermes.local.yaml --device /dev/ttyUSB0
```

### Flash

```bash
# Flash the firmware to the device
esphome run firmware/voice-pe-hermes.local.yaml
```

Or, to compile and upload in separate steps:
```bash
esphome compile firmware/voice-pe-hermes.local.yaml
esphome upload firmware/voice-pe-hermes.local.yaml
```

The `run` command compiles, uploads, and monitors the device logs. Use it
for the first flash so you can verify the device boots correctly.

---

## Step 6: Verify the Flash

After flashing, the device will reboot. Watch the serial output:

```bash
# If you used 'esphome run' the logs will auto-show.
# If you used separate steps, monitor with:
esphome logs firmware/voice-pe-hermes.local.yaml
```

### Expected Boot Sequence

```
[I][hermes_voice:012]: Hermes Voice component initialized
[I][hermes_voice:015]: Connecting to WebSocket server...
[I][hermes_voice:022]: WebSocket connected to ws://192.168.1.199:8765
[I][hermes_voice:028]: Wake word model loaded: jarvis
[I][hermes_voice:031]: Ready. Say "Hey Jarvis" to start.
```

If you see the WebSocket connection succeed, the device is ready.

### Verification Checklist

- [ ] Device connects to WiFi (check ESPHome logs for IP assignment)
- [ ] Device connects to WebSocket server
- [ ] Wake word model loads without errors
- [ ] LED ring shows idle state (solid or breathing light)

---

## Step 7: Test End-to-End

Before starting the server, make sure Hermes and your LLM backend are running.

1. **Start the Python server** (on your server machine):
   ```bash
   cd server
   pip install -r requirements.txt
   python server.py
   ```

2. **Say the wake word** ("Hey Jarvis" by default) near the device
3. **The LED should turn blue** to indicate listening
4. **Speak your query**, then pause
5. **The LED turns green** while processing
6. **The LED turns yellow** during TTS playback
7. **Hermes responds** through the Voice PE speaker

---

## Flashing Without Holding the Button (OTA)

Once the initial firmware is flashed via USB, subsequent updates can be done
over-the-air (OTA):

```bash
# OTA flash — device must be on WiFi
esphome run firmware/voice-pe-hermes.local.yaml --device voice-pe-hermes.local
```

Or using the device's IP:
```bash
esphome run firmware/voice-pe-hermes.local.yaml --device 192.168.1.50
```

> OTA requires that the first flash included WiFi credentials and the device
> is currently connected to your network.

---

## Troubleshooting

### "Failed to connect" during flash

1. **Wrong port** — Use `--device /dev/ttyUSB0` (or the correct port)
2. **Not in flash mode** — Hold the button while connecting USB
3. **Bad cable** — Try a different USB-C cable (some are power-only)
4. **Driver missing** — On Windows, install CP210x or CH340 drivers

### Device boots but WebSocket won't connect

1. **Server not running** — Verify `server.py` is running and listening
2. **Wrong URL** — Check the `url` in the firmware YAML is correct
3. **Firewall** — Ensure port 8765 is open on the server machine
4. **WiFi** — Confirm the Voice PE has a valid IP and can reach the server

### Wake word not detected

1. **Distance from mic** — Speak within 3-5 feet of the device
2. **Background noise** — The Voice PE's beamforming mic array is good at
   rejecting noise, but very loud environments may affect detection
3. **Model not loaded** — Check logs for wake word model loading errors
4. **Wrong model name** — Try a different model (e.g. "alexa") in the config

### Audio Quality Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Echo in response | TTS playing through mic | Lower speaker volume |
| Distorted playback | Volume too high | Reduce volume in config |
| Mic too quiet | Low mic gain | Increase mic gain in YAML |
| Robotic TTS | Wrong sample rate | Check resampling config |

### Recovering a Bricked Device

If a bad flash leaves the device unresponsive:

1. **Hold the button while connecting USB** — this puts it in download mode
2. **Flash a known-good config** — use the default `voice-pe-hermes.yaml`
3. **If that fails**, try erasing the flash first:
   ```bash
   esphome run firmware/voice-pe-hermes.local.yaml --erase-first
   ```

The Voice PE's bootloader is in ROM — it's very hard to permanently brick.

---

## Quick Reference

```bash
# Flash a new device (first time)
esphome run firmware/voice-pe-hermes.local.yaml

# OTA update
esphome run firmware/voice-pe-hermes.local.yaml --device voice-pe-hermes.local

# Monitor logs
esphome logs firmware/voice-pe-hermes.local.yaml

# Clean compile (force rebuild everything)
esphome compile firmware/voice-pe-hermes.local.yaml --clean

# List ESPHome devices on the network
esphome dashboard
```
