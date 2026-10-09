# Module 3: REST & gRPC Service Contracts

This project implements the same Order → Inventory interaction twice: once over REST/HTTP with JSON, and once over gRPC. Both versions accept an order with `item_id` and `quantity`, ask Inventory whether stock is available, and handle slow Inventory responses with a timeout (REST) or deadline (gRPC) without crashing the Order Service.

**Language:** Python

**Sample inventory**

| item_id   | available quantity |
|-----------|--------------------|
| item_101  | 10                 |
| item_102  | 5                  |
| item_103  | 0                  |

---

## Setup

```bash
pip install -r requirements.txt
```

Generate the gRPC stubs from the protobuf contract (creates `grpc_version/inventory_pb2.py` and `grpc_version/inventory_pb2_grpc.py`):

```bash
python -m grpc_tools.protoc -I grpc_version --python_out=grpc_version --grpc_python_out=grpc_version grpc_version/inventory.proto
```

If those stub files are already present in the repository, you can skip regeneration unless you change `inventory.proto`.

**Quick end-to-end check** (starts both stacks, runs success / unavailable / timeout / recovery):

```bash
python demo.py
```

---

## Running the REST version

Use two terminals from the repository root.

**Terminal 1 — Inventory Service (port 5001):**

```bash
python -m rest.inventory_service
```

**Terminal 2 — Order Service (port 5000):**

```bash
python -m rest.order_service
```

Order calls Inventory with `POST http://127.0.0.1:5001/check-inventory` and a **2-second** HTTP timeout.

To demonstrate a slow Inventory response (used for timeout evidence below):

```bash
# PowerShell
$env:INVENTORY_DELAY="5"; python -m rest.inventory_service

# bash
INVENTORY_DELAY=5 python -m rest.inventory_service
```

---

## Running the gRPC version

Use two terminals from the repository root. Generate stubs first if they are missing (see Setup).

**Terminal 1 — Inventory Service (gRPC port 50051):**

```bash
python -m grpc_version.inventory_service
```

**Terminal 2 — Order Service (HTTP entry on port 5002):**

```bash
python -m grpc_version.order_service
```

`POST /orders` on port 5002 is only a local way to submit an order for demos and curl. The **Order → Inventory** call uses gRPC (`InventoryService.CheckInventory`) with a **2-second** deadline.

Slow Inventory for deadline evidence:

```bash
# PowerShell
$env:INVENTORY_DELAY="5"; python -m grpc_version.inventory_service

# bash
INVENTORY_DELAY=5 python -m grpc_version.inventory_service
```

---

## Example of a successful REST request and response

```bash
curl -sS -X POST http://127.0.0.1:5000/orders ^
  -H "Content-Type: application/json" ^
  -d "{\"item_id\":\"item_101\",\"quantity\":2}"
```

**Response (HTTP 200):**

```json
{
  "status": "accepted",
  "item_id": "item_101",
  "quantity": 2,
  "available": true,
  "available_quantity": 10,
  "message": "Order accepted"
}
```

---

## Example of a successful gRPC request and response

Submit the order through the Order Service HTTP entry; Inventory is checked over gRPC.

```bash
curl -sS -X POST http://127.0.0.1:5002/orders ^
  -H "Content-Type: application/json" ^
  -d "{\"item_id\":\"item_101\",\"quantity\":2}"
```

**Response (HTTP 200):**

```json
{
  "status": "accepted",
  "item_id": "item_101",
  "quantity": 2,
  "available": true,
  "available_quantity": 10,
  "message": "Order accepted"
}
```

Order Service log confirms the gRPC hop:

```text
Checking inventory over gRPC: target=127.0.0.1:50051 item_id=item_101 quantity=2 deadline=2s
Order accepted for item_101 quantity 2 (available_quantity=10)
```

---

## Evidence of REST timeout handling

Client timeout is **2 seconds**. Inventory was restarted with `INVENTORY_DELAY=5` so the delay exceeds the timeout.

```bash
curl -sS -X POST http://127.0.0.1:5000/orders ^
  -H "Content-Type: application/json" ^
  -d "{\"item_id\":\"item_101\",\"quantity\":2}"
```

**Response (HTTP 504):**

```json
{
  "error": "Inventory service request timed out",
  "timeout_seconds": 2,
  "item_id": "item_101",
  "quantity": 2
}
```

Observed elapsed time was about **2.03s** (client gave up near the 2s timeout instead of waiting the full 5s delay).

Order Service log:

```text
Checking inventory over HTTP: POST http://127.0.0.1:5001/check-inventory item_id=item_101 quantity=2 timeout=2s
Inventory request timed out after 2 seconds: ... Read timed out. (read timeout=2)
"POST /orders HTTP/1.1" 504 -
```

Inventory log during the delayed request:

```text
Received POST /check-inventory
Delaying inventory check by 5.0 seconds
```

---

## Evidence of gRPC deadline handling

Client deadline is **2 seconds**. Inventory was restarted with `INVENTORY_DELAY=5`.

```bash
curl -sS -X POST http://127.0.0.1:5002/orders ^
  -H "Content-Type: application/json" ^
  -d "{\"item_id\":\"item_101\",\"quantity\":2}"
```

**Response (HTTP 504):**

```json
{
  "error": "Inventory service deadline exceeded",
  "grpc_status": "DEADLINE_EXCEEDED",
  "item_id": "item_101",
  "quantity": 2,
  "timeout_seconds": 2
}
```

Observed elapsed time was about **2.02s**. Inventory returns gRPC status `DEADLINE_EXCEEDED` when the call does not finish in time; the Order Service maps that to HTTP 504 for the curl client.

Order Service log:

```text
Checking inventory over gRPC: target=127.0.0.1:50051 item_id=item_101 quantity=2 deadline=2s
gRPC inventory call failed: DEADLINE_EXCEEDED (Deadline Exceeded)
"POST /orders HTTP/1.1" 504 -
```

---

## Evidence that the Order Service remains running after the failures

In the demo run, the REST Order Service kept the same process ID across the timeout and recovery scenarios, and then accepted a new order after Inventory was restarted without delay:

```text
PASS: REST order pid 32940 still alive after timeout
PASS: REST order pid 32940 still alive after recovery
```

Recovery response after the timeout (HTTP 200, Order process never restarted):

```json
{
  "status": "accepted",
  "item_id": "item_101",
  "quantity": 2,
  "available": true,
  "available_quantity": 10,
  "message": "Order accepted"
}
```

The gRPC Order Service likewise kept the same PID through the deadline failure and recovery:

```text
PASS: gRPC order pid 28160 still alive after deadline
PASS: gRPC order pid 28160 still alive after recovery
```

Only the Inventory Service was restarted to inject/remove the artificial delay. The Order Service stayed up and continued to handle requests.

---

## REST vs gRPC comparison

Implementing the same Order → Inventory check with REST felt familiar: JSON bodies, HTTP status codes (200 / 409 / 504), and a simple `requests` timeout. The gRPC version required a `.proto` contract and generated stubs, which added a build step but made the service interface and message fields more explicit. Error handling also differed: REST used HTTP 409 for insufficient stock, while gRPC used `FAILED_PRECONDITION` (and `DEADLINE_EXCEEDED` for slow responses) before the Order Service mapped those codes back to HTTP for curl. Both approaches handled a 5-second Inventory delay cleanly with a 2-second client bound, and in both cases the Order Service process kept running afterward. Overall, REST was quicker to wire up for this small lab, while gRPC made the shared contract and typed statuses clearer once the stubs were in place.

---

## Project layout

```text
rest/
  inventory_service.py   # POST /check-inventory (JSON)
  order_service.py       # POST /orders → HTTP Inventory call
grpc_version/
  inventory.proto        # gRPC service contract
  inventory_service.py   # CheckInventory RPC
  order_service.py       # POST /orders → gRPC Inventory call
demo.py                  # Automated success / failure / recovery demo
tests/                   # pytest coverage for both stacks
requirements.txt
```
