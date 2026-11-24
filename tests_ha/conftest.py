"""Home Assistant tests of the integration (pytest-homeassistant-custom-component), against the made-up FF1."""

import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

from fake_ff1 import Ff1  # noqa: E402

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def ff1(socket_enabled):
    """A made-up FF1 on 127.0.0.1; `ff1.port` is where it listens."""
    device = Ff1()
    server = TestServer(device.app(), host="127.0.0.1")
    await server.start_server()
    device.port = server.port
    yield device
    await device.close()
    await server.close()
