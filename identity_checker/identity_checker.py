import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone


def load_env():
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "identity_checker", ".env"),
    ]
    for path in candidates:
        if os.path.exists(path):
            env = {}
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            if "CSPR_CLOUD_API_KEY" in env:
                return env, path
    # Fallback to the first found env if no key matches
    for path in candidates:
        if os.path.exists(path):
            env = {}
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            return env, path
    return {}, None


ENV, ENV_PATH = load_env()
API_KEY = ENV.get("CSPR_CLOUD_API_KEY")
BASE_URL = "https://api.testnet.cspr.cloud"

if not API_KEY:
    print("ERROR: CSPR_CLOUD_API_KEY not found in any .env file. Checked paths:")
    print("  1. core/.env")
    print("  2. agent_verifier/.env")
    print("  3. identity_checker/.env")
    sys.exit(1)


def api_get(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"accept": "application/json", "authorization": API_KEY})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_account(account_hash):
    data = api_get(f"/accounts/{account_hash}")
    return data.get("data", data)


def get_deploys_by_public_key(public_key, limit=50):
    # Fix: CSPR.cloud requires order_by to be passed along with order_direction
    data = api_get("/deploys", {
        "caller_public_key": public_key,
        "limit": limit,
        "order_by": "timestamp",
        "order_direction": "ASC"
    })
    return data.get("data", [])


def score_balance(motes: int) -> int:
    cspr = motes / 1_000_000_000
    if cspr <= 0:
        return 0
    if cspr < 1:
        return 5
    if cspr < 10:
        return 15
    if cspr < 100:
        return 25
    return 30


def score_deploy_count(count: int) -> int:
    if count == 0:
        return 0
    if count <= 2:
        return 10
    if count <= 5:
        return 20
    if count <= 15:
        return 30
    return 35


def score_age(age_days) -> int:
    if age_days is None:
        return 0
    if age_days < 1:
        return 5
    if age_days < 7:
        return 15
    if age_days < 30:
        return 25
    return 35


def tier_from_score(score: int) -> str:
    if score < 25:
        return "Rejected"
    if score < 50:
        return "Low"
    if score < 75:
        return "Medium"
    return "High"


def check_identity(account_hash: str) -> dict:
    result = {
        "account_hash": account_hash,
        "public_key": None,
        "balance_motes": None,
        "deploy_count": None,
        "account_age_days": None,
        "first_deploy_timestamp": None,
        "score_breakdown": {},
        "trust_score": 0,
        "tier": "Rejected",
        "errors": [],
    }

    balance_pts, deploy_pts, age_pts = 0, 0, 0

    try:
        account = get_account(account_hash)
        balance = int(account.get("balance", 0))
        public_key = account.get("public_key")
        result["balance_motes"] = balance
        result["public_key"] = public_key
        balance_pts = score_balance(balance)
    except Exception as e:
        result["errors"].append(f"balance check failed: {e}")
        public_key = None

    if public_key:
        try:
            deploys = get_deploys_by_public_key(public_key)
            result["deploy_count"] = len(deploys)
            deploy_pts = score_deploy_count(len(deploys))

            if deploys:
                first_ts = deploys[0].get("timestamp")
                result["first_deploy_timestamp"] = first_ts
                if first_ts:
                    first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - first_dt).total_seconds() / 86400
                    result["account_age_days"] = round(age_days, 2)
                    age_pts = score_age(age_days)
        except Exception as e:
            result["errors"].append(f"deploy history check failed: {e}")
    else:
        result["errors"].append("no public_key resolved, skipped deploy history check")

    total = balance_pts + deploy_pts + age_pts
    result["score_breakdown"] = {
        "balance_points": balance_pts,
        "deploy_count_points": deploy_pts,
        "account_age_points": age_pts,
        "max_possible": 100,
    }
    result["trust_score"] = total
    result["tier"] = tier_from_score(total)

    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 identity_checker.py <account_hash>")
        sys.exit(1)

    account_hash = sys.argv[1]
    if account_hash.startswith("account-hash-"):
        account_hash = account_hash.replace("account-hash-", "")

    result = check_identity(account_hash)
    print(json.dumps(result, indent=2))
