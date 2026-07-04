import asyncio
import os
import json
import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from croo import AgentClient, Config, EventType, DeliverableType, DeliverOrderRequest
from sourceattest_logic import run_attest, SourceAttestInput
from dataclasses import asdict

client = AgentClient(Config(
    base_url=os.environ["CROO_API_URL"],
    ws_url=os.environ["CROO_WS_URL"],
), os.environ["SOURCEATTEST_API_KEY"])

async def main():
    stream = await client.connect_websocket()

    def on_negotiation(e):
        async def _handle():
            result = await client.accept_negotiation(e.negotiation_id)
            print(f"[SourceAttest] Order created: {result.order.order_id}")
        asyncio.create_task(_handle())

    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)

    def on_paid(e):
        async def _handle():
            try:
                order = await client.get_order(e.order_id)
                negotiation = await client.get_negotiation(order.negotiation_id)
                payload = json.loads(negotiation.requirements)
                inp = SourceAttestInput(
                    source_id=payload["source_id"],
                    quality_score=payload["quality_score"],
                    passed=payload["passed"],
                    flags=payload.get("flags") or [f"flag_{i}" for i in range(payload.get("flags_count", 0))],
                    data_hash=payload["data_hash"],
                    checked_at=payload.get("checked_at", ""),
                    requester_agent_id=payload.get("requester_agent_id", "did:croo:trustgate:placeholder"),
                )
                cert_output = run_attest(inp)
                cert = asdict(cert_output)
                await client.deliver_order(e.order_id, DeliverOrderRequest(
                    deliverable_type=DeliverableType.SCHEMA,
                    deliverable_text=json.dumps(cert),
                ))
                print(f"[SourceAttest] Certificate issued: {cert['cert_id']}")
            except Exception as err:
                print(f"[SourceAttest] Error: {err}")
        asyncio.create_task(_handle())

    stream.on(EventType.ORDER_PAID, on_paid)

    print("[SourceAttest] Online — listening for orders")
    stop = asyncio.Event()
    await stop.wait()

asyncio.run(main())