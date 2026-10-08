"""REST Inventory Service.

Exposes POST /check-inventory on port 5001. Stock is kept in memory and is
never shared with the Order Service; callers must use HTTP.
"""

import logging
import os
import time

from flask import Flask, jsonify, request

logger = logging.getLogger("rest.inventory")

# Sample catalog. item_103 exists but has no units, so it is insufficient
# rather than missing.
INVENTORY = {
    "item_101": 10,
    "item_102": 5,
    "item_103": 0,
}


def inventory_delay_seconds() -> float:
    """Read INVENTORY_DELAY on each request so a restart can change it."""
    raw = os.environ.get("INVENTORY_DELAY", "0")
    try:
        delay = float(raw)
    except ValueError:
        logger.error("Invalid INVENTORY_DELAY=%r; using 0", raw)
        return 0.0
    if delay < 0:
        logger.error("Negative INVENTORY_DELAY=%s; using 0", delay)
        return 0.0
    return delay


def parse_check_request(data):
    """Validate a check-inventory body. Return ((item_id, quantity), error)."""
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object"
    if "item_id" not in data or "quantity" not in data:
        return None, "item_id and quantity are required"
    item_id = data["item_id"]
    quantity = data["quantity"]
    if not isinstance(item_id, str) or not item_id.strip():
        return None, "item_id must be a non-empty string"
    # bool is a subclass of int; reject it explicitly.
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return None, "quantity must be a positive integer"
    return (item_id.strip(), quantity), None


def evaluate_inventory(item_id, quantity):
    """Decide availability without mutating stock."""
    if item_id not in INVENTORY:
        return {
            "available": False,
            "item_id": item_id,
            "error": "Item does not exist",
        }, 404

    available_quantity = INVENTORY[item_id]
    if quantity > available_quantity:
        return {
            "available": False,
            "item_id": item_id,
            "requested_quantity": quantity,
            "available_quantity": available_quantity,
            "error": "Insufficient inventory",
        }, 409

    return {
        "available": True,
        "item_id": item_id,
        "requested_quantity": quantity,
        "available_quantity": available_quantity,
    }, 200


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    @app.post("/check-inventory")
    def check_inventory():
        logger.info("Received POST /check-inventory")
        delay = inventory_delay_seconds()
        if delay:
            logger.info("Delaying inventory check by %s seconds", delay)
            time.sleep(delay)

        payload = request.get_json(silent=True)
        parsed, error = parse_check_request(payload)
        if error:
            logger.error("Invalid inventory request: %s", error)
            return jsonify({"error": error}), 400

        item_id, quantity = parsed
        body, status = evaluate_inventory(item_id, quantity)
        if status == 200:
            logger.info(
                "Inventory available for %s quantity %s (on hand %s)",
                item_id,
                quantity,
                body["available_quantity"],
            )
        else:
            logger.warning(
                "Inventory check failed for %s quantity %s: %s",
                item_id,
                quantity,
                body.get("error"),
            )
        return jsonify(body), status

    return app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    port = int(os.environ.get("PORT", "5001"))
    app = create_app()
    logger.info(
        "REST inventory service listening on http://127.0.0.1:%s (pid %s)",
        port,
        os.getpid(),
    )
    # threaded=True keeps the process accepting requests. Reloader is off so
    # this process stays the one the Order Service is talking to.
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
