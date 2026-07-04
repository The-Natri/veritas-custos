"""
SourceAttest — On-chain Certificate Builder
Constructs the certificate object from TrustGate's verified result.
Simulates on-chain write locally until SDK lands.

When SDK lands:
- Replace _simulate_onchain_write() with actual deliver_order() call
- Swap sha256 → web3.keccak() for cert_hash
"""

import hashlib
from eth_hash.auto import keccak
import json
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Input schema — what TrustGate sends to SourceAttest
# ---------------------------------------------------------------------------
@dataclass
class SourceAttestInput:
    source_id: str
    quality_score: int
    passed: bool
    flags: list[str]
    data_hash: str              # keccak256 of original payload (from TrustGate)
    checked_at: str             # ISO timestamp from TrustGate
    requester_agent_id: str     # TrustGate's agent DID


# ---------------------------------------------------------------------------
# Output schema — what SourceAttest delivers back to TrustGate
# ---------------------------------------------------------------------------
@dataclass
class SourceAttestOutput:
    cert_id: str                # unique certificate identifier
    source_id: str
    quality_score: int
    passed: bool
    data_hash: str
    cert_hash: str              # keccak256 of the full certificate object
    checked_at: str
    attested_at: str            # timestamp of on-chain write
    tx_hash: str                # blockchain transaction ID (simulated until SDK)
    block_number: Optional[int] # block number of on-chain write
    issuer_agent_id: str        # SourceAttest's agent DID


# ---------------------------------------------------------------------------
# Certificate construction
# ---------------------------------------------------------------------------
def build_certificate(inp: SourceAttestInput, issuer_agent_id: str) -> dict:
    """
    Constructs the canonical certificate object.
    This is what gets written on-chain (via deliver_order payload).
    """
    cert_id = str(uuid.uuid4())
    attested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    cert = {
        "cert_id": cert_id,
        "source_id": inp.source_id,
        "quality_score": inp.quality_score,
        "passed": inp.passed,
        "flags_count": len(inp.flags),
        "data_hash": inp.data_hash,
        "checked_at": inp.checked_at,
        "attested_at": attested_at,
        "requester_agent_id": inp.requester_agent_id,
        "issuer_agent_id": issuer_agent_id,
    }

    # Hash the full cert object for tamper detection
    cert_raw = json.dumps(cert, sort_keys=True, separators=(",", ":")).encode()
    cert["cert_hash"] = "0x" + keccak(cert_raw).hex()

    return cert


# ---------------------------------------------------------------------------
# On-chain write simulation (replaced by deliver_order() when SDK lands)
# ---------------------------------------------------------------------------
def _simulate_onchain_write(cert: dict) -> tuple[str, int]:
    """
    Simulates tx_hash and block_number for local testing.
    SDK replacement: call deliver_order(result=json.dumps(cert))
    and extract tx_hash from the delivery receipt.
    """
    sim_input = (cert["cert_hash"] + str(time.time())).encode()
    tx_hash = "0x" + hashlib.sha256(sim_input).hexdigest()
    block_number = int(time.time()) % 1_000_000  # fake block number
    return tx_hash, block_number


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def run_attest(
    inp: SourceAttestInput,
    issuer_agent_id: str = "did:croo:sourceattest:placeholder",
    live: bool = False,       # set True when SDK is active
) -> SourceAttestOutput:
    """
    live=False  → simulates on-chain write (local dev / judge dry-run)
    live=True   → calls actual deliver_order() via SDK (fill in when SDK lands)
    """
    cert = build_certificate(inp, issuer_agent_id)

    if live:
        # deliver_order is handled by the agent wrapper (sourceattest_agent.py)
        tx_hash = "0x" + keccak((cert["cert_hash"] + "live").encode()).hex()
        block_number = 999999  # placeholder live block number
    else:
        tx_hash, block_number = _simulate_onchain_write(cert)

    return SourceAttestOutput(
        cert_id=cert["cert_id"],
        source_id=cert["source_id"],
        quality_score=cert["quality_score"],
        passed=cert["passed"],
        data_hash=cert["data_hash"],
        cert_hash=cert["cert_hash"],
        checked_at=cert["checked_at"],
        attested_at=cert["attested_at"],
        tx_hash=tx_hash,
        block_number=block_number,
        issuer_agent_id=cert["issuer_agent_id"],
    )


# ---------------------------------------------------------------------------
# Serializer — for deliver_order() payload and on-chain storage
# ---------------------------------------------------------------------------
def serialize_output(output: SourceAttestOutput) -> str:
    return json.dumps(asdict(output), indent=2)