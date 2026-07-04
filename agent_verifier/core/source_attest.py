"""
SourceAttest (Veritas Custos) - On-chain certificate writer.
Ported from Veritas Fides: instead of simulating a tx_hash locally,
this calls register_agent (new agent) or update_reputation (existing
agent) on the deployed Odra contract, via the odra-cli subprocess,
and parses the REAL transaction hash from its output.
"""
import re
import subprocess
import time
import os

CLI_PROJECT_DIR = "/home/naveen_m_n/veritas-custos/veritas_custos_contract"
CLI_BIN = "veritas_custos_contract_cli"

TX_HASH_RE = re.compile(r'Transaction "([a-f0-9]+)" successfully executed')


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    cmd = ["cargo", "run", "--bin", CLI_BIN, "--"] + args
    env = os.environ.copy()
    cargo_bin = "/home/naveen_m_n/.cargo/bin"
    if cargo_bin not in env.get("PATH", ""):
        env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"
    result = subprocess.run(
        cmd, cwd=CLI_PROJECT_DIR, capture_output=True, text=True, timeout=180, env=env
    )
    return result.returncode, result.stdout, result.stderr


def write_on_chain(agent_address: str, score: int, tier: str, already_registered: bool) -> dict:
    """
    Writes the trust score to chain. Uses register_agent for first-time
    agents, update_reputation for agents that already have a record.
    """
    attested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry_point = "update_reputation" if already_registered else "register_agent"
    score_arg = "--new_score" if already_registered else "--initial_score"

    code, out, err = _run_cli([
        "contract", "VeritasCustos", entry_point,
        "--agent_address", agent_address,
        score_arg, str(score),
        "--tier", tier,
        "--gas", "3.0 cspr",
    ])

    combined_output = out + err
    match = TX_HASH_RE.search(combined_output)
    tx_hash = match.group(1) if match else None

    return {
        "entry_point": entry_point,
        "agent_address": agent_address,
        "score": score,
        "tier": tier,
        "tx_hash": tx_hash,
        "attested_at": attested_at,
        "success": code == 0 and tx_hash is not None,
        "raw_output_tail": combined_output.strip()[-500:],
    }
