"""
TrustGate (Veritas Custos) - Full verification pipeline orchestrator.
Pipeline per agent address:
  1. Identity Checker  -> deterministic trust_score (0-100) + tier
  2. ReputationOracle  -> existing on-chain record, if any
  3. Decision Agent    -> Gemini reviews score, produces written verdict + final tier
  4. SourceAttest      -> writes final tier/score on-chain, returns real tx_hash
  5. Verdict saved     -> decisions/{tx_hash}.json (permanently linked to on-chain tx)
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "identity_checker"))

from reputation_oracle import get_on_chain_reputation
from source_attest import write_on_chain
from decision_agent import make_verdict, save_verdict
from identity_checker import check_identity


def run_pipeline(account_hash: str) -> dict:
    t_start = time.time()

    # Step 1: Identity check
    identity_result = check_identity(account_hash)

    # Step 2: On-chain reputation lookup
    agent_address = f"account-hash-{account_hash}" if not account_hash.startswith("account-hash-") else account_hash
    reputation_before = get_on_chain_reputation(agent_address)

    # Step 3: Decision Agent - LLM verdict
    verdict = make_verdict(identity_result, reputation_before)
    final_tier = verdict.get("final_tier", identity_result["tier"])
    final_score = identity_result["trust_score"]

    # Step 4: Write final verdict on-chain
    attest_result = write_on_chain(
        agent_address=agent_address,
        score=final_score,
        tier=final_tier,
        already_registered=reputation_before["registered"],
    )

    # Step 5: Save reasoning to decisions/{tx_hash}.json
    tx_hash = attest_result.get("tx_hash")
    verdict_path = None
    if tx_hash:
        verdict_path = save_verdict(tx_hash, account_hash, identity_result, verdict)

    pipeline_ms = int((time.time() - t_start) * 1000)

    return {
        "account_hash": account_hash,
        "identity_check": identity_result,
        "on_chain_before": reputation_before,
        "decision_agent": verdict,
        "attestation": attest_result,
        "verdict_saved_to": verdict_path,
        "pipeline_ms": pipeline_ms,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 trustgate.py <account_hash>")
        sys.exit(1)

    result = run_pipeline(sys.argv[1])
    print(json.dumps(result, indent=2))
