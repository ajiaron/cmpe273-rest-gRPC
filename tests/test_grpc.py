"""gRPC Order -> Inventory tests. Orders enter through the HTTP entry point."""

import time

import requests

from grpc_version.inventory_service import create_server
from grpc_version.order_service import create_app as create_order_app
from tests.conftest import FlaskServer


def _post_order(port, item_id, quantity, timeout=10):
    return requests.post(
        f"http://127.0.0.1:{port}/orders",
        json={"item_id": item_id, "quantity": quantity},
        timeout=timeout,
    )


class GrpcInventory:
    def __init__(self, port=0):
        self.server, self.port = create_server(port)

    def start(self):
        self.server.start()
        return self

    def stop(self):
        self.server.stop(0).wait(timeout=5)


def _start_inventory_on(port, attempts=25):
    last_error = None
    for _ in range(attempts):
        try:
            return GrpcInventory(port).start()
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise last_error


def test_successful_grpc_inventory_lookup():
    inventory = GrpcInventory().start()
    order = FlaskServer(create_order_app(f"127.0.0.1:{inventory.port}")).start()
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


def test_unavailable_grpc_inventory():
    inventory = GrpcInventory().start()
    order = FlaskServer(create_order_app(f"127.0.0.1:{inventory.port}")).start()
    try:
        response = _post_order(order.port, "item_103", 1)
        body = response.json()
        assert response.status_code == 409
        assert body["status"] == "rejected"
        assert body["grpc_status"] == "FAILED_PRECONDITION"
        assert body["item_id"] == "item_103"
        assert body["quantity"] == 1
        assert body["available"] is False
        assert "insufficient" in body["error"].lower()
        assert order.is_alive()
    finally:
        order.stop()
        inventory.stop()


def test_grpc_deadline_exceeded(monkeypatch):
    monkeypatch.setenv("INVENTORY_DELAY", "5")
    inventory = GrpcInventory().start()
    order = FlaskServer(create_order_app(f"127.0.0.1:{inventory.port}")).start()
    try:
        started = time.perf_counter()
        response = _post_order(order.port, "item_101", 2)
        elapsed = time.perf_counter() - started
        body = response.json()
        assert response.status_code == 504
        assert body["grpc_status"] == "DEADLINE_EXCEEDED"
        assert "deadline exceeded" in body["error"].lower()
        assert body["timeout_seconds"] == 2
        assert 1.5 <= elapsed < 4
        assert order.is_alive()
    finally:
        order.stop()
        inventory.stop()


def test_grpc_order_service_recovers_after_deadline(monkeypatch):
    monkeypatch.setenv("INVENTORY_DELAY", "5")
    inventory = GrpcInventory().start()
    inventory_port = inventory.port
    order = FlaskServer(create_order_app(f"127.0.0.1:{inventory_port}")).start()
    try:
        timed_out = _post_order(order.port, "item_101", 2)
        assert timed_out.status_code == 504
        assert timed_out.json()["grpc_status"] == "DEADLINE_EXCEEDED"
        assert order.is_alive()

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
