"""
Veritas Fides — Judge Test Entry Point
=======================================
Single command to verify the full pipeline works end-to-end.

Usage:
    python run_judge_test.py

No environment variables required for simulation mode.
All three agents run locally without SDK or network access.

What this demonstrates:
    1. Clean data  → passes verification → certificate issued → source reputation starts building
    2. Corrupted data → fails verification → flags printed → certificate still issued (failed cert)
    3. Second clean run → reputation accumulates → trust tier assigned
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))

from orchestrator import run_pipeline, serialize_response


# ---------------------------------------------------------------------------
# Load mock payloads
# ---------------------------------------------------------------------------
PAYLOADS_DIR = os.path.join(os.path.dirname(__file__), 'mock_payloads')

def load_payload(filename: str) -> dict:
    path = os.path.join(PAYLOADS_DIR, filename)
    with open(path, 'r') as f:
        data = json.load(f)
    # JSON stores None as null — convert back to float nan for numpy
    data['payload'] = [
        float('nan') if v is None else v
        for v in data['payload']
    ]
    return data


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def divider(char='─', width=60):
    print(char * width)

def header(title: str):
    print()
    divider('═')
    print(f"  {title}")
    divider('═')

def print_result(label: str, r, elapsed_ms: int):
    status = "✓  PASSED" if r.passed else "✗  FAILED"
    tier_badge = f"[{r.trust_tier}]" if r.trust_tier != "Unrated" else "[Unrated]"

    print(f"\n  Result         : {status}")
    print(f"  Quality Score  : {r.quality_score}/100")
    print(f"  Trust Tier     : {r.trust_tier} {tier_badge}")
    print(f"  Historical     : {r.total_historical_checks} checks | "
          f"{r.pass_rate:.0%} pass rate | trend: {r.trend}")
    print(f"  Data Hash      : {r.data_hash[:20]}...")
    print(f"  Certificate ID : {r.cert_id[:20]}...")
    print(f"  Cert TX Hash   : {r.cert_tx_hash[:20]}...")
    print(f"  Pipeline Time  : {r.pipeline_ms}ms  (wall clock: {elapsed_ms}ms)")

    if r.flags:
        print(f"\n  Flags detected :")
        for flag in r.flags:
            print(f"    ✗  {flag}")
    else:
        print(f"\n  Flags          : none")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print()
    print("  ================================================")
    print("       VERITAS FIDES -- Judge Test Runner         ")
    print("    Proven Trust Infrastructure for Agent Economy ")
    print("  ================================================")
    print()
    print("  Agents  : TrustGate  ·  SourceAttest  ·  ReputationOracle")
    print("  Mode    : Simulation (no SDK / network required)")
    print("  Network : Base Mainnet (live mode after SDK credentials)")

    results = []
    all_passed = True

    # ── Test 1: Clean data ────────────────────────────────────────────────
    header("TEST 1 — Clean Sensor Data")
    print("  Source  : sensor_demo_clean")
    print("  Expect  : PASS — no anomalies, certificate issued")
    print()

    payload = load_payload('clean_data.json')
    t0 = time.time()
    r = run_pipeline(payload)
    elapsed = int((time.time() - t0) * 1000)

    print_result("clean", r, elapsed)
    ok = r.passed and r.quality_score >= 70
    results.append(("Clean data passes verification", ok))
    if not ok:
        all_passed = False
        print("\n  ✗ ASSERTION FAILED: clean data should pass with score ≥ 70")

    # ── Test 2: Corrupted data ────────────────────────────────────────────
    header("TEST 2 — Corrupted Sensor Data")
    print("  Source  : sensor_demo_corrupted")
    print("  Expect  : FAIL — missing values, outliers, range violations flagged")
    print()

    payload = load_payload('corrupted_data.json')
    t0 = time.time()
    r = run_pipeline(payload)
    elapsed = int((time.time() - t0) * 1000)

    print_result("corrupted", r, elapsed)
    ok = not r.passed and len(r.flags) >= 2
    results.append(("Corrupted data fails with ≥2 flags", ok))
    if not ok:
        all_passed = False
        print("\n  ✗ ASSERTION FAILED: corrupted data should fail with ≥2 flags")

    # ── Test 3: Reputation accumulation ──────────────────────────────────
    header("TEST 3 — Reputation Accumulation")
    print("  Source  : sensor_demo_clean (5 additional runs)")
    print("  Expect  : Trust tier升 Bronze → Silver after repeated clean passes")
    print()

    for i in range(5):
        payload = load_payload('clean_data.json')
        run_pipeline(payload)
        print(f"  Run {i+2}/6 complete...")

    payload = load_payload('clean_data.json')
    t0 = time.time()
    r = run_pipeline(payload)
    elapsed = int((time.time() - t0) * 1000)

    print_result("after_7_runs", r, elapsed)
    ok = r.trust_tier in ("Silver", "Gold") and r.total_historical_checks >= 6
    results.append(("Reputation accumulates to Silver/Gold tier", ok))
    if not ok:
        all_passed = False
        print("\n  ✗ ASSERTION FAILED: expected Silver or Gold after 7 clean runs")

    # ── Summary ───────────────────────────────────────────────────────────
    header("RESULTS SUMMARY")
    for label, passed in results:
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {label}")

    print()
    divider()
    passed_count = sum(1 for _, p in results if p)
    print(f"  {passed_count}/{len(results)} assertions passed")
    divider()

    if all_passed:
        print()
        print("  ✓  All tests passed.")
        print("  ✓  TrustGate verification logic confirmed.")
        print("  ✓  SourceAttest certificate pipeline confirmed.")
        print("  ✓  ReputationOracle accumulation confirmed.")
        print()
        print("  To run with live SDK credentials:")
        print("    1. Copy .env.example → .env")
        print("    2. Fill in API keys from CROO Dashboard")
        print("    3. Set live=True in orchestrator.py")
        print()
        sys.exit(0)
    else:
        print()
        print("  ✗  Some tests failed. See output above.")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()