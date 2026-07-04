"""
TrustGate — Data Quality Check Engine
All anomaly checks are self-contained statistical methods.
Upgrade: Universal data verification engine using Keccak256.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Any

import numpy as np
from scipy import stats
from eth_hash.auto import keccak


# ---------------------------------------------------------------------------
# Config — deduction weights per flag (total starts at 100)
# ---------------------------------------------------------------------------
DEDUCTIONS = {
    "missing_data": 20,
    "outliers": 15,
    "drift": 20,
    "flatline": 25,
    "range_violation": 15,
    "temporal_inconsistency": 15,
    "duplicates": 15,
    "schema_consistency": 25,
    "cardinality": 10,
    "null_pattern": 15,
    "encoding_validity": 10,
}

PASS_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Input / Output schemas (mirrors CAP payload contract)
# ---------------------------------------------------------------------------
@dataclass
class TrustGateInput:
    source_id: str
    data_type: str                          # "price_feed" | "sensor" | "time_series"
    payload: Any                            # list[float] | list[dict] | list[str] | dict | str
    metadata: dict                          # expected_range, frequency_hz
    reputation_context: Optional[dict] = None  # injected by ReputationOracle pre-check


@dataclass
class TrustGateOutput:
    source_id: str
    quality_score: int
    passed: bool
    flags: list[str]
    check_details: dict
    checked_at: str
    data_hash: str
    reputation_summary: Optional[dict] = None  # forwarded from ReputationOracle


# ---------------------------------------------------------------------------
# Step 3 — Type Detector function
# ---------------------------------------------------------------------------
def detect_data_type(payload: Any) -> str:
    """
    Detects the schema type of the input payload.
    """
    if isinstance(payload, list):
        if not payload:
            return "unknown"
            
        # check if all elements are int or float
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in payload):
            return "numeric_1d"
            
        # check if all elements are dict
        if all(isinstance(x, dict) for x in payload):
            first = payload[0]
            if not first:
                return "unknown"
                
            all_numeric = True
            all_string = True
            
            for record in payload:
                for v in record.values():
                    if isinstance(v, bool):
                        all_numeric = False
                        all_string = False
                    elif isinstance(v, (int, float)):
                        all_string = False
                    elif isinstance(v, str):
                        all_numeric = False
                    else:
                        all_numeric = False
                        all_string = False
                        
            if all_numeric:
                return "numeric_tabular"
            if all_string:
                return "categorical_tabular"
            return "mixed_tabular"
            
        # check if all elements are str
        if all(isinstance(x, str) for x in payload):
            return "text"
            
        return "mixed_raw"
        
    elif isinstance(payload, dict):
        return "nested_json"
    elif isinstance(payload, str):
        return "raw_text"
        
    return "unknown"


# ---------------------------------------------------------------------------
# Step 4 — Input coercion function
# ---------------------------------------------------------------------------
def coerce_payload(payload: Any, detected_type: str) -> tuple[Any, list[str]]:
    """
    Coerces input payload elements where applicable and returns warnings.
    """
    warnings = []
    if detected_type == "numeric_1d":
        coerced = []
        for idx, val in enumerate(payload):
            try:
                coerced.append(float(val))
            except (ValueError, TypeError):
                warnings.append(f"Index {idx}: value '{val}' could not be coerced to float")
        if not coerced:
            raise ValueError("Payload contains no valid numeric values")
        return coerced, warnings
        
    elif detected_type == "numeric_tabular":
        coerced = []
        for idx, record in enumerate(payload):
            new_record = {}
            for k, v in record.items():
                try:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        new_record[k] = float(v)
                    elif isinstance(v, str):
                        try:
                            new_record[k] = float(v)
                        except ValueError:
                            new_record[k] = v
                    else:
                        new_record[k] = v
                except (ValueError, TypeError):
                    new_record[k] = v
            coerced.append(new_record)
        return coerced, warnings
        
    return payload, warnings


# ---------------------------------------------------------------------------
# Step 5 — Check Routing Map
# ---------------------------------------------------------------------------
CHECKS_BY_TYPE = {
    "numeric_1d": ["missing", "outliers", "drift", "flatline", "range", "temporal", "duplicates"],
    "numeric_tabular": ["missing", "outliers", "drift", "flatline", "range", "schema_consistency", "duplicates", "null_pattern"],
    "mixed_tabular": ["missing", "schema_consistency", "duplicates", "null_pattern", "cardinality"],
    "categorical_tabular": ["missing", "schema_consistency", "duplicates", "null_pattern", "cardinality"],
    "text": ["missing", "duplicates", "encoding_validity"],
    "nested_json": ["missing", "schema_consistency", "duplicates", "null_pattern", "cardinality"],
    "raw_text": ["encoding_validity", "duplicates"],
    "mixed_raw": ["missing", "duplicates", "null_pattern"],
    "unknown": ["missing", "duplicates"],
}


# ---------------------------------------------------------------------------
# Check 1 — Missing Data (updated to handle all types)
# ---------------------------------------------------------------------------
def check_missing(payload: Any, detected_type: str) -> tuple[bool, str, dict]:
    """Flag missing data according to payload type."""
    if detected_type == "numeric_1d":
        arr = np.array(payload, dtype=float)
        total = len(arr)
        bad = int(np.sum(~np.isfinite(arr)))
        ratio = bad / total if total > 0 else 0.0
        flagged = ratio > 0.05
        detail = {"missing_count": bad, "missing_ratio": round(ratio, 4)}
        msg = f"missing_data: {bad}/{total} ({ratio:.1%}) non-finite values" if flagged else ""
        return flagged, msg, detail
        
    elif detected_type in ("numeric_tabular", "mixed_tabular", "categorical_tabular") or (isinstance(payload, list) and all(isinstance(x, dict) for x in payload)):
        total = len(payload)
        bad = 0
        for record in payload:
            if any(v is None or v == "" for v in record.values()):
                bad += 1
        ratio = bad / total if total > 0 else 0.0
        flagged = ratio > 0.05
        detail = {"missing_count": bad, "missing_ratio": round(ratio, 4)}
        msg = f"missing_data: {bad}/{total} records with missing values ({ratio:.1%})" if flagged else ""
        return flagged, msg, detail
        
    elif detected_type == "text" or (isinstance(payload, list) and all(isinstance(x, str) for x in payload)):
        total = len(payload)
        bad = sum(1 for s in payload if s is None or s == "")
        ratio = bad / total if total > 0 else 0.0
        flagged = ratio > 0.05
        detail = {"missing_count": bad, "missing_ratio": round(ratio, 4)}
        msg = f"missing_data: {bad}/{total} empty or null strings ({ratio:.1%})" if flagged else ""
        return flagged, msg, detail
        
    elif detected_type == "nested_json" or isinstance(payload, dict):
        total = len(payload)
        bad = sum(1 for v in payload.values() if v is None)
        ratio = bad / total if total > 0 else 0.0
        flagged = ratio > 0.05
        detail = {"missing_count": bad, "missing_ratio": round(ratio, 4)}
        msg = f"missing_data: {bad}/{total} null values ({ratio:.1%})" if flagged else ""
        return flagged, msg, detail
        
    elif detected_type == "raw_text" or isinstance(payload, str):
        flagged = len(payload.strip()) == 0
        detail = {"empty": flagged}
        msg = "missing_data: raw text is empty" if flagged else ""
        return flagged, msg, detail
        
    else:
        if isinstance(payload, list):
            total = len(payload)
            bad = sum(1 for x in payload if x is None or x == "")
            ratio = bad / total if total > 0 else 0.0
            flagged = ratio > 0.05
            detail = {"missing_count": bad, "missing_ratio": round(ratio, 4)}
            msg = f"missing_data: {bad}/{total} missing values ({ratio:.1%})" if flagged else ""
            return flagged, msg, detail
        return False, "", {"missing_count": 0, "missing_ratio": 0.0}


# ---------------------------------------------------------------------------
# Check 2 — Outlier Detection (Z-score + IQR, both must agree to flag)
# ---------------------------------------------------------------------------
def check_outliers(arr: np.ndarray) -> tuple[bool, str, dict]:
    """
    Dual-method: flag only when both Z-score (>±3σ) AND IQR (1.5×) agree.
    Reduces false positives on legitimately skewed distributions.
    """
    finite = arr[np.isfinite(arr)]
    if len(finite) < 4:
        return False, "", {"outlier_count": 0, "method": "skipped_insufficient_data"}

    z_scores = np.abs(stats.zscore(finite))
    z_outliers = set(np.where(z_scores > 3)[0])

    q1, q3 = np.percentile(finite, [25, 75])
    iqr = q3 - q1
    iqr_outliers = set(np.where(
        (finite < q1 - 1.5 * iqr) | (finite > q3 + 1.5 * iqr)
    )[0])

    consensus = z_outliers & iqr_outliers
    count = len(consensus)
    ratio = count / len(finite)
    flagged = ratio > 0.05

    detail = {
        "outlier_count": count,
        "outlier_ratio": round(ratio, 4),
        "z_score_outliers": len(z_outliers),
        "iqr_outliers": len(iqr_outliers),
    }
    msg = f"outliers: {count} consensus outliers ({ratio:.1%} of data)" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Check 3 — Drift Detection (Mann-Whitney U on first vs second half)
# ---------------------------------------------------------------------------
def check_drift(arr: np.ndarray) -> tuple[bool, str, dict]:
    """
    Split series into two halves and test for distributional shift.
    Mann-Whitney U is non-parametric — works on non-normal distributions.
    """
    finite = arr[np.isfinite(arr)]
    if len(finite) < 10:
        return False, "", {"drift_detected": False, "reason": "insufficient_data"}

    mid = len(finite) // 2
    first_half, second_half = finite[:mid], finite[mid:]

    mean_shift = abs(np.mean(second_half) - np.mean(first_half))
    relative_shift = mean_shift / (np.std(finite) + 1e-9)

    _, p_value = stats.mannwhitneyu(first_half, second_half, alternative="two-sided")
    flagged = p_value < 0.05 and relative_shift > 0.5

    detail = {
        "p_value": round(float(p_value), 6),
        "mean_shift": round(float(mean_shift), 4),
        "relative_shift": round(float(relative_shift), 4),
    }
    msg = f"drift: distributional shift detected (p={p_value:.4f}, shift={relative_shift:.2f}σ)" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Check 4 — Flatline / Noise Floor Detection
# ---------------------------------------------------------------------------
def check_flatline(arr: np.ndarray) -> tuple[bool, str, dict]:
    """
    Flag near-zero variance — indicates dead sensor, fabricated constant data,
    or signal dropout masquerading as real readings.
    """
    finite = arr[np.isfinite(arr)]
    if len(finite) < 2:
        return False, "", {"variance": None}

    variance = float(np.var(finite))
    diffs = np.diff(finite)
    zero_diff_ratio = float(np.sum(np.abs(diffs) < 1e-9) / len(diffs)) if len(diffs) > 0 else 0.0

    flagged = variance < 1e-6 or zero_diff_ratio > 0.9

    detail = {
        "variance": round(variance, 10),
        "zero_diff_ratio": round(zero_diff_ratio, 4),
    }
    msg = f"flatline: near-zero variance ({variance:.2e}) or {zero_diff_ratio:.1%} identical consecutive values" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Check 5 — Range Validation (uses buyer-supplied metadata)
# ---------------------------------------------------------------------------
def check_range(arr: np.ndarray, metadata: dict) -> tuple[bool, str, dict]:
    """
    Flag values outside buyer-declared expected_range.
    If no range supplied, skip gracefully.
    """
    expected = metadata.get("expected_range")
    if not expected or len(expected) != 2:
        return False, "", {"range_check": "skipped_no_metadata"}

    lo, hi = expected
    finite = arr[np.isfinite(arr)]
    violations = int(np.sum((finite < lo) | (finite > hi)))
    ratio = violations / len(finite) if len(finite) > 0 else 0.0
    flagged = ratio > 0.02

    detail = {
        "expected_range": [lo, hi],
        "violation_count": violations,
        "violation_ratio": round(ratio, 4),
    }
    msg = f"range_violation: {violations} values outside [{lo}, {hi}] ({ratio:.1%})" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Check 6 — Temporal Consistency
# ---------------------------------------------------------------------------
def check_temporal(arr: np.ndarray, metadata: dict) -> tuple[bool, str, dict]:
    """
    Three sub-checks: autocorrelation, rate-of-change, and entropy cv proxy.
    """
    finite = arr[np.isfinite(arr)]
    if len(finite) < 10:
        return False, "", {"temporal_check": "skipped_insufficient_data"}

    issues = []

    # Sub-check 1: lag-1 autocorrelation
    autocorr = float(np.corrcoef(finite[:-1], finite[1:])[0, 1])
    low_autocorr = abs(autocorr) < 0.1
    if low_autocorr:
        issues.append(f"low_autocorrelation({autocorr:.3f})")

    # Sub-check 2: rate-of-change bounds
    diffs = np.abs(np.diff(finite))
    expected_range = metadata.get("expected_range")
    if expected_range and len(expected_range) == 2:
        signal_range = expected_range[1] - expected_range[0]
        max_allowable_step = signal_range * 0.5
        impossible_jumps = int(np.sum(diffs > max_allowable_step))
        if impossible_jumps > 0:
            issues.append(f"impossible_jumps({impossible_jumps})")
    else:
        step_mean, step_std = np.mean(diffs), np.std(diffs)
        impossible_jumps = int(np.sum(diffs > step_mean + 5 * step_std))
        if impossible_jumps > 0:
            issues.append(f"impossible_jumps({impossible_jumps})")

    # Sub-check 3: cv of diffs
    if len(diffs) > 0:
        cv = float(np.std(diffs) / (np.mean(diffs) + 1e-9))
        if cv > 10.0:
            issues.append(f"high_entropy(cv={cv:.2f})")

    flagged = len(issues) >= 2
    detail = {
        "autocorrelation_lag1": round(autocorr, 4),
        "sub_issues": issues,
    }
    msg = f"temporal_inconsistency: {', '.join(issues)}" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Step 6 — New Checks
# ---------------------------------------------------------------------------
def check_duplicates(payload: Any, detected_type: str) -> tuple[bool, str, dict]:
    """Convert each record to a hashable string, flag if duplicate ratio > 10%."""
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = list(payload.items())
    elif isinstance(payload, str):
        records = payload.splitlines()
    else:
        records = [payload]

    total = len(records)
    if total == 0:
        return False, "", {"duplicate_count": 0, "duplicate_ratio": 0.0}

    def make_hashable(r):
        if isinstance(r, (dict, list, set)):
            return json.dumps(r, sort_keys=True)
        return str(r)

    string_records = [make_hashable(r) for r in records]
    from collections import Counter
    counts = Counter(string_records)
    
    unique_count = len(counts)
    duplicate_count = total - unique_count
    ratio = duplicate_count / total
    
    # numeric_1d legitimately repeats values — use higher threshold
    threshold = 0.30 if detected_type == "numeric_1d" else 0.10
    flagged = ratio > threshold
    detail = {
        "total_records": total,
        "duplicate_count": duplicate_count,
        "duplicate_ratio": round(ratio, 4)
    }
    msg = f"duplicates: {duplicate_count}/{total} duplicate records ({ratio:.1%})" if flagged else ""
    return flagged, msg, detail


def check_schema_consistency(payload: list[dict]) -> tuple[bool, str, dict]:
    """Flag if any record's schema keyset differs from reference record (0 tolerance)."""
    if not payload or not all(isinstance(x, dict) for x in payload):
        return False, "", {"schema_consistency": "skipped_invalid_input"}
        
    reference_keys = set(payload[0].keys())
    inconsistent_count = 0
    example_diff = {}
    
    for idx, record in enumerate(payload):
        record_keys = set(record.keys())
        if record_keys != reference_keys:
            inconsistent_count += 1
            if not example_diff:
                missing_in_record = list(reference_keys - record_keys)
                extra_in_record = list(record_keys - reference_keys)
                example_diff = {
                    "record_index": idx,
                    "missing_keys": missing_in_record,
                    "extra_keys": extra_in_record
                }
                
    flagged = inconsistent_count > 0
    detail = {
        "reference_keys": sorted(list(reference_keys)),
        "inconsistent_record_count": inconsistent_count,
        "example_diff": example_diff
    }
    msg = f"schema_consistency: {inconsistent_count} records differ from reference schema" if flagged else ""
    return flagged, msg, detail


def check_cardinality(payload: list[dict] | list[str]) -> tuple[bool, str, dict]:
    """Flag columns with unique/total ratio > 0.95 and count > 50 (ID leak detection)."""
    flagged_columns = []
    cardinality_ratios = {}
    flagged = False
    
    if not payload:
        return False, "", {"flagged_columns": [], "cardinality_ratios": {}}
        
    total = len(payload)
    
    if all(isinstance(x, dict) for x in payload):
        all_keys = set()
        for r in payload:
            all_keys.update(r.keys())
            
        for col in all_keys:
            col_vals = [str(r[col]) for r in payload if col in r and isinstance(r[col], str)]
            if not col_vals:
                continue
            unique_vals = set(col_vals)
            ratio = len(unique_vals) / len(col_vals)
            cardinality_ratios[col] = round(ratio, 4)
            if ratio > 0.95 and len(col_vals) > 50:
                flagged_columns.append(col)
                
        flagged = len(flagged_columns) > 0
        msg = f"cardinality: high cardinality columns detected: {', '.join(flagged_columns)}" if flagged else ""
        
    elif all(isinstance(x, str) for x in payload):
        unique_vals = set(payload)
        ratio = len(unique_vals) / total
        cardinality_ratios["payload"] = round(ratio, 4)
        if len(unique_vals) == total and total > 50:
            flagged = True
            flagged_columns.append("payload")
            msg = f"cardinality: all {total} values are unique (possible ID leak)"
        else:
            msg = ""
    else:
        msg = ""
        
    detail = {
        "flagged_columns": flagged_columns,
        "cardinality_ratios": cardinality_ratios
    }
    return flagged, msg, detail


def check_null_pattern(payload: list[dict]) -> tuple[bool, str, dict]:
    """Flag column with null ratio > 20% or clustering in contiguous blocks (dropout)."""
    if not payload or not all(isinstance(x, dict) for x in payload):
        return False, "", {"null_ratios": {}, "systematic_columns": []}
        
    all_keys = set()
    for r in payload:
        all_keys.update(r.keys())
        
    total = len(payload)
    null_ratios = {}
    systematic_columns = []
    flagged_high_null = []
    
    def is_null_val(v):
        return v is None or v == "" or (isinstance(v, (list, dict)) and len(v) == 0)

    for col in all_keys:
        col_vals = [r.get(col) for r in payload]
        null_mask = [is_null_val(v) for v in col_vals]
        null_count = sum(null_mask)
        ratio = null_count / total
        null_ratios[col] = round(ratio, 4)
        
        if ratio > 0.20:
            flagged_high_null.append(col)
            
        max_run = 0
        current_run = 0
        for is_null in null_mask:
            if is_null:
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 0
                
        if max_run >= 5 and null_count < total:
            systematic_columns.append(col)
            
    flagged = len(flagged_high_null) > 0 or len(systematic_columns) > 0
    
    issues = []
    if flagged_high_null:
        issues.append(f"high_null_ratio(columns: {', '.join(flagged_high_null)})")
    if systematic_columns:
        issues.append(f"systematic_dropout(columns: {', '.join(systematic_columns)})")
        
    msg = f"null_pattern: {', '.join(issues)}" if flagged else ""
    detail = {
        "null_ratios": null_ratios,
        "systematic_columns": systematic_columns
    }
    return flagged, msg, detail


def check_encoding_validity(payload: list[str] | str) -> tuple[bool, str, dict]:
    """Flag issues with bad characters or truncation exceeding 1% threshold."""
    if isinstance(payload, str):
        records = [payload]
    elif isinstance(payload, list) and all(isinstance(x, str) for x in payload):
        records = payload
    else:
        return False, "", {"control_char_count": 0, "replacement_char_count": 0, "truncated_count": 0}

    total = len(records)
    if total == 0:
        return False, "", {"control_char_count": 0, "replacement_char_count": 0, "truncated_count": 0}

    control_char_count = 0
    replacement_char_count = 0
    truncated_count = 0

    for s in records:
        has_control = any(ord(c) < 32 and c not in ('\n', '\t') for c in s)
        if has_control:
            control_char_count += 1
            
        if '\uFFFD' in s:
            replacement_char_count += 1
            
        s_strip = s.rstrip()
        if len(s_strip) < 20:
            if s_strip and s_strip[-1].isalnum():
                if not any(char in s_strip for char in '.!?;:,'):
                    truncated_count += 1

    threshold = 0.01 * total
    flagged = (control_char_count > threshold or 
               replacement_char_count > threshold or 
               truncated_count > threshold)

    detail = {
        "control_char_count": control_char_count,
        "replacement_char_count": replacement_char_count,
        "truncated_count": truncated_count
    }
    
    issues = []
    if control_char_count > threshold:
        issues.append(f"control_characters({control_char_count})")
    if replacement_char_count > threshold:
        issues.append(f"replacement_characters({replacement_char_count})")
    if truncated_count > threshold:
        issues.append(f"truncated_strings({truncated_count})")

    msg = f"encoding_validity: {', '.join(issues)}" if flagged else ""
    return flagged, msg, detail


# ---------------------------------------------------------------------------
# Score Engine
# ---------------------------------------------------------------------------
def compute_score(flags: list[str]) -> int:
    score = 100
    for flag in flags:
        for key, deduction in DEDUCTIONS.items():
            if key in flag:
                score -= deduction
                break
    return max(0, score)


# ---------------------------------------------------------------------------
# Step 8 — Data Hash (using Keccak256)
# ---------------------------------------------------------------------------
def hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "0x" + keccak(raw).hex()


# ---------------------------------------------------------------------------
# Step 10 — Main Entry Point (Refactored Router)
# ---------------------------------------------------------------------------
def run_checks(input_data: TrustGateInput) -> TrustGateOutput:
    if isinstance(input_data.payload, list) and len(input_data.payload) > 50000:
        raise ValueError(f"Payload too large: {len(input_data.payload)} records exceeds 50,000 record limit")

    detected_type = detect_data_type(input_data.payload)
    payload, coercion_warnings = coerce_payload(input_data.payload, detected_type)
    applicable_checks = CHECKS_BY_TYPE.get(detected_type, CHECKS_BY_TYPE["unknown"])

    # Pre-build np.ndarray if numeric_1d
    arr = None
    if detected_type == "numeric_1d":
        arr = np.array(payload, dtype=float)

    flags = []
    checks = {}

    if detected_type == "numeric_tabular":
        # Find all keys
        all_keys = set()
        for r in payload:
            all_keys.update(r.keys())

        # For each numeric column independently
        for col in sorted(all_keys):
            col_vals = [r[col] for r in payload if col in r and isinstance(r[col], (int, float)) and not isinstance(r[col], bool)]
            if not col_vals:
                continue
            col_arr = np.array(col_vals, dtype=float)

            if "outliers" in applicable_checks:
                flagged, msg, detail = check_outliers(col_arr)
                if "outliers" not in checks:
                    checks["outliers"] = {}
                checks["outliers"][col] = detail
                if flagged:
                    flags.append(f"{col} outliers: {msg}")

            if "drift" in applicable_checks:
                flagged, msg, detail = check_drift(col_arr)
                if "drift" not in checks:
                    checks["drift"] = {}
                checks["drift"][col] = detail
                if flagged:
                    flags.append(f"{col} drift: {msg}")

            if "flatline" in applicable_checks:
                flagged, msg, detail = check_flatline(col_arr)
                if "flatline" not in checks:
                    checks["flatline"] = {}
                checks["flatline"][col] = detail
                if flagged:
                    flags.append(f"{col} flatline: {msg}")

            if "range" in applicable_checks:
                flagged, msg, detail = check_range(col_arr, metadata=input_data.metadata)
                if "range" not in checks:
                    checks["range"] = {}
                checks["range"][col] = detail
                if flagged:
                    flags.append(f"{col} range_violation: {msg}")

        # Now run non-array-based checks for numeric_tabular
        for check_name in applicable_checks:
            if check_name == "missing":
                flagged, msg, detail = check_missing(payload, detected_type)
                checks["missing"] = detail
                if flagged:
                    flags.append(msg)
            elif check_name == "schema_consistency" and isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
                flagged, msg, detail = check_schema_consistency(payload)
                checks["schema_consistency"] = detail
                if flagged:
                    flags.append(msg)
            elif check_name == "duplicates":
                flagged, msg, detail = check_duplicates(payload, detected_type)
                checks["duplicates"] = detail
                if flagged:
                    flags.append(msg)
            elif check_name == "null_pattern" and isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
                flagged, msg, detail = check_null_pattern(payload)
                checks["null_pattern"] = detail
                if flagged:
                    flags.append(msg)
    else:
        # Standard loop for other types
        for check_name in applicable_checks:
            if check_name == "missing":
                flagged, msg, detail = check_missing(payload, detected_type)
            elif check_name == "outliers" and arr is not None:
                flagged, msg, detail = check_outliers(arr)
            elif check_name == "drift" and arr is not None:
                flagged, msg, detail = check_drift(arr)
            elif check_name == "flatline" and arr is not None:
                flagged, msg, detail = check_flatline(arr)
            elif check_name == "range" and arr is not None:
                flagged, msg, detail = check_range(arr, metadata=input_data.metadata)
            elif check_name == "temporal" and arr is not None:
                flagged, msg, detail = check_temporal(arr, metadata=input_data.metadata)
            elif check_name == "duplicates":
                flagged, msg, detail = check_duplicates(payload, detected_type)
            elif check_name == "schema_consistency":
                p = [payload] if isinstance(payload, dict) else payload
                flagged, msg, detail = check_schema_consistency(p)
            elif check_name == "cardinality":
                p = [payload] if isinstance(payload, dict) else payload
                flagged, msg, detail = check_cardinality(p)
            elif check_name == "null_pattern":
                p = [payload] if isinstance(payload, dict) else payload
                flagged, msg, detail = check_null_pattern(p)
            elif check_name == "encoding_validity":
                flagged, msg, detail = check_encoding_validity(payload)
            else:
                continue

            checks[check_name] = detail
            if flagged:
                flags.append(msg)

    score = compute_score(flags)
    passed = score >= PASS_THRESHOLD

    return TrustGateOutput(
        source_id=input_data.source_id,
        quality_score=score,
        passed=passed,
        flags=flags,
        check_details={
            **checks,
            "detected_type": detected_type,
            "coercion_warnings": coercion_warnings,
            "buyer_declared_type": input_data.data_type,
        },
        checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        data_hash=hash_payload(input_data.payload),
        reputation_summary=input_data.reputation_context,
    )