# Veritas Custos
### *Guardian of Truth*

**Multi-agent trust verification for the Casper agent economy.**

> "Casper wants to be the trust layer for the agent economy. Veritas Custos is the trust layer for the agents on Casper."

---

## What It Does

Veritas Custos answers one question on behalf of any agent, protocol, or system on Casper: **can this agent be trusted?**

It does this by running an account hash through a four-agent verification pipeline, writing a signed trust verdict to the Casper Testnet, and exposing the result through an x402-gated HTTP server. Any downstream agent can query the on-chain trust record and make access decisions accordingly — which is exactly what the included consuming agent demonstrates.

---

## Architecture

```
Agent Request
     │
     ▼
┌─────────────────────────────────────────────┐
│  x402 HTTP Server (port 8402)               │
│  /verify · /decisions/<tx> · /health        │
└────────────────┬────────────────────────────┘
                 │
    ┌────────────▼─────────────┐
    │     TrustGate Pipeline   │
    │                          │
    │  1. Identity Checker     │  ← CSPR.cloud API: balance, deploys, account age
    │  2. Reputation Oracle    │  ← On-chain contract: registered tier, job history
    │  3. Decision Agent       │  ← Gemini 2.5 Flash: LLM review, tier override
    │  4. Source Attest        │  ← Casper Testnet write: verdict → smart contract
    └────────────┬─────────────┘
                 │
    ┌────────────▼─────────────┐
    │   Odra Smart Contract    │  ← Casper Testnet, immutable trust registry
    │   contract-6f5ac8ab...   │
    └──────────────────────────┘
                 │
    ┌────────────▼─────────────┐
    │   Consuming Agent        │  ← Reads on-chain tier, makes access decision
    │   trust_gate_client.py   │
    └──────────────────────────┘
```

---

## The Four Agents

### 1. Identity Checker
Queries the CSPR.cloud REST API to gather raw on-chain signals for the target account: CSPR balance, total deploy count, and account age in days. Computes a deterministic trust score (0–100) across three weighted dimensions:

| Dimension | Max Points |
|-----------|-----------|
| Balance   | 30        |
| Deploys   | 35        |
| Account Age | 35      |

Outputs a trust score, deterministic tier (High / Medium / Low / Rejected), and full score breakdown.

### 2. Reputation Oracle
Queries the deployed Veritas Custos smart contract directly to check whether the agent has been previously registered, what tier was last recorded, and how many on-chain jobs have been completed. This is the memory layer — it gives the Decision Agent a longitudinal view, not just a point-in-time snapshot.

### 3. Decision Agent
Powered by Gemini 2.5 Flash (`gemini-2.5-flash`, temperature 0.3, maxOutputTokens 4096). Receives the full identity signals and reputation history, then produces a final verdict with three outputs:

- `final_tier` — the authoritative trust classification
- `adjusted` — whether the LLM overrode the deterministic score
- `reasoning` — a human-readable explanation citing the specific field and value that triggered any override (e.g. *"jobs_completed = 0 overrides the deterministic High tier despite balance_points = 30/30"*)

If Gemini is unavailable (503, quota, timeout), the agent falls back gracefully to the deterministic score with a logged explanation — no pipeline failure.

### 4. Source Attest
Calls `casper-client` to invoke either `register_agent` (first time) or `update_reputation` (subsequent) on the deployed Odra smart contract. The resulting deploy transaction hash is the on-chain proof of verification — immutable, auditable, permanent. Every verdict is also persisted locally as a JSON file keyed by tx hash under `agent_verifier/decisions/`.

---

## Smart Contract

Built with the [Odra framework](https://odra.dev) and deployed to Casper Testnet. Stores per-agent trust records in a contract-level dictionary keyed by account hash.

| Field | Value |
|-------|-------|
| Contract Package Hash | `contract-package-219bdf679193ca11da406ffa82774ebf21a7745978d43106e90505ead7f51704` |
| Active Contract Hash | `contract-6f5ac8ab98f5419ff406cd2494b04dd05d6f50db287ab252df9d6473c9b180a9` |
| Deploy Transaction | `ff66ea0f801579218fec1806e00556058ebd75d68f4cc0523dffce726fff5a19` |
| Network | casper-test |
| RPC Node | `http://65.108.41.125:7777` |
| Entry Points | `register_agent`, `update_reputation` |

---

## x402 Payment Layer

The verification endpoint is gated by the [x402 micropayment protocol](https://x402.org) (x402Version: 2). The server declares a payment requirement in CSPR before serving verification results.

**On the current state of x402 on Casper:**

The server-side facilitator flow is production-correct. However, the production Casper x402 facilitator (`x402-facilitator.cspr.cloud`) currently supports CEP-18 token settlement only — native CSPR transfers are not yet supported. Additionally, Casper's browser-wallet EIP-712 signing SDK has not yet shipped, which means client-side payment signing cannot be completed from a web context today.

For the demo, the server runs in `DEV_MODE` (facilitator URL intentionally unconfigured), which skips real settlement while keeping the full x402 negotiation protocol intact. This is the correct engineering response to a gap in Casper's own tooling — not a limitation of Veritas Custos. The `/health` endpoint confirms:

```json
{
  "mode": "dev",
  "facilitator": "not_configured",
  "x402Version": 2
}
```

Payment flow can be completed end-to-end the moment Casper ships CEP-18 support and the browser-wallet signing SDK.

---

## Proven On-Chain Transactions

Three independent transactions confirmed on Casper Testnet, covering both contract entry points and two distinct accounts:

| Transaction Hash | Type | Account |
|-----------------|------|---------|
| `de230d7a1af2dff67ffcc3ab0c3132721caa788464bfa04c37774c279ae799c6` | `update_reputation` | Primary |
| `a78117b6b391c5d0aa5b61b88ac1c906ef40e3861b8330a6e4a790e67e557fe3` | `update_reputation` | Primary |
| `dbf4a782f5e050f30eb3927fcb26e54062e25e3331eed5c86dca0a578bbb82aa` | `register_agent` | Independent (`23058a429ae...`) |

Verify any transaction at [testnet.cspr.live](https://testnet.cspr.live).

---

## Consuming Agent Demo

`consuming_agent/trust_gate_client.py` demonstrates the full trust-layer value proposition: a separate agent reading Veritas Custos's on-chain verdict and making an autonomous access decision based on it.

```bash
python3 trust_gate_client.py 7ca9cd84b0e3a34d328fb2c10611871c56c05abb3fc8a49fbcc76c951d0b766f
```

```
═══════════════════════════════════════
VERITAS CUSTOS — TRUST GATE CLIENT
Consuming agent access decision demo
═══════════════════════════════════════
Agent:  account-hash-7ca9cd84b0e3a34d328fb2c10611871c56c05abb3fc8a49fbcc76c951d0b766f
Stored tier (on-chain): High
Decision: ACCESS GRANTED ✓
═══════════════════════════════════════
Reasoning: Agent tier 'High' meets minimum threshold (Medium+)
for full system access.
```

Access policy:

| On-Chain Tier | Decision |
|--------------|----------|
| High / Medium | ACCESS GRANTED |
| Low | ACCESS RESTRICTED (read-only) |
| Rejected / Unrated | ACCESS DENIED |

---

## Demo Verification Results

Three distinct agent profiles verified end-to-end, showing the full range of pipeline behavior:

**High Trust — Established account (95/100)**
- Balance: 30/30 · Deploys: 30/35 · Age: 35/35
- LLM confirms deterministic tier, no override
- 10 jobs completed, 1912 days account age

**Medium Trust — Active but unproven (85/100, overridden)**
- Score warrants High deterministically
- LLM overrides to Medium: `jobs_completed = 0` despite strong balance and deploy signals
- Demonstrates AI layer adding judgment beyond raw scoring

**Low Trust — New account (30/100)**
- Balance: 30/30 · Deploys: 0/35 · Age: 0/35
- No on-chain history, no account age
- LLM confirms: no basis for a higher tier

---

## UI

Cinematic space-console HUD served at `http://localhost:8402`. Features:

- Three.js starfield with cinematic intro sequence
- Airlock door animation: pneumatic slide-apart on approve, vacuum ejection on reject
- Web Audio API synthesized sounds (no external audio files)
- Live agent pipeline status with per-step updates
- 4-axis radar chart (Balance / Deploys / Age / Jobs)
- Algorithmic vs AI verdict comparison with OVERRIDE / CONFIRMED badge
- Raw verdict JSON toggle fetching from `/decisions/<tx_hash>`
- Tier history tracking across re-verifications

---

## Running Locally

**Prerequisites:** Python 3.12+, casper-client, WSL2 (Ubuntu 24.04), valid Casper Testnet keys

```bash
# Start the server
cd agent_verifier
python3 -u server.py

# Verify an agent (in a separate terminal)
curl -s "http://localhost:8402/verify?account_hash=<account_hash>" \
  -H "X-PAYMENT: dev_payment" | python3 -m json.tool

# Run the consuming agent
cd consuming_agent
python3 trust_gate_client.py <account_hash>

# Check a saved verdict
curl -s http://localhost:8402/decisions/<tx_hash> | python3 -m json.tool
```

Zero external Python dependencies — stdlib only (`urllib`, `json`, `subprocess`, `http.server`).

---

## Why Veritas Custos

*Veritas Custos* is Latin for **Guardian of Truth**.

The agent economy on Casper faces a trust bootstrapping problem: how does a protocol, DAO, or smart contract decide which agents to grant access to, delegate work to, or accept signed outputs from? On-chain history is public but uninterpreted. Reputation is implicit and siloed.

Veritas Custos makes trust explicit, on-chain, and queryable. It is not a monitoring tool or an analytics dashboard — it is infrastructure. The consuming agent demo shows the end state: any agent in the Casper ecosystem can call `query-contract-key` against the Veritas Custos registry and receive a trust verdict that was written by a multi-agent pipeline, reviewed by an LLM, and attested on-chain.

That is the trust layer the agent economy needs.

---

## Project Structure

```
veritas-custos/
├── keys/                        # Casper secp256k1 keypair
├── veritas_custos_contract/     # Odra smart contract (deployed)
├── identity_checker/            # Standalone identity checker
├── agent_verifier/
│   ├── server.py                # x402 HTTP server (port 8402)
│   ├── ui.html                  # Space console HUD
│   ├── trustgate.py             # Pipeline orchestrator
│   ├── decisions/               # Persisted verdict JSONs
│   └── core/
│       ├── identity_checker.py
│       ├── reputation_oracle.py
│       ├── decision_agent.py    # Gemini 2.5 Flash
│       └── source_attest.py
└── consuming_agent/
    └── trust_gate_client.py     # Downstream agent demo
```

---

*Built for the Casper Agentic Buildathon — DoraHacks, July 2026.*
