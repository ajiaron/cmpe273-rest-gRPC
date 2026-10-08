"""REST Order -> Inventory tests. Each test runs both services locally."""

import time

import requests

from rest.inventory_service import create_app as create_inventory_app
from rest.order_service import create_app as create_order_app
from tests.conftest import FlaskServer


def _post_order(port, item_id, quantity, timeout=10):
    return requests.post(
        f"http://127.0.0.1:{port}/orders",
        json={"item_id": item_id, "quantity": quantity},
        timeout=timeout,
    )


def _start_inventory_on(port, attempts=25):
    """Bind inventory on a specific port, retrying briefly after a restart."""
    last_error = None
    for _ in range(attempts):
        try:
            return FlaskServer(create_inventory_app(), port=port).start()
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise last_error


def test_successful_rest_inventory_lookup():
    inventory = FlaskServer(create_inventory_app()).start()
    order = FlaskServer(create_order_app(f"http://127.0.0.1:{inventory.port}")).start()
    try:
        response = _post_order(order.port, "item_101", 2)
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "accepted"
        assert body["item_id"] == "item_101"
        assert body["quantity"] == 2
        assert body["available"] is True
        assert body["available_quantity"] == 10
    finally:
        order.stop()
        inventory.stop()


def test_unavailable_rest_inventory():
    inventory = FlaskServer(create_inventory_app()).start()
    order = FlaskServer(create_order_app(f"http://127.0.0.1:{inventory.port}")).start()
    try:
        response = _post_order(order.port, "item_103", 1)
        body = response.json()
        assert response.status_code == 409
        assert body["status"] == "rejected"
        assert body["item_id"] == "item_103"
        assert body["quantity"] == 1
        assert body["available"] is False
        assert body["available_quantity"] == 0
        assert "insufficient" in body["error"].lower()
        assert order.is_alive()
    finally:
        order.stop()
        inventory.stop()


def test_rest_timeout_handling(monkeypatch):
    monkeypatch.setenv("INVENTORY_DELAY", "5")
    inventory = FlaskServer(create_inventory_app()).start()
    order = FlaskServer(create_order_app(f"http://127.0.0.1:{inventory.port}")).start()
    try:
        started = time.perf_counter()
        response = _post_order(order.port, "item_101", 2)
        elapsed = time.perf_counter() - started
        body = response.json()
        assert response.status_code == 504
        assert "timed out" in body["error"].lower()
        assert body["timeout_seconds"] == 2
        # 5 second server delay, 2 second client timeout: the response must
        # come back before the inventory sleep finishes.
        assert 1.5 <= elapsed < 4
        assert order.is_alive()
    finally:
        order.stop()
        inventory.stop()


def test_rest_order_service_recovers_after_timeout(monkeypatch):
    monkeypatch.setenv("INVENTORY_DELAY", "5")
    inventory = FlaskServer(create_inventory_app()).start()
    inventory_port = inventory.port
    order = FlaskServer(create_order_app(f"http://127.0.0.1:{inventory_port}")).start()
    try:
        timed_out = _post_order(order.port, "item_101", 2)
        assert timed_out.status_code == 504
        assert order.is_alive()

        # Restart only Inventory, on the same port, with the delay removed.
        inventory.stop()
        monkeypatch.setenv("INVENTORY_DELAY", "0")
        inventory = _start_inventory_on(inventory_port)

        recovered = _post_order(order.port, "item_101", 2)
        body = recovered.json()
        assert recovered.status_code == 200
        assert body["status"] == "accepted"
        assert body["item_id"] == "item_101"
        assert body["quantity"] == 2
        assert body["available"] is True
        assert body["available_quantity"] == 10
        assert order.is_alive()
    finally:
        order.stop()
        inventory.stop()
