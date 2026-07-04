"""
ReputationOracle (Veritas Custos) - On-chain trust history reader.
Ported from Veritas Fides: shells out to the deployed contracts CLI
(get_reputation) instead of reading a local JSON certificate store.
"""
import json
import subprocess
import time
import os

CLI_PROJECT_DIR = "/home/naveen_m_n/veritas-custos/veritas_custos_contract"
CLI_BIN = "veritas_custos_contract_cli"


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    cmd = ["cargo", "run", "--bin", CLI_BIN, "--"] + args
    env = os.environ.copy()
    cargo_bin = "/home/naveen_m_n/.cargo/bin"
    if cargo_bin not in env.get("PATH", ""):
        env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"
    result = subprocess.run(
        cmd, cwd=CLI_PROJECT_DIR, capture_output=True, text=True, timeout=120, env=env
    )
    return result.returncode, result.stdout, result.stderr


def get_on_chain_reputation(agent_address: str) -> dict:
    """
    Queries the contract for an existing AgentRecord via get_reputation.
    Returns registered=False if the agent has never been registered.
    """
    queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    code, out, err = _run_cli(["contract", "VeritasCustos", "get_reputation", "--agent_address", agent_address, "--json"])

    if code != 0:
        return {
            "account_hash": agent_address,
            "registered": False,
            "score": None,
            "tier": "Unrated",
            "job_count": 0,
            "queried_at": queried_at,
            "raw_stderr": err.strip()[-500:],
        }

    try:
        start_idx = out.find("{")
        end_idx = out.rfind("}")
        if start_idx != -1 and end_idx != -1:
            parsed = json.loads(out[start_idx:end_idx+1])
        else:
            parsed = None
    except Exception:
        parsed = None

    if not parsed or parsed.get("result") == "None" or not parsed.get("result"):
        return {
            "account_hash": agent_address,
            "registered": False,
            "score": None,
            "tier": "Unrated",
            "job_count": 0,
            "queried_at": queried_at,
        }

    try:
        record = json.loads(parsed["result"])
    except Exception:
        record = {}

    return {
        "account_hash": agent_address,
        "registered": True,
        "score": int(record.get("score")) if record.get("score") is not None else None,
        "tier": record.get("tier"),
        "job_count": int(record.get("job_count")) if record.get("job_count") is not None else 0,
        "queried_at": queried_at,
        "raw": record,
    }
