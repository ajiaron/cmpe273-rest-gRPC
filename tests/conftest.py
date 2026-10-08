"""Shared helpers for in-process Flask servers."""

import threading

import pytest
from werkzeug.serving import make_server


@pytest.fixture(autouse=True)
def reset_inventory_delay(monkeypatch):
    """Each test starts from no artificial delay unless it sets one."""
    monkeypatch.setenv("INVENTORY_DELAY", "0")


class FlaskServer:
    """Run a Flask app on a background thread.

    port 0 asks the OS for an unused port. Pass a fixed port when a restarted
    Inventory Service must come back on the address the Order Service already has.
    """

    def __init__(self, app, port: int = 0):
        self._server = make_server("127.0.0.1", port, app, threaded=True)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._stopped = False

    def start(self):
        self._thread.start()
        return self

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._server.shutdown()
        self._thread.join(timeout=10)
