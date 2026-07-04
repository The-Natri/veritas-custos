"""
TrustGate Orchestrator
Full pipeline: ReputationOracle → TrustGate checks → SourceAttest → unified response

This is the main entry point for the TrustGate agent.
SDK wrapping (negotiate_order, pay_order, deliver_order) slots in here.

Call flow per buyer request:
  1. Query ReputationOracle for historical context (pays $0.01)
  2. Run TrustGate quality checks with reputation context injected
  3. Call SourceAttest to write on-chain certificate (pays $0.02)
  4. Return unified response to buyer

Sequential PayOrder constraint: ReputationOracle payment must settle
before SourceAttest payment is initiated — AA wallet nonce collision
if concurrent. This is enforced by the await chain below.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json
import time
from dataclasses import dataclass, asdict
from croo import AgentClient, NegotiateOrderRequest, EventType

from trustgate_checks import (
    TrustGateInput,
    TrustGateOutput,
    run_checks,
)
from sourceattest_logic import (
    SourceAttestInput,
    SourceAttestOutput,
    run_attest,
    serialize_output as sa_serialize,
)
from reputation_oracle_logic import (
    ReputationOracleInput,
    ReputationOracleOutput,
    run_reputation_query,
    get_store,
    serialize_output as ro_serialize,
)


# ---------------------------------------------------------------------------
# Retry Utility
# ---------------------------------------------------------------------------
async def _retry(coro_fn, retries: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """
    Retry an async callable on transient errors with exponential backoff.
    coro_fn: zero-argument async callable, e.g. lambda: client.pay_order(order_id)
    Raises the last exception if all retries are exhausted.
    Does NOT retry on: ValueError, AssertionError, TimeoutError — these are logic errors.
    """
    import asyncio
    last_exc = None
    wait = delay
    for attempt in range(retries):
        try:
            return await coro_fn()
        except (ValueError, AssertionError, TimeoutError):
            raise  # never retry logic errors
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(wait)
                wait *= backoff
    raise last_exc


# ---------------------------------------------------------------------------
# Timeout Config
# ---------------------------------------------------------------------------
PIPELINE_STEP_TIMEOUT_SEC = 120  # 2 minutes per step


# ---------------------------------------------------------------------------
# Agent identifiers (placeholders until SDK registration)
# ---------------------------------------------------------------------------
TRUSTGATE_AGENT_ID    = "did:croo:trustgate:placeholder"
SOURCEATTEST_AGENT_ID = "did:croo:sourceattest:placeholder"
REPORACLE_AGENT_ID    = "did:croo:reporacle:placeholder"


# ---------------------------------------------------------------------------
# Unified buyer response
# ---------------------------------------------------------------------------
@dataclass
class TrustGateResponse:
    source_id: str
    quality_score: int
    passed: bool
    flags: list[str]
    trust_tier: str
    pass_rate: float
    total_historical_checks: int
    trend: str
    data_hash: str
    cert_tx_hash: str
    cert_id: str
    checked_at: str
    pipeline_ms: int        # total wall-clock time for full pipeline


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    raw_input: dict,
    live: bool = False,
) -> TrustGateResponse:
    """
    raw_input: matches TrustGateInput field names (from buyer's CAP payload)
    live=False: simulation mode (local dev, judge dry-run)
    live=True:  real SDK calls (wire in after SDK access lands)
    """
    t_start = time.time()

    # ── Step 1: ReputationOracle query ────────────────────────────────────
    # Called FIRST — historical context enriches the final response
    # SDK live mode: negotiate_order → pay_order($0.01) → get_delivery()
    rep_input = ReputationOracleInput(
        source_id=raw_input["source_id"],
        queried_by=TRUSTGATE_AGENT_ID,
    )
    rep_output: ReputationOracleOutput = run_reputation_query(rep_input)

    reputation_context = {
        "trust_tier": rep_output.trust_tier,
        "pass_rate": rep_output.pass_rate,
        "total_checks": rep_output.total_checks,
        "trend": rep_output.trend,
        "avg_quality_score": rep_output.avg_quality_score,
    }

    # ── Step 2: TrustGate quality checks ──────────────────────────────────
    tg_input = TrustGateInput(
        source_id=raw_input["source_id"],
        data_type=raw_input["data_type"],
        payload=raw_input["payload"],
        metadata=raw_input.get("metadata", {}),
        reputation_context=reputation_context,
    )
    tg_output: TrustGateOutput = run_checks(tg_input)

    # ── Step 3: SourceAttest certificate write ─────────────────────────────
    # Sequential — must complete after ReputationOracle payment settles
    # SDK live mode: negotiate_order → pay_order($0.02) → get_delivery()
    sa_input = SourceAttestInput(
        source_id=tg_output.source_id,
        quality_score=tg_output.quality_score,
        passed=tg_output.passed,
        flags=tg_output.flags,
        data_hash=tg_output.data_hash,
        checked_at=tg_output.checked_at,
        requester_agent_id=TRUSTGATE_AGENT_ID,
    )
    sa_output: SourceAttestOutput = run_attest(
        sa_input,
        issuer_agent_id=SOURCEATTEST_AGENT_ID,
        live=live,
    )

    # ── Step 4: Feed new cert into ReputationOracle store ─────────────────
    # In live mode this happens on-chain automatically via SourceAttest
    # In sim mode we update local store so reputation accumulates across runs
    if not live:
        store = get_store()
        store.add_certificate({
            "source_id": sa_output.source_id,
            "quality_score": sa_output.quality_score,
            "passed": sa_output.passed,
            "data_hash": sa_output.data_hash,
            "attested_at": sa_output.attested_at,
            "cert_id": sa_output.cert_id,
        })

    pipeline_ms = int((time.time() - t_start) * 1000)

    # ── Step 5: Unified response to buyer ─────────────────────────────────
    return TrustGateResponse(
        source_id=tg_output.source_id,
        quality_score=tg_output.quality_score,
        passed=tg_output.passed,
        flags=tg_output.flags,
        trust_tier=rep_output.trust_tier,
        pass_rate=rep_output.pass_rate,
        total_historical_checks=rep_output.total_checks,
        trend=rep_output.trend,
        data_hash=tg_output.data_hash,
        cert_tx_hash=sa_output.tx_hash,
        cert_id=sa_output.cert_id,
        checked_at=tg_output.checked_at,
        pipeline_ms=pipeline_ms,
    )


async def run_pipeline_live(
    raw_input: dict,
    requester_client: AgentClient,
) -> TrustGateResponse:
    """
    Real CROO SDK version of the pipeline.
    Queries ReputationOracle, runs quality checks, and attests to SourceAttest.
    """
    import asyncio
    t_start = time.time()
    _rep_order_id: str | None = None
    _sa_order_id: str | None = None

    stream = await requester_client.connect_websocket()
    try:
        async with asyncio.timeout(360):  # 6 min hard ceiling for full pipeline
            try:
                # ── Step 1: ReputationOracle (real SDK call) ──────────────────────────
                rep_event = asyncio.Event()
                rep_order_id = None
                rep_negotiation = None

                def on_rep_order_created(e):
                    nonlocal rep_order_id
                    if rep_negotiation and e.negotiation_id == rep_negotiation.negotiation_id:
                        rep_order_id = e.order_id
                        rep_event.set()

                stream.on(EventType.ORDER_CREATED, on_rep_order_created)

                rep_negotiation = await _retry(lambda: requester_client.negotiate_order(
                    NegotiateOrderRequest(
                        service_id=os.environ["REPUTATIONORACLE_SERVICE_ID"],
                        requirements=json.dumps({
                            "source_id": raw_input["source_id"],
                            "queried_by": TRUSTGATE_AGENT_ID,
                        })
                    )
                ))

                try:
                    await asyncio.wait_for(rep_event.wait(), timeout=PIPELINE_STEP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    raise TimeoutError("ReputationOracle did not accept negotiation within timeout")
                assert rep_order_id is not None
                _rep_order_id = rep_order_id

                rep_completed_event = asyncio.Event()

                def on_rep_order_completed(e):
                    if e.order_id == rep_order_id:
                        rep_completed_event.set()

                stream.on(EventType.ORDER_COMPLETED, on_rep_order_completed)

                await _retry(lambda: requester_client.pay_order(rep_order_id))
                try:
                    await asyncio.wait_for(rep_completed_event.wait(), timeout=PIPELINE_STEP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    await requester_client.reject_order(rep_order_id, "ReputationOracle delivery timeout")
                    raise TimeoutError("ReputationOracle did not deliver within timeout")

                rep_delivery = await _retry(lambda: requester_client.get_delivery(rep_order_id))
                rep_output_dict = json.loads(rep_delivery.deliverable_text)

                reputation_context = {
                    "trust_tier": rep_output_dict["trust_tier"],
                    "pass_rate": rep_output_dict["pass_rate"],
                    "total_checks": rep_output_dict["total_checks"],
                    "trend": rep_output_dict["trend"],
                    "avg_quality_score": rep_output_dict["avg_quality_score"],
                }

                # ── Step 2: TrustGate quality checks ──────────────────────────────────
                data_type = raw_input.get("data_type")
                payload = raw_input.get("payload")
                data_url = raw_input.get("data_url")
                if data_url:
                    import httpx
                    async with httpx.AsyncClient(timeout=30.0) as hc:
                        resp = await hc.get(data_url)
                        resp.raise_for_status()
                        fetched = resp.json()
                        if isinstance(fetched, dict):
                            data_type = fetched.get("data_type", data_type)
                            payload = fetched.get("payload", payload)
                        else:
                            payload = fetched

                if data_type is None or payload is None:
                    raise ValueError("Requirements must include data_type and payload, or a data_url pointing to JSON with those fields")

                tg_input = TrustGateInput(
                    source_id=raw_input["source_id"],
                    data_type=data_type,
                    payload=payload,
                    metadata=raw_input.get("metadata", {}),
                    reputation_context=reputation_context,
                )
                tg_output: TrustGateOutput = run_checks(tg_input)

                # ── Step 3: SourceAttest (real SDK call, AFTER Step 2 completes) ───────
                sa_event = asyncio.Event()
                sa_order_id = None
                sa_negotiation = None

                def on_sa_order_created(e):
                    nonlocal sa_order_id
                    if sa_negotiation and e.negotiation_id == sa_negotiation.negotiation_id:
                        sa_order_id = e.order_id
                        sa_event.set()

                stream.on(EventType.ORDER_CREATED, on_sa_order_created)

                sa_negotiation = await _retry(lambda: requester_client.negotiate_order(
                    NegotiateOrderRequest(
                        service_id=os.environ["SOURCEATTEST_SERVICE_ID"],
                        requirements=json.dumps({
                            "source_id": tg_output.source_id,
                            "quality_score": tg_output.quality_score,
                            "passed": tg_output.passed,
                            "flags": tg_output.flags,
                            "data_hash": tg_output.data_hash,
                            "checked_at": tg_output.checked_at,
                            "requester_agent_id": TRUSTGATE_AGENT_ID,
                        })
                    )
                ))

                try:
                    await asyncio.wait_for(sa_event.wait(), timeout=PIPELINE_STEP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    raise TimeoutError("SourceAttest did not accept negotiation within timeout")
                assert sa_order_id is not None
                _sa_order_id = sa_order_id

                sa_completed_event = asyncio.Event()

                def on_sa_order_completed(e):
                    if e.order_id == sa_order_id:
                        sa_completed_event.set()

                stream.on(EventType.ORDER_COMPLETED, on_sa_order_completed)

                await _retry(lambda: requester_client.pay_order(sa_order_id))
                try:
                    await asyncio.wait_for(sa_completed_event.wait(), timeout=PIPELINE_STEP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    await requester_client.reject_order(sa_order_id, "SourceAttest delivery timeout")
                    raise TimeoutError("SourceAttest did not deliver within timeout")

                sa_delivery = await _retry(lambda: requester_client.get_delivery(sa_order_id))
                sa_output_dict = json.loads(sa_delivery.deliverable_text)
            except Exception as exc:
                # Release any open orders from escrow before propagating
                # Only reject orders that are in paid state (order_id known, no delivery yet)
                if _sa_order_id is not None:
                    try:
                        await requester_client.reject_order(_sa_order_id, f"TrustGate pipeline error: {type(exc).__name__}")
                    except Exception:
                        pass  # best-effort only — don't mask original error
                elif _rep_order_id is not None:
                    # Only reject rep order if sa order was never opened
                    # (if sa order exists, rep order is already settled)
                    try:
                        await requester_client.reject_order(_rep_order_id, f"TrustGate pipeline error: {type(exc).__name__}")
                    except Exception:
                        pass
                raise  # always re-raise original exception
            finally:
                await stream.close()
    except asyncio.TimeoutError:
        raise TimeoutError("Full pipeline exceeded 6-minute hard ceiling")

    pipeline_ms = int((time.time() - t_start) * 1000)

    # ── Step 4: Unified response to buyer ─────────────────────────────────
    return TrustGateResponse(
        source_id=tg_output.source_id,
        quality_score=tg_output.quality_score,
        passed=tg_output.passed,
        flags=tg_output.flags,
        trust_tier=rep_output_dict["trust_tier"],
        pass_rate=rep_output_dict["pass_rate"],
        total_historical_checks=rep_output_dict["total_checks"],
        trend=rep_output_dict["trend"],
        data_hash=tg_output.data_hash,
        cert_tx_hash=sa_output_dict["tx_hash"],
        cert_id=sa_output_dict["cert_id"],
        checked_at=tg_output.checked_at,
        pipeline_ms=pipeline_ms,
    )


def serialize_response(response: TrustGateResponse) -> str:
    return json.dumps(asdict(response), indent=2)