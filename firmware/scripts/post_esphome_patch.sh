#!/bin/bash
#
# post_esphome_patch.sh — Patch Voice PE stock package to disable the red "no HA" LED glow
#
# The stock Voice PE package's control_leds master script checks
#   !id(wifi_id).is_connected() || !id(api_id).is_connected()
# and shows a red twinkle on the LED ring when HA isn't connected.
# Since Voice PE doesn't use HA, this always fires.
#
# This script replaces the combined check with just !id(wifi_id).is_connected(),
# bypassing the api_id connectivity test entirely.
#
# Idempotent: checks if the target string exists before patching;
#               if already patched (or never had the api_id check), prints
#               "Skipped — already up to date" and exits 0.
#

set -euo pipefail

# Paths — relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_FILE="$PROJECT_ROOT/firmware/.esphome/packages/f1018e04/home-assistant-voice.yaml"

# The target string we want to patch — the OR condition that checks both wifi and api
OLD_STRING='!id(wifi_id).is_connected() || !id(api_id).is_connected()'
NEW_STRING='!id(wifi_id).is_connected()'

# Resolve the target file
if [ ! -f "$TARGET_FILE" ]; then
    echo "Error: Stock package file not found at $TARGET_FILE"
    echo "Has ESPHome downloaded the Voice PE package yet? Run 'esphome compile' first."
    exit 1
fi

# Check if the target string exists (needs patching)
if grep -q "$OLD_STRING" "$TARGET_FILE"; then
    echo "Patched: removing api_id.is_connected() check from line $(grep -n "$OLD_STRING" "$TARGET_FILE" | head -1 | cut -d: -f1)"
    echo "  $OLD_STRING"
    echo "  → $NEW_STRING"
    sed -i 's/!id(wifi_id).is_connected() || !id(api_id).is_connected()/!id(wifi_id).is_connected()/' "$TARGET_FILE"
else
    echo "Skipped — already up to date"
fi

exit 0
