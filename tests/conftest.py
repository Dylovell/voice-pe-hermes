"""pytest configuration for voice-pe-hermes tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Add server directory to Python path so tests can import server modules
SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


def pytest_configure(config):
    """Register custom markers and set asyncio mode."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async (runs with pytest-asyncio)"
    )
