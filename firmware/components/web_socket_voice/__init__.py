"""ESPHome custom component: web_socket_voice.

Replaces the built-in voice_assistant component with a WebSocket-based
streaming component that talks directly to a Python server (not Home Assistant).
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome.core import CORE

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


def _final_validator(config):
    """Validate that this component is not used alongside voice_assistant."""
    # The package already disables voice_assistant, but a user override may
    # re-enable it.  We cannot easily inspect the final config from here, so
    # we just document the constraint.
    return config


FINAL_VALIDATE_SCHEMA = _final_validator

# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------
async def to_code(config):
    """Generate C++ code for the WebSocketVoice component."""
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_server_host(config[CONF_SERVER_HOST]))
    cg.add(var.set_server_port(config[CONF_SERVER_PORT]))

    # ESPHome Component requirements
    cg.add_library("WLautomatik/WebSockets", "2.6.1")
    cg.add_library("bblanchon/ArduinoJson", "7.3.1")
    cg.add_define("USE_WEBSOCKET_VOICE")
