"""
Decision Agent (Veritas Custos) - LLM-powered trust verdict layer.
Uses Google Gemini (free tier, no credit card) to review the deterministic
identity check score and produce a written justification for the final tier.
The reasoning is stored locally, keyed by the on-chain tx_hash so it is
permanently linkable to the immutable Testnet transaction.
"""
import json
import os
import time
import urllib.request
import urllib.error

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env()
GEMINI_API_KEY = ENV.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    GEMINI_API_KEY = GEMINI_API_KEY.strip()

# Use gemini-2.5-flash since gemini-2.0-flash returns 429 quota limits for this key
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

DECISIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "decisions")


def _call_gemini(prompt: str) -> tuple:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not found in .env")

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,  # Increased token ceiling for detailed model output
        }
    }).encode()

    req = urllib.request.Request(
        GEMINI_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    candidate = data["candidates"][0]
    text = candidate["content"]["parts"][0]["text"].strip()
    finish_reason = candidate.get("finishReason", "STOP")
    return text, finish_reason


def make_verdict(identity_result: dict, on_chain_before: dict) -> dict:
    """
    Asks Gemini to review the deterministic identity check and produce
    a written trust verdict with optional tier adjustment.
    Returns a verdict dict with: final_tier, reasoning, adjusted (bool).
    """
    deterministic_score = identity_result.get("trust_score", 0)
    deterministic_tier = identity_result.get("tier", "Rejected")
    balance_cspr = round(identity_result.get("balance_motes", 0) / 1_000_000_000, 4)
    deploy_count = identity_result.get("deploy_count", 0)
    age_days = identity_result.get("account_age_days", 0)
    score_breakdown = identity_result.get("score_breakdown", {})
    already_registered = on_chain_before.get("registered", False)
    previous_tier = on_chain_before.get("tier", "Unrated")
    job_count = on_chain_before.get("job_count", 0)

    prompt = f"""You are a trust verification agent in a multi-agent system on the Casper blockchain.
Your job is to review a deterministic identity check result for an on-chain agent and produce a final trust verdict.

AGENT IDENTITY SIGNALS:
- Balance: {balance_cspr} CSPR (points: {score_breakdown.get("balance_points",0)}/30)
- Deploy count (on-chain transactions): {deploy_count} (points: {score_breakdown.get("deploy_count_points",0)}/35)
- Account age: {age_days} days (points: {score_breakdown.get("account_age_points",0)}/35)
- Score breakdown: balance={score_breakdown.get("balance_points",0)}/30, deploys={score_breakdown.get("deploy_count_points",0)}/35, age={score_breakdown.get("account_age_points",0)}/35
- Deterministic trust score: {deterministic_score}/100
- Deterministic tier assigned: {deterministic_tier}

ON-CHAIN HISTORY:
- Previously registered: {already_registered}
- Previous tier: {previous_tier}
- Jobs completed: {job_count}

Your task:
1. Review these signals critically.
2. Decide if the deterministic tier ({deterministic_tier}) is appropriate, or if it should be adjusted (only Rejected/Low/Medium/High are valid).
3. Write 2-3 sentences explaining your reasoning in plain language.
4. IMPORTANT: If you decide to adjust/override the tier (i.e. final_tier != {deterministic_tier}), your reasoning text MUST explicitly name the specific field and value that triggered the override (e.g. 'job_count = 0 overrides the deterministic High tier despite balance_points = 30/30 and deploy_count_points = 30/35'), rather than a vague general paragraph. Keep it readable prose, but precise and auditable.
5. Respond ONLY with valid JSON in exactly this format, no markdown, no extra text:
{{"final_tier": "Medium", "reasoning": "Your explanation here.", "adjusted": false}}"""

    raw = ""
    finish_reason = "N/A"
    try:
        raw, finish_reason = _call_gemini(prompt)
        # Strip any accidental markdown fences
        raw_clean = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw_clean)
        valid_tiers = {"Rejected", "Low", "Medium", "High"}
        if parsed.get("final_tier") not in valid_tiers:
            parsed["final_tier"] = deterministic_tier
            parsed["adjusted"] = False
        parsed["adjusted"] = parsed.get("final_tier") != deterministic_tier
        return parsed
    except Exception as e:
        # Log raw response and finishReason server-side for debugging
        print(f"[decision_agent] LLM call failed: {type(e).__name__}: {e}. Raw response (truncated): {raw[:500]}. FinishReason: {finish_reason}")
        # Fallback: keep deterministic result, note the failure
        return {
            "final_tier": deterministic_tier,
            "reasoning": f"LLM verdict unavailable ({type(e).__name__}: {str(e)[:200]}). Deterministic score of {deterministic_score}/100 applied directly.",
            "adjusted": False,
            "error": str(e),
        }


def save_verdict(tx_hash: str, account_hash: str, identity_result: dict, verdict: dict):
    """Saves the full reasoning to decisions/{tx_hash}.json."""
    os.makedirs(DECISIONS_DIR, exist_ok=True)
    record = {
        "tx_hash": tx_hash,
        "account_hash": account_hash,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity_signals": {
            "balance_cspr": round(identity_result.get("balance_motes", 0) / 1_000_000_000, 4),
            "deploy_count": identity_result.get("deploy_count"),
            "account_age_days": identity_result.get("account_age_days"),
            "trust_score": identity_result.get("trust_score"),
            "deterministic_tier": identity_result.get("tier"),
        },
        "verdict": verdict,
        "on_chain_proof": f"https://testnet.cspr.live/transaction/{tx_hash}",
    }
    path = os.path.join(DECISIONS_DIR, f"{tx_hash}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path
