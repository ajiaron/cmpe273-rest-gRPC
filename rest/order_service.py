"""REST Order Service.

Exposes POST /orders on port 5000. Inventory is reached only through HTTP.
This module does not import or read the Inventory Service stock dictionary.
"""

import logging
import os

import requests
from flask import Flask, jsonify, request

logger = logging.getLogger("rest.order")

DEFAULT_INVENTORY_URL = "http://127.0.0.1:5001"
REQUEST_TIMEOUT_SECONDS = 2


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


def create_app(inventory_url: str | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    base_url = (inventory_url or os.environ.get("INVENTORY_URL") or DEFAULT_INVENTORY_URL).rstrip("/")

    @app.post("/orders")
    def create_order():
        payload = request.get_json(silent=True)
        parsed, error = parse_order(payload)
        if error:
            logger.error("Invalid order request: %s", error)
            return jsonify({"error": error}), 400

        item_id, quantity = parsed
        url = f"{base_url}/check-inventory"
        logger.info(
            "Checking inventory over HTTP: POST %s item_id=%s quantity=%s timeout=%ss",
            url,
            item_id,
            quantity,
            REQUEST_TIMEOUT_SECONDS,
        )

        try:
            inventory_response = requests.post(
                url,
                json={"item_id": item_id, "quantity": quantity},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout as exc:
            logger.error(
                "Inventory request timed out after %s seconds: %s",
                REQUEST_TIMEOUT_SECONDS,
                exc,
            )
            return jsonify({
                "error": "Inventory service request timed out",
                "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                "item_id": item_id,
                "quantity": quantity,
            }), 504
        except requests.exceptions.ConnectionError as exc:
            logger.error("Could not connect to inventory service at %s: %s", url, exc)
            return jsonify({
                "error": "Inventory service is unavailable",
            }), 503
        except requests.exceptions.RequestException as exc:
            logger.exception("Inventory request failed: %s", exc)
            return jsonify({
                "error": "Inventory service request failed",
            }), 503
        except Exception:
            logger.exception("Unhandled error while creating order")
            return jsonify({"error": "Internal server error"}), 500

        try:
            inventory_body = inventory_response.json()
        except ValueError:
            logger.error(
                "Inventory service returned non-JSON (status %s): %s",
                inventory_response.status_code,
                inventory_response.text,
            )
            return jsonify({"error": "Invalid response from inventory service"}), 502

        if inventory_response.status_code == 200:
            logger.info(
                "Order accepted for %s quantity %s (available_quantity=%s)",
                item_id,
                quantity,
                inventory_body.get("available_quantity"),
            )
            return jsonify({
                "status": "accepted",
                "item_id": item_id,
                "quantity": quantity,
                "available": inventory_body.get("available", True),
                "available_quantity": inventory_body.get("available_quantity"),
                "message": "Order accepted",
            }), 200

        if inventory_response.status_code in (400, 404, 409):
            error_message = inventory_body.get("error", "Inventory check failed")
            logger.error(
                "Inventory rejected order for %s quantity %s: HTTP %s %s",
                item_id,
                quantity,
                inventory_response.status_code,
                error_message,
            )
            body = {
                "status": "rejected",
                "item_id": item_id,
                "quantity": quantity,
                "error": error_message,
            }
            if "available" in inventory_body:
                body["available"] = inventory_body["available"]
            if "available_quantity" in inventory_body:
                body["available_quantity"] = inventory_body["available_quantity"]
            return jsonify(body), inventory_response.status_code

        logger.error(
            "Unexpected inventory status %s for %s: %s",
            inventory_response.status_code,
            item_id,
            inventory_body,
        )
        return jsonify({"error": "Unexpected response from inventory service"}), 502

    return app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    port = int(os.environ.get("PORT", "5000"))
    inventory_url = os.environ.get("INVENTORY_URL", DEFAULT_INVENTORY_URL)
    app = create_app(inventory_url)
    logger.info(
        "REST order service listening on http://127.0.0.1:%s (pid %s), inventory=%s",
        port,
        os.getpid(),
        inventory_url,
    )
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
