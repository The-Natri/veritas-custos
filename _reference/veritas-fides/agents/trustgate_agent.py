import asyncio
import os
import json
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

# Create the logs directory before basicConfig is run to prevent FileNotFoundError
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/trustgate.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("trustgate")

MAX_CONCURRENT_ORDERS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ORDERS)

import time as _time
from collections import defaultdict
_last_order_time: dict[str, float] = defaultdict(float)
MIN_ORDER_INTERVAL_SEC = 10.0  # minimum seconds between orders from same requester

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from croo import AgentClient, Config, EventType, DeliverableType, DeliverOrderRequest
from orchestrator import run_pipeline, run_pipeline_live, _retry
from dataclasses import asdict
from reputation_oracle_logic import get_store

client = AgentClient(Config(
    base_url=os.environ["CROO_API_URL"],
    ws_url=os.environ["CROO_WS_URL"],
), os.environ["TRUSTGATE_API_KEY"])

requester_client = AgentClient(Config(
    base_url=os.environ["CROO_API_URL"],
    ws_url=os.environ["CROO_WS_URL"],
), os.environ["TRUSTGATE_API_KEY"])

async def main():
    stream = await client.connect_websocket()

    def on_negotiation(e):
        async def _handle():
            logger.info("Negotiation received: %s", e.negotiation_id)
            result = await _retry(lambda: client.accept_negotiation(e.negotiation_id))
            logger.info("Order created: %s", result.order_id)
        asyncio.create_task(_handle())

    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)

    def on_paid(e):
        async def _handle():
            if _semaphore.locked():
                logger.warning("Concurrency limit reached (%d active orders), queuing order=%s", MAX_CONCURRENT_ORDERS, e.order_id)
            async with _semaphore:
                try:
                    requester_id = e.order_id  # use order_id as proxy until requester_agent_id is on event
                    now = _time.monotonic()
                    try:
                        order = await _retry(lambda: client.get_order(e.order_id))
                        requester_id = order.requester_agent_id or e.order_id
                    except Exception:
                        pass  # fall back to order_id as key

                    last = _last_order_time[requester_id]
                    if now - last < MIN_ORDER_INTERVAL_SEC:
                        wait_time = MIN_ORDER_INTERVAL_SEC - (now - last)
                        logger.warning("Rate limit: requester=%s cooling down %.1fs", requester_id, wait_time)
                        await asyncio.sleep(wait_time)

                    _last_order_time[requester_id] = _time.monotonic()

                    # Retrieve negotiation using the fetched order
                    negotiation = await _retry(lambda: client.get_negotiation(order.negotiation_id))
                    payload = json.loads(negotiation.requirements)
                    logger.info("Order paid: order=%s source=%s", e.order_id, payload.get("source_id", "unknown"))
                    result = await run_pipeline_live(payload, requester_client)
                    await _retry(lambda: client.deliver_order(e.order_id, DeliverOrderRequest(
                        deliverable_type=DeliverableType.SCHEMA,
                        deliverable_text=json.dumps(asdict(result)),
                    )))
                    store = get_store()
                    store.add_certificate({
                        "source_id": result.source_id,
                        "quality_score": result.quality_score,
                        "passed": result.passed,
                        "data_hash": result.data_hash,
                        "attested_at": result.checked_at,
                        "cert_id": result.cert_id,
                    })
                    logger.info("Delivered order=%s score=%s passed=%s cert=%s", e.order_id, result.quality_score, result.passed, result.cert_id)
                except Exception as err:
                    logger.error("Pipeline error order=%s %s: %s", e.order_id, type(err).__name__, err, exc_info=True)
                    try:
                        await client.reject_order(e.order_id, f"TrustGate internal error: {type(err).__name__}: {str(err)[:200]}")
                    except Exception:
                        pass
        asyncio.create_task(_handle())

    stream.on(EventType.ORDER_PAID, on_paid)

    logger.info("Online — listening for orders")
    stop = asyncio.Event()
    await stop.wait()

asyncio.run(main())