#!/bin/bash
# post_esphome_patch.sh — Patch Voice PE stock package
#
# Patches applied:
#   1. Remove api_id.is_connected() from control_leds script (red LED fix)
#   2. Change mic bits_per_sample from 32bit to 16bit (audio format fix)
#
# Idempotent: checks if each target string exists before patching.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_FILE="$PROJECT_ROOT/firmware/.esphome/packages/f1018e04/home-assistant-voice.yaml"

if [ ! -f "$TARGET_FILE" ]; then
    echo "Error: Stock package file not found at $TARGET_FILE"
    echo "Run 'esphome compile' first to download the package."
    exit 1
fi

patches=0

# ── Patch 1: Red LED fix ──────────────────────────────────────────────
OLD_LED='!id(wifi_id).is_connected() || !id(api_id).is_connected()'
NEW_LED='!id(wifi_id).is_connected()'

if grep -q "$OLD_LED" "$TARGET_FILE"; then
    echo "P1: Removing api_id check from control_leds (line $(grep -n "$OLD_LED" "$TARGET_FILE" | head -1 | cut -d: -f1))"
    sed -i 's/!id(wifi_id).is_connected() || !id(api_id).is_connected()/!id(wifi_id).is_connected()/' "$TARGET_FILE"
    patches=$((patches + 1))
fi

# ── Patch 2: Mic 32bit → 16bit ────────────────────────────────────────
# The stock mic outputs 32-bit stereo.  Our WebSocket transport treats
# all audio as 16-bit mono int16.  32-bit stereo data gets corrupted:
# each 32-bit sample is split into two 16-bit values, doubling the
# apparent duration and mangling the waveform.
OLD_MIC='    bits_per_sample: 32bit'
NEW_MIC='    bits_per_sample: 16bit'

if grep -q "$OLD_MIC" "$TARGET_FILE"; then
    echo "P2: Changing mic bits_per_sample 32bit → 16bit (line $(grep -n "$OLD_MIC" "$TARGET_FILE" | head -1 | cut -d: -f1))"
    sed -i 's/    bits_per_sample: 32bit/    bits_per_sample: 16bit/' "$TARGET_FILE"
    patches=$((patches + 1))
fi

# ── Summary ────────────────────────────────────────────────────────────
if [ "$patches" -eq 0 ]; then
    echo "No patches applied (already up to date)"
else
    echo "Applied $patches patch(es)"
fi

exit 0
