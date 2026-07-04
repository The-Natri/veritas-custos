"""
TrustGate — End-to-End Tests
Covers: clean data, corrupted data, reputation accumulation across multiple runs.
No SDK dependency. Run with: python test_pipeline.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

# Clean up any persistent certificate store from previous runs
STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "certificate_store.json")
if os.path.exists(STORE_PATH):
    try:
        os.remove(STORE_PATH)
    except Exception:
        pass

import json
import numpy as np
from orchestrator import run_pipeline, serialize_response
from trustgate_checks import run_checks, TrustGateInput


# ---------------------------------------------------------------------------
# Test payloads
# ---------------------------------------------------------------------------
def _sinusoid(n=200, noise=0.05):
    t = np.linspace(0, 4 * np.pi, n)
    return (np.sin(t) + np.random.normal(0, noise, n)).tolist()


def _corrupted(n=200):
    t = np.linspace(0, 4 * np.pi, n)
    sig = np.sin(t)
    # inject NaNs (>5% → triggers missing_data)
    sig[10:25] = np.nan
    # inject outliers far outside range (triggers range_violation + outliers)
    sig[50] = 150.0
    sig[51] = -150.0
    sig[80] = 200.0
    sig[81] = -200.0
    sig[90] = 180.0
    sig[91] = -180.0
    sig[95] = 160.0
    sig[96] = -160.0
    sig[97] = 170.0
    sig[98] = -170.0
    # inject strong drift (second half shifts up by 10x signal amplitude)
    sig[100:] += 10.0
    # inject flatline (triggers flatline check)
    sig[150:170] = 0.5
    return sig.tolist()


CLEAN_PAYLOAD = {
    "source_id": "sensor_abc123",
    "data_type": "sensor",
    "payload": _sinusoid(),
    "metadata": {"expected_range": [-2.0, 2.0], "frequency_hz": 10.0},
}

CORRUPTED_PAYLOAD = {
    "source_id": "sensor_xyz999",
    "data_type": "sensor",
    "payload": _corrupted(),
    "metadata": {"expected_range": [-2.0, 2.0], "frequency_hz": 10.0},
}

PRICE_FEED_PAYLOAD = {
    "source_id": "price_feed_eth_usd",
    "data_type": "price_feed",
    "payload": (1800 + np.cumsum(np.random.normal(0, 5, 150))).tolist(),
    "metadata": {"expected_range": [1000.0, 5000.0], "frequency_hz": 1.0},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def print_result(label: str, response):
    print(f"\n[{label}]")
    print(f"  passed         : {response.passed}")
    print(f"  quality_score  : {response.quality_score}/100")
    print(f"  trust_tier     : {response.trust_tier}")
    print(f"  pass_rate      : {response.pass_rate:.1%} ({response.total_historical_checks} checks)")
    print(f"  trend          : {response.trend}")
    print(f"  cert_id        : {response.cert_id[:16]}...")
    print(f"  cert_tx_hash   : {response.cert_tx_hash[:18]}...")
    print(f"  pipeline_ms    : {response.pipeline_ms}ms")
    if response.flags:
        print(f"  flags:")
        for f in response.flags:
            print(f"    ✗ {f}")
    else:
        print(f"  flags          : none")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_clean_data():
    section("TEST 1 — Clean sinusoidal sensor data")
    r = run_pipeline(CLEAN_PAYLOAD)
    print_result("clean_data", r)
    assert r.passed, "Clean data should pass"
    assert r.quality_score >= 70, f"Expected score ≥ 70, got {r.quality_score}"
    assert r.cert_tx_hash.startswith("0x"), "Expected tx_hash to start with 0x"
    print("  ✓ PASS")


def test_corrupted_data():
    section("TEST 2 — Corrupted data (NaN + outliers + drift + flatline)")
    r = run_pipeline(CORRUPTED_PAYLOAD)
    print_result("corrupted_data", r)
    assert not r.passed, "Corrupted data should fail"
    assert r.quality_score < 70, f"Expected score < 70, got {r.quality_score}"
    assert len(r.flags) >= 2, f"Expected ≥2 flags, got {len(r.flags)}"
    print("  ✓ PASS")


def test_reputation_accumulation():
    section("TEST 3 — Reputation accumulation (same source, 8 runs)")
    source = "sensor_abc123"

    # Run 8 times against the same source_id — reputation should build
    for i in range(8):
        payload = {**CLEAN_PAYLOAD, "source_id": source, "payload": _sinusoid()}
        run_pipeline(payload)

    # 9th run — should now reflect history
    payload = {**CLEAN_PAYLOAD, "source_id": source, "payload": _sinusoid()}
    r = run_pipeline(payload)
    print_result(f"after_9_runs ({source})", r)

    assert r.total_historical_checks >= 8, \
        f"Expected ≥8 historical checks, got {r.total_historical_checks}"
    assert r.trust_tier in ("Gold", "Silver"), \
        f"Expected Silver or Gold after 9 clean runs, got {r.trust_tier}"
    print("  ✓ PASS")


def test_price_feed():
    section("TEST 4 — Price feed (random walk, different domain)")
    r = run_pipeline(PRICE_FEED_PAYLOAD)
    print_result("price_feed_eth_usd", r)
    assert r.cert_tx_hash.startswith("0x"), "Expected valid cert"
    print(f"  result: {'✓ PASSED' if r.passed else '✗ FAILED (score too low or flagged)'}")
    # Price feeds are inherently noisy — we don't assert pass/fail here
    # just confirm the pipeline completes without error
    print("  ✓ Pipeline completed cleanly")


def test_unrated_new_source():
    section("TEST 5 — Brand new source (should be Unrated)")
    payload = {
        "source_id": "brand_new_source_never_seen",
        "data_type": "time_series",
        "payload": _sinusoid(),
        "metadata": {"expected_range": [-2.0, 2.0], "frequency_hz": 1.0},
    }
    r = run_pipeline(payload)
    print_result("new_source", r)
    # First call: ReputationOracle returns Unrated, then this run gets attested
    # So after the call, reputation_context during check was Unrated
    assert r.trust_tier == "Unrated" or r.total_historical_checks == 0, \
        "Brand new source should start Unrated"
    print("  ✓ PASS")


def test_numeric_tabular_payload():
    section("TEST 6 — Numeric tabular payload")
    payload = [
        {"a": 1.0, "b": 2.0},
        {"a": 1.1, "b": 2.1},
        {"a": 1.2, "b": 2.2},
        {"a": 1.3, "b": 2.3},
    ]
    tg_input = TrustGateInput(
        source_id="test_numeric_tab",
        data_type="sensor",
        payload=payload,
        metadata={"expected_range": [0.0, 10.0]}
    )
    res = run_checks(tg_input)
    assert res.check_details["detected_type"] == "numeric_tabular"
    for check in ["missing", "outliers", "drift", "flatline", "range", "schema_consistency", "duplicates", "null_pattern"]:
        assert check in res.check_details, f"Expected check '{check}' to run for numeric_tabular"
    print("  ✓ PASS")


def test_categorical_tabular_payload():
    section("TEST 7 — Categorical tabular payload")
    payload = [
        {"a": "hello", "b": "world"},
        {"a": "good", "b": "morning"},
    ]
    tg_input = TrustGateInput(
        source_id="test_cat_tab",
        data_type="sensor",
        payload=payload,
        metadata={}
    )
    res = run_checks(tg_input)
    assert res.check_details["detected_type"] == "categorical_tabular"
    for check in ["missing", "schema_consistency", "duplicates", "null_pattern", "cardinality"]:
        assert check in res.check_details, f"Expected check '{check}' to run for categorical_tabular"
    print("  ✓ PASS")


def test_text_payload():
    section("TEST 8 — Text payload (list of strings)")
    payload = [
        "This is a valid long sentence.",
        "Another long valid sentence.",
    ]
    tg_input = TrustGateInput(
        source_id="test_text",
        data_type="sensor",
        payload=payload,
        metadata={}
    )
    res = run_checks(tg_input)
    assert res.check_details["detected_type"] == "text"
    for check in ["missing", "duplicates", "encoding_validity"]:
        assert check in res.check_details, f"Expected check '{check}' to run for text"
    print("  ✓ PASS")


def test_nested_json_payload():
    section("TEST 9 — Nested JSON payload (dict)")
    payload = {
        "status": "active",
        "value": 42.0,
        "info": {"nested": "value"}
    }
    tg_input = TrustGateInput(
        source_id="test_nested",
        data_type="sensor",
        payload=payload,
        metadata={}
    )
    res = run_checks(tg_input)
    assert res.check_details["detected_type"] == "nested_json"
    for check in ["missing", "schema_consistency", "duplicates", "null_pattern", "cardinality"]:
        assert check in res.check_details, f"Expected check '{check}' to run for nested_json"
    print("  ✓ PASS")


def test_duplicate_heavy_numeric_1d():
    section("TEST 10 — Duplicate-heavy numeric_1d payload")
    payload = [1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    tg_input = TrustGateInput(
        source_id="test_dup_1d",
        data_type="sensor",
        payload=payload,
        metadata={}
    )
    res = run_checks(tg_input)
    assert res.check_details["detected_type"] == "numeric_1d"
    assert "duplicates" in res.check_details, "Expected duplicates check to run"
    assert any("duplicates" in f for f in res.flags), "Expected duplicates check to flag"
    print("  ✓ PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\nTrustGate + SourceAttest + ReputationOracle — Full Pipeline Tests")
    print("Simulation mode (live=False) — no SDK required\n")

    tests = [
        test_clean_data,
        test_corrupted_data,
        test_reputation_accumulation,
        test_price_feed,
        test_unrated_new_source,
        test_numeric_tabular_payload,
        test_categorical_tabular_payload,
        test_text_payload,
        test_nested_json_payload,
        test_duplicate_heavy_numeric_1d,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            failed += 1

    section(f"RESULTS: {passed}/{len(tests)} tests passed")