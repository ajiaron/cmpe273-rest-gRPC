"""Order Service entry point for the gRPC version.

POST /orders on port 5002 is only a local HTTP door for submitting an order.
The Order -> Inventory call uses a gRPC stub and a 2-second deadline.
"""

import logging
import os

import grpc
from flask import Flask, jsonify, request

from grpc_version.load_stubs import inventory_pb2, inventory_pb2_grpc

logger = logging.getLogger("grpc.order")

DEFAULT_INVENTORY_TARGET = "127.0.0.1:50051"
REQUEST_DEADLINE_SECONDS = 2

# Map handled Inventory RPC statuses onto HTTP statuses for this entry point.
GRPC_STATUS_HTTP = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.FAILED_PRECONDITION: 409,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    grpc.StatusCode.UNAVAILABLE: 503,
}


def parse_order(data):
    """Validate an order body. Return ((item_id, quantity), error)."""
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object"
    if "item_id" not in data or "quantity" not in data:
        return None, "item_id and quantity are required"
    item_id = data["item_id"]
    quantity = data["quantity"]
    if not isinstance(item_id, str) or not item_id.strip():
        return None, "item_id must be a non-empty string"
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return None, "quantity must be a positive integer"
    return (item_id.strip(), quantity), None


def create_app(inventory_target: str | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    target = inventory_target or os.environ.get("INVENTORY_GRPC_TARGET") or DEFAULT_INVENTORY_TARGET

    @app.post("/orders")
    def create_order():
        payload = request.get_json(silent=True)
        parsed, error = parse_order(payload)
        if error:
            logger.error("Invalid order request: %s", error)
            return jsonify({"error": error}), 400

        item_id, quantity = parsed
        logger.info(
            "Checking inventory over gRPC: target=%s item_id=%s quantity=%s deadline=%ss",
            target,
            item_id,
            quantity,
            REQUEST_DEADLINE_SECONDS,
        )

        try:
            rpc_request = inventory_pb2.CheckInventoryRequest(
                item_id=item_id,
                quantity=quantity,
            )
            # A new channel per order keeps this process independent of Inventory
            # restarts: the next call simply dials again.
            with grpc.insecure_channel(target) as channel:
                stub = inventory_pb2_grpc.InventoryServiceStub(channel)
                rpc_response = stub.CheckInventory(
                    rpc_request,
                    timeout=REQUEST_DEADLINE_SECONDS,
                )
        except ValueError as exc:
            logger.error("Could not build CheckInventory request: %s", exc)
            return jsonify({
                "error": "quantity is out of range",
                "grpc_status": "INVALID_ARGUMENT",
                "item_id": item_id,
                "quantity": quantity,
            }), 400
        except grpc.RpcError as exc:
            code = exc.code()
            details = exc.details() or code.name
            logger.error("gRPC inventory call failed: %s (%s)", code.name, details)
            http_status = GRPC_STATUS_HTTP.get(code, 502)
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                message = "Inventory service deadline exceeded"
            elif code == grpc.StatusCode.UNAVAILABLE:
                message = "Inventory service is unavailable"
            elif code in (
                grpc.StatusCode.INVALID_ARGUMENT,
                grpc.StatusCode.NOT_FOUND,
                grpc.StatusCode.FAILED_PRECONDITION,
            ):
                message = details
            else:
                message = f"Inventory service error: {details}"
            body = {
                "error": message,
                "grpc_status": code.name,
                "item_id": item_id,
                "quantity": quantity,
            }
            if http_status == 504:
                body["timeout_seconds"] = REQUEST_DEADLINE_SECONDS
            if http_status in (404, 409):
                body["status"] = "rejected"
            if http_status == 409:
                body["available"] = False
            return jsonify(body), http_status
        except Exception:
            logger.exception("Unhandled error while creating order")
            return jsonify({"error": "Internal server error"}), 500

        logger.info(
            "Order accepted for %s quantity %s (available_quantity=%s)",
            item_id,
            quantity,
            rpc_response.available_quantity,
        )
        return jsonify({
            "status": "accepted",
            "item_id": item_id,
            "quantity": quantity,
            "available": rpc_response.available,
            "available_quantity": rpc_response.available_quantity,
            "message": "Order accepted",
        }), 200

    return app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    port = int(os.environ.get("PORT", "5002"))
    target = os.environ.get("INVENTORY_GRPC_TARGET", DEFAULT_INVENTORY_TARGET)
    app = create_app(target)
    logger.info(
        "gRPC order service HTTP entry listening on http://127.0.0.1:%s (pid %s), inventory gRPC target=%s",
        port,
        os.getpid(),
        target,
    )
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
