"""Run the REST and gRPC success, failure, and recovery scenarios.

Starts the real service processes, calls them with curl, and prints the
responses plus whether each Order Service process is still alive.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURL_TIMEOUT = "10"

REST_INVENTORY_PORT = 5001
REST_ORDER_PORT = 5000
GRPC_INVENTORY_PORT = 50051
GRPC_ORDER_PORT = 5002


class Service:
    def __init__(self, module, env, log_file):
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env.update(env)
        self.log_file = log_file
        self.module = module
        self.proc = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=ROOT,
            env=child_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    @property
    def pid(self):
        return self.proc.pid

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=3)


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"127.0.0.1:{port} did not start listening within {timeout}s")


def wait_until_closed(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_open(port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"127.0.0.1:{port} is still in use")


def require_ports_free(ports):
    busy = [port for port in ports if port_open(port)]
    if busy:
        listed = ", ".join(str(port) for port in busy)
        raise SystemExit(
            f"Cannot start the demo because these ports are already in use: {listed}. "
            "Stop the other process and run python demo.py again."
        )


def curl_order(port, item_id, quantity):
    command = [
        "curl",
        "-sS",
        "-m",
        CURL_TIMEOUT,
        "-w",
        "\nHTTP_STATUS:%{http_code}\n",
        "-X",
        "POST",
        f"http://127.0.0.1:{port}/orders",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps({"item_id": item_id, "quantity": quantity}),
    ]
    print("$ " + " ".join(command))
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    status = _status_from_curl(completed.stdout)
    body = _body_from_curl(completed.stdout)
    return status, body


def _status_from_curl(output):
    for line in output.splitlines():
        if line.startswith("HTTP_STATUS:"):
            return int(line.split(":", 1)[1])
    return None


def _body_from_curl(output):
    text = "\n".join(line for line in output.splitlines() if not line.startswith("HTTP_STATUS:"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def expect(condition, message):
    if not condition:
        raise SystemExit(f"DEMO FAILED: {message}")
    print(f"PASS: {message}")


def print_log(path, title):
    print(f"\n----- {title} -----")
    text = path.read_text(errors="replace").strip()
    print(text if text else "(no log output)")
    print("----- end log -----\n")


def start_service(module, env, log_dir, name):
    log_path = log_dir / f"{name}.log"
    handle = open(log_path, "w", encoding="utf-8")
    service = Service(module, env, handle)
    service.log_path = log_path
    service.log_handle = handle
    return service


def demo_rest(log_dir):
    print("\n========== REST ==========")
    inventory = start_service(
        "rest.inventory_service",
        {"INVENTORY_DELAY": "0"},
        log_dir,
        "rest-inventory-a",
    )
    order = start_service("rest.order_service", {}, log_dir, "rest-order")
    order_pid = order.pid
    try:
        wait_for_port(REST_INVENTORY_PORT)
        wait_for_port(REST_ORDER_PORT)
        print(f"REST inventory pid {inventory.pid} on {REST_INVENTORY_PORT}")
        print(f"REST order pid {order_pid} on {REST_ORDER_PORT}")

        print("\n--- Scenario A: successful order (item_101 x 2) ---")
        status, body = curl_order(REST_ORDER_PORT, "item_101", 2)
        expect(status == 200, f"REST success returned HTTP {status}")
        expect(body.get("status") == "accepted" and body.get("available") is True, f"REST success body {body}")
        expect(body.get("available_quantity") == 10, f"REST available_quantity is {body.get('available_quantity')}")

        print("\n--- Scenario B: unavailable inventory (item_103 x 1) ---")
        status, body = curl_order(REST_ORDER_PORT, "item_103", 1)
        expect(status == 409, f"REST insufficient inventory returned HTTP {status}")
        expect(body.get("available") is False, f"REST insufficient body {body}")
        expect("insufficient" in body.get("error", "").lower(), f"REST error text {body}")

        print("\n--- Scenario C: timeout (INVENTORY_DELAY=5, client timeout 2s) ---")
        inventory.stop()
        wait_until_closed(REST_INVENTORY_PORT)
        inventory = start_service(
            "rest.inventory_service",
            {"INVENTORY_DELAY": "5"},
            log_dir,
            "rest-inventory-delay",
        )
        wait_for_port(REST_INVENTORY_PORT)
        started = time.perf_counter()
        status, body = curl_order(REST_ORDER_PORT, "item_101", 2)
        elapsed = time.perf_counter() - started
        expect(status == 504, f"REST timeout returned HTTP {status}")
        expect("timed out" in body.get("error", "").lower(), f"REST timeout body {body}")
        expect(elapsed < 4, f"REST timeout elapsed {elapsed:.2f}s (expected about 2s, not 5s)")
        expect(order.alive() and order.pid == order_pid, f"REST order pid {order_pid} still alive after timeout")
        print_log(inventory.log_path, "REST inventory service log during 5s delay")

        print("\n--- Scenario D: recovery (restart inventory only, INVENTORY_DELAY=0) ---")
        inventory.stop()
        wait_until_closed(REST_INVENTORY_PORT)
        inventory = start_service(
            "rest.inventory_service",
            {"INVENTORY_DELAY": "0"},
            log_dir,
            "rest-inventory-recovered",
        )
        wait_for_port(REST_INVENTORY_PORT)
        status, body = curl_order(REST_ORDER_PORT, "item_101", 2)
        expect(status == 200, f"REST recovery returned HTTP {status}")
        expect(body.get("status") == "accepted" and body.get("available_quantity") == 10, f"REST recovery body {body}")
        expect(order.alive() and order.pid == order_pid, f"REST order pid {order_pid} still alive after recovery")
        print_log(order.log_path, "REST order service log")
    finally:
        inventory.stop()
        order.stop()
        order.log_handle.close()


def demo_grpc(log_dir):
    print("\n========== gRPC ==========")
    inventory = start_service(
        "grpc_version.inventory_service",
        {"INVENTORY_DELAY": "0"},
        log_dir,
        "grpc-inventory-a",
    )
    order = start_service("grpc_version.order_service", {}, log_dir, "grpc-order")
    order_pid = order.pid
    try:
        wait_for_port(GRPC_INVENTORY_PORT)
        wait_for_port(GRPC_ORDER_PORT)
        print(f"gRPC inventory pid {inventory.pid} on {GRPC_INVENTORY_PORT}")
        print(f"gRPC order HTTP entry pid {order_pid} on {GRPC_ORDER_PORT}")

        print("\n--- Scenario A: successful order (item_101 x 2), internal call is gRPC ---")
        status, body = curl_order(GRPC_ORDER_PORT, "item_101", 2)
        expect(status == 200, f"gRPC success returned HTTP {status}")
        expect(body.get("status") == "accepted" and body.get("available") is True, f"gRPC success body {body}")
        expect(body.get("available_quantity") == 10, f"gRPC available_quantity is {body.get('available_quantity')}")

        print("\n--- Scenario B: unavailable inventory (item_103 x 1) ---")
        status, body = curl_order(GRPC_ORDER_PORT, "item_103", 1)
        expect(status == 409, f"gRPC insufficient inventory returned HTTP {status}")
        expect(body.get("grpc_status") == "FAILED_PRECONDITION", f"gRPC status body {body}")
        expect(body.get("available") is False, f"gRPC insufficient body {body}")

        print("\n--- Scenario C: deadline exceeded (INVENTORY_DELAY=5, deadline 2s) ---")
        inventory.stop()
        wait_until_closed(GRPC_INVENTORY_PORT)
        inventory = start_service(
            "grpc_version.inventory_service",
            {"INVENTORY_DELAY": "5"},
            log_dir,
            "grpc-inventory-delay",
        )
        wait_for_port(GRPC_INVENTORY_PORT)
        started = time.perf_counter()
        status, body = curl_order(GRPC_ORDER_PORT, "item_101", 2)
        elapsed = time.perf_counter() - started
        expect(status == 504, f"gRPC deadline returned HTTP {status}")
        expect(body.get("grpc_status") == "DEADLINE_EXCEEDED", f"gRPC deadline body {body}")
        expect(elapsed < 4, f"gRPC deadline elapsed {elapsed:.2f}s (expected about 2s, not 5s)")
        expect(order.alive() and order.pid == order_pid, f"gRPC order pid {order_pid} still alive after deadline")
        print_log(inventory.log_path, "gRPC inventory service log during 5s delay")

        print("\n--- Scenario D: recovery (restart inventory only, INVENTORY_DELAY=0) ---")
        inventory.stop()
        wait_until_closed(GRPC_INVENTORY_PORT)
        inventory = start_service(
            "grpc_version.inventory_service",
            {"INVENTORY_DELAY": "0"},
            log_dir,
            "grpc-inventory-recovered",
        )
        wait_for_port(GRPC_INVENTORY_PORT)
        status, body = curl_order(GRPC_ORDER_PORT, "item_101", 2)
        expect(status == 200, f"gRPC recovery returned HTTP {status}")
        expect(body.get("status") == "accepted" and body.get("available_quantity") == 10, f"gRPC recovery body {body}")
        expect(order.alive() and order.pid == order_pid, f"gRPC order pid {order_pid} still alive after recovery")
        print_log(order.log_path, "gRPC order service log")
        print_log(inventory.log_path, "gRPC inventory service log after recovery")
    finally:
        inventory.stop()
        order.stop()
        order.log_handle.close()


def main():
    stubs = ROOT / "grpc_version" / "inventory_pb2.py"
    if not stubs.is_file():
        raise SystemExit(
            "Generated protobuf modules are missing. From the repository root run:\n"
            "python -m grpc_tools.protoc -I grpc_version --python_out=grpc_version "
            "--grpc_python_out=grpc_version grpc_version/inventory.proto"
        )
    require_ports_free([REST_ORDER_PORT, REST_INVENTORY_PORT, GRPC_ORDER_PORT, GRPC_INVENTORY_PORT])
    with tempfile.TemporaryDirectory(prefix="module3-demo-") as temp:
        log_dir = Path(temp)
        demo_rest(log_dir)
        demo_grpc(log_dir)
    print("\nAll demo scenarios passed.")


if __name__ == "__main__":
    main()
