"""ESPHome custom component: web_socket_voice.

Replaces the built-in voice_assistant component with a WebSocket-based
streaming component that talks directly to a Python server (not Home
Assistant).  Uses ESP-IDF's built-in ``esp_websocket_client`` and
``cJSON`` — no external PlatformIO libraries needed.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID

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

    # No external libraries needed — esp_websocket_client and cJSON are
    # part of the ESP-IDF framework (already in the toolchain).
    cg.add_define("USE_WEBSOCKET_VOICE")
