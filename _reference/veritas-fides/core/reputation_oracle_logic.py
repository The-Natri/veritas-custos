"""
ReputationOracle — Historical Trust Layer
Reads accumulated SourceAttest certificates for a source_id.
Returns pass rate, trust tier, and contextual summary for TrustGate.

Called by TrustGate BEFORE running quality checks — so historical context
enriches the final response delivered to the buyer.

When SDK lands:
- Replace CertificateStore (in-memory) with on-chain read via get_delivery()
  across historical SourceAttest order receipts indexed by source_id
"""

import os
import sys
import json
import time
import threading
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Trust Tier thresholds
# ---------------------------------------------------------------------------
STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "certificate_store.json")


TIERS = {
    "Gold":   {"min_checks": 10, "min_pass_rate": 0.90},
    "Silver": {"min_checks": 5,  "min_pass_rate": 0.75},
    "Bronze": {"min_checks": 1,  "min_pass_rate": 0.0},
    "Unrated": {"min_checks": 0, "min_pass_rate": 0.0},
}


def assign_tier(total_checks: int, pass_rate: float) -> str:
    if total_checks >= TIERS["Gold"]["min_checks"] and pass_rate >= TIERS["Gold"]["min_pass_rate"]:
        return "Gold"
    if total_checks >= TIERS["Silver"]["min_checks"] and pass_rate >= TIERS["Silver"]["min_pass_rate"]:
        return "Silver"
    if total_checks >= TIERS["Bronze"]["min_checks"]:
        return "Bronze"
    return "Unrated"


# ---------------------------------------------------------------------------
# In-memory certificate store (SDK replacement target)
# ---------------------------------------------------------------------------
class CertificateStore:
    """
    Local store for development and judge dry-run.
    SDK replacement: query on-chain SourceAttest delivery receipts
    indexed by source_id using get_delivery() calls.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(STORE_PATH):
            self._store = {}
            return
        try:
            parent_dir = os.path.dirname(STORE_PATH)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                self._store = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load certificate store from {STORE_PATH}: {e}", file=sys.stderr)
            self._store = {}

    def _save(self):
        try:
            parent_dir = os.path.dirname(STORE_PATH)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            tmp_path = STORE_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._store, f, indent=2)
            os.replace(tmp_path, STORE_PATH)
        except Exception as e:
            print(f"Error: Failed to save certificate store to {STORE_PATH}: {e}", file=sys.stderr)

    def add_certificate(self, cert: dict):
        """Called by SourceAttest after each successful attest."""
        with self._lock:
            source_id = cert["source_id"]
            if source_id not in self._store:
                self._store[source_id] = []
            self._store[source_id].append(cert)
            self._save()

    def get_history(self, source_id: str) -> list[dict]:
        with self._lock:
            return list(self._store.get(source_id, []))

    def all_sources(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


# Singleton store shared across the process
# In live mode, this gets replaced by on-chain reads
_store = CertificateStore()


def get_store() -> CertificateStore:
    return _store


# ---------------------------------------------------------------------------
# Input / Output schemas
# ---------------------------------------------------------------------------
@dataclass
class ReputationOracleInput:
    source_id: str
    queried_by: str         # TrustGate's agent DID


@dataclass
class ReputationOracleOutput:
    source_id: str
    total_checks: int
    pass_count: int
    fail_count: int
    pass_rate: float
    avg_quality_score: float
    trust_tier: str         # "Gold" | "Silver" | "Bronze" | "Unrated"
    last_checked: Optional[str]
    first_checked: Optional[str]
    trend: str              # "improving" | "degrading" | "stable" | "insufficient_data"
    queried_at: str
    queried_by: str


# ---------------------------------------------------------------------------
# Trend Analysis — last 5 checks vs prior history
# ---------------------------------------------------------------------------
def compute_trend(history: list[dict]) -> str:
    if len(history) < 6:
        return "insufficient_data"

    scores = [h["quality_score"] for h in history]
    recent = scores[-5:]
    prior = scores[:-5]

    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    delta = recent_avg - prior_avg

    if delta > 5:
        return "improving"
    if delta < -5:
        return "degrading"
    return "stable"


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def run_reputation_query(
    inp: ReputationOracleInput,
    store: Optional[CertificateStore] = None,
) -> ReputationOracleOutput:
    """
    Queries certificate history for source_id and returns trust summary.
    TrustGate calls this before running its own checks.
    """
    if store is None:
        store = get_store()

    history = store.get_history(inp.source_id)
    queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not history:
        return ReputationOracleOutput(
            source_id=inp.source_id,
            total_checks=0,
            pass_count=0,
            fail_count=0,
            pass_rate=0.0,
            avg_quality_score=0.0,
            trust_tier="Unrated",
            last_checked=None,
            first_checked=None,
            trend="insufficient_data",
            queried_at=queried_at,
            queried_by=inp.queried_by,
        )

    total = len(history)
    passed = sum(1 for h in history if h["passed"])
    failed = total - passed
    pass_rate = round(passed / total, 4)
    avg_score = round(sum(h["quality_score"] for h in history) / total, 2)
    tier = assign_tier(total, pass_rate)
    trend = compute_trend(history)

    # Sort by attested_at to get first/last timestamps
    sorted_history = sorted(history, key=lambda h: h.get("attested_at", ""))
    first_checked = sorted_history[0].get("attested_at")
    last_checked = sorted_history[-1].get("attested_at")

    return ReputationOracleOutput(
        source_id=inp.source_id,
        total_checks=total,
        pass_count=passed,
        fail_count=failed,
        pass_rate=pass_rate,
        avg_quality_score=avg_score,
        trust_tier=tier,
        last_checked=last_checked,
        first_checked=first_checked,
        trend=trend,
        queried_at=queried_at,
        queried_by=inp.queried_by,
    )


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------
def serialize_output(output: ReputationOracleOutput) -> str:
    return json.dumps(asdict(output), indent=2)