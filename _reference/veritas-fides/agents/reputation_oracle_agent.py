import asyncio
import os
import json
import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from croo import AgentClient, Config, EventType, DeliverableType, DeliverOrderRequest
from reputation_oracle_logic import run_reputation_query, ReputationOracleInput
from dataclasses import asdict

client = AgentClient(Config(
    base_url=os.environ["CROO_API_URL"],
    ws_url=os.environ["CROO_WS_URL"],
), os.environ["REPUTATIONORACLE_API_KEY"])

async def main():
    stream = await client.connect_websocket()

    def on_negotiation(e):
        async def _handle():
            result = await client.accept_negotiation(e.negotiation_id)
            print(f"[ReputationOracle] Order created: {result.order.order_id}")
        asyncio.create_task(_handle())

    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)

    def on_paid(e):
        async def _handle():
            try:
                order = await client.get_order(e.order_id)
                negotiation = await client.get_negotiation(order.negotiation_id)
                payload = json.loads(negotiation.requirements)
                inp = ReputationOracleInput(
                    source_id=payload["source_id"],
                    queried_by=payload.get("queried_by", "did:croo:trustgate:placeholder"),
                )
                reputation_output = run_reputation_query(inp)
                reputation = asdict(reputation_output)
                await client.deliver_order(e.order_id, DeliverOrderRequest(
                    deliverable_type=DeliverableType.SCHEMA,
                    deliverable_text=json.dumps(reputation),
                ))
                print(f"[ReputationOracle] Reputation returned for: {payload['source_id']}")
            except Exception as err:
                print(f"[ReputationOracle] Error: {err}")
        asyncio.create_task(_handle())

    stream.on(EventType.ORDER_PAID, on_paid)

    print("[ReputationOracle] Online — listening for orders")
    stop = asyncio.Event()
    await stop.wait()

asyncio.run(main())