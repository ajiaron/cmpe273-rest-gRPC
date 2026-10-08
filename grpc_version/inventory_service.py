"""gRPC Inventory Service.

Implements InventoryService.CheckInventory on port 50051. Stock is local to
this process. The Order Service reaches it only through the gRPC stub.
"""

import logging
import os
import time
from concurrent import futures

import grpc

from grpc_version.load_stubs import inventory_pb2, inventory_pb2_grpc

logger = logging.getLogger("grpc.inventory")

INVENTORY = {
    "item_101": 10,
    "item_102": 5,
    "item_103": 0,
}


def inventory_delay_seconds() -> float:
    """Read INVENTORY_DELAY on each call so a restart can change it."""
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


class InventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    def CheckInventory(self, request, context):
        logger.info(
            "Received CheckInventory item_id=%r quantity=%s",
            request.item_id,
            request.quantity,
        )
        delay = inventory_delay_seconds()
        if delay:
            logger.info("Delaying inventory check by %s seconds", delay)
            time.sleep(delay)

        item_id = request.item_id.strip() if isinstance(request.item_id, str) else ""
        quantity = request.quantity

        if not item_id or quantity <= 0:
            message = "item_id must be a non-empty string and quantity must be a positive integer"
            logger.error("Invalid inventory request: item_id=%r quantity=%r", request.item_id, quantity)
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, message)

        if item_id not in INVENTORY:
            message = f"Item {item_id} does not exist"
            logger.warning("Inventory item not found: %s", item_id)
            context.abort(grpc.StatusCode.NOT_FOUND, message)

        available_quantity = INVENTORY[item_id]
        if quantity > available_quantity:
            message = (
                f"Insufficient inventory for {item_id}: "
                f"requested {quantity}, available {available_quantity}"
            )
            logger.warning(message)
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, message)

        logger.info(
            "Inventory available for %s quantity %s (on hand %s)",
            item_id,
            quantity,
            available_quantity,
        )
        return inventory_pb2.CheckInventoryResponse(
            available=True,
            available_quantity=available_quantity,
        )


def create_server(port: int = 0):
    """Return (server, bound_port). The server is not started."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(), server)
    bound = server.add_insecure_port(f"127.0.0.1:{port}")
    if bound == 0:
        raise OSError(f"Could not bind gRPC inventory service to 127.0.0.1:{port}")
    return server, bound


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    port = int(os.environ.get("PORT", "50051"))
    server, bound = create_server(port)
    server.start()
    logger.info(
        "gRPC inventory service listening on 127.0.0.1:%s (pid %s)",
        bound,
        os.getpid(),
    )
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down gRPC inventory service")
        server.stop(grace=1).wait(timeout=5)


if __name__ == "__main__":
    main()
