"""ESPHome custom component: web_socket_voice.

Replaces the built-in voice_assistant component with a WebSocket-based
streaming component that talks directly to a Python server (not Home
Assistant).  Uses ESP-IDF's built-in ``esp_websocket_client`` and
``cJSON`` — no external PlatformIO libraries needed.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import pins
from esphome.components import microphone, speaker
from esphome.const import CONF_ID, CONF_MICROPHONE, CONF_SPEAKER

# ---------------------------------------------------------------------------
# Configuration keys
# ---------------------------------------------------------------------------
CONF_SERVER_HOST = "server_host"
CONF_SERVER_PORT = "server_port"

# ---------------------------------------------------------------------------
# Code generation namespace
# ---------------------------------------------------------------------------
web_socket_voice_ns = cg.esphome_ns.namespace("web_socket_voice")
WebSocketVoice = web_socket_voice_ns.class_(
    "WebSocketVoice",
    cg.Component,
)

# ---------------------------------------------------------------------------
# Configuration schema
# ---------------------------------------------------------------------------
CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(WebSocketVoice),
        cv.Required(CONF_SERVER_HOST): cv.string,
        cv.Optional(CONF_SERVER_PORT, default=8765): cv.port,
        cv.Optional(CONF_MICROPHONE): cv.use_id(
            cv.global_ns.microphone.Microphone
        ),
        cv.Optional(CONF_SPEAKER): cv.use_id(
            cv.global_ns.speaker.Speaker
        ),
    }
).extend(cv.COMPONENT_SCHEMA)

# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------
async def to_code(config):
    """Generate C++ code for the WebSocketVoice component."""
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_server_host(config[CONF_SERVER_HOST]))
    cg.add(var.set_server_port(config[CONF_SERVER_PORT]))

    if CONF_MICROPHONE in config:
        mic = await cg.get_variable(config[CONF_MICROPHONE])
        cg.add(var.set_microphone(mic))

    if CONF_SPEAKER in config:
        spk = await cg.get_variable(config[CONF_SPEAKER])
        cg.add(var.set_speaker(spk))

    cg.add_define("USE_WEBSOCKET_VOICE")
