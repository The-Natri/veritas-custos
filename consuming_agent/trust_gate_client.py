#!/usr/bin/env python3
"""
Veritas Custos - Trust Gate Client
Consuming agent access decision demo.
"""
import json
import os
import subprocess
import sys

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", "agent_verifier", ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def extract_tier_from_json(data):
    if not isinstance(data, dict):
        return None
    # 1. Direct checks
    if "tier" in data:
        t = data["tier"]
        if isinstance(t, str):
            return t
        if isinstance(t, dict) and len(t) > 0:
            return list(t.keys())[0]
            
    # 2. Try nested result -> stored_value -> CLValue -> parsed
    stored_val = data.get("result", {}).get("stored_value", {})
    if "CLValue" in stored_val:
        cl_val = stored_val["CLValue"]
        parsed = cl_val.get("parsed")
        if isinstance(parsed, dict):
            tier = parsed.get("tier")
            if tier:
                if isinstance(tier, str):
                    return tier
                if isinstance(tier, dict) and len(tier) > 0:
                    return list(tier.keys())[0]
        elif isinstance(parsed, str):
            if parsed in ("High", "Medium", "Low", "Rejected"):
                return parsed
            try:
                nested = json.loads(parsed)
                if isinstance(nested, dict) and "tier" in nested:
                    return nested["tier"]
            except Exception:
                pass
                
    # 3. Recursively search for any "tier" key
    def find_key_recursive(d, key):
        if isinstance(d, dict):
            if key in d:
                return d[key]
            for k, v in d.items():
                res = find_key_recursive(v, key)
                if res is not None:
                    return res
        elif isinstance(d, list):
            for item in d:
                res = find_key_recursive(item, key)
                if res is not None:
                    return res
        return None
        
    t = find_key_recursive(data, "tier")
    if t:
        if isinstance(t, str):
            return t
        if isinstance(t, dict) and len(t) > 0:
            return list(t.keys())[0]
            
    return None


def query_tier_via_cli(account_hash: str) -> str:
    # First, try to query using the compiled veritas_custos_contract_cli
    cli_path = "/home/naveen_m_n/veritas-custos/veritas_custos_contract/target/debug/veritas_custos_contract_cli"
    if os.path.exists(cli_path):
        cmd = [
            cli_path,
            "contract",
            "VeritasCustos",
            "get_reputation",
            "--agent_address",
            f"account-hash-{account_hash}"
        ]
        try:
            res = subprocess.run(
                cmd,
                cwd="/home/naveen_m_n/veritas-custos/veritas_custos_contract",
                capture_output=True,
                text=True,
                timeout=30
            )
            if res.returncode == 0:
                out = res.stdout
                if "Call result:" in out:
                    start_idx = out.find("{")
                    end_idx = out.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        data = json.loads(out[start_idx:end_idx+1])
                        tier = extract_tier_from_json(data)
                        if tier:
                            return str(tier)
        except Exception:
            pass
            
    # Fallback to the casper-client command (with either query-contract-key or query-global-state)
    casper_client_path = "/home/naveen_m_n/.cargo/bin/casper-client"
    if not os.path.exists(casper_client_path):
        casper_client_path = "casper-client" # try system PATH
        
    cmd = [
        casper_client_path,
        "query-contract-key",
        "--node-address",
        "http://65.108.41.125:7777",
        "--contract-hash",
        "contract-6f5ac8ab98f5419ff406cd2494b04dd05d6f50db287ab252df9d6473c9b180a9",
        "--key",
        f"account-hash-{account_hash}",
        "--output-format",
        "json"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            parsed = json.loads(res.stdout)
            tier = extract_tier_from_json(parsed)
            if tier:
                return str(tier)
    except Exception:
        pass

    return "Unrated"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 trust_gate_client.py <account_hash>")
        sys.exit(1)
        
    account_hash = sys.argv[1].replace("account-hash-", "").strip()
    
    env = _load_env()
    network = env.get("CASPER_NETWORK", "casper-test")
    
    tier = query_tier_via_cli(account_hash)
    
    # Decisions logic
    if tier in ("High", "Medium"):
        decision = "ACCESS GRANTED ✓"
        reasoning = f"Agent tier '{tier}' meets minimum threshold (Medium+) for full system access."
    elif tier == "Low":
        decision = "ACCESS RESTRICTED (read-only) ⚠"
        reasoning = "Agent tier is 'Low'. Write access disabled, read-only permissions enforced."
    elif tier == "Rejected":
        decision = "ACCESS DENIED ✗"
        reasoning = "Agent tier is 'Rejected'. Trust validation failed."
    else:
        decision = "ACCESS DENIED ✗"
        reasoning = "Agent not registered in Veritas Custos trust registry."
        tier = "Unrated"

    print("═══════════════════════════════════════")
    print("VERITAS CUSTOS — TRUST GATE CLIENT")
    print("Consuming agent access decision demo")
    print("═══════════════════════════════════════")
    print(f"Agent:  account-hash-{account_hash}")
    print(f"Stored tier (on-chain): {tier}")
    print(f"Decision: {decision}")
    print("═══════════════════════════════════════")
    print(f"Reasoning: {reasoning}")

if __name__ == "__main__":
    main()
