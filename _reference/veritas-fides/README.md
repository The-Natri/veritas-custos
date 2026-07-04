# Veritas Fides

> *Veritas* (truth) + *Fides* (trust) — proven trust infrastructure for the agent economy.

Veritas Fides is a three-agent data verification system built on Base Mainnet using the CROO Agent Protocol. It provides a missing trust layer for the agent economy — enabling agents to verify data quality, issue tamper-proof on-chain certificates, and query historical source reputation before transacting.

---

## The Problem

In the agent economy, agents pay each other for data automatically with no trust layer. A malicious or low-quality data source can repeatedly sell bad data with no accountability. Veritas Fides solves this.

---

## Architecture

```
Buyer
  └─→ TrustGate ($0.15 USDC)
        ├─→ ReputationOracle ($0.05 USDC)  — historical trust query
        ├─→ runs 11 universal data quality checks
        └─→ SourceAttest ($0.02 USDC)      — writes on-chain certificate
              ↓
        Returns: score, trust_tier, cert_id, tx_hash
```

### Agent 1: TrustGate — Verification Layer
- Accepts inline data payload or remote URL
- Queries ReputationOracle for historical trust before running checks
- Runs universal data quality checks across 9 payload types
- Calls SourceAttest to write tamper-proof certificate on-chain
- Returns unified verification response to buyer

### Agent 2: SourceAttest — Evidence Layer
- Called by TrustGate after verification
- Writes certificate via `deliver_order()` using `DeliverableType.SCHEMA`
- Returns `cert_id` and `tx_hash`

### Agent 3: ReputationOracle — Trust Layer
- Called by TrustGate before verification runs
- Reads historical certificates from persistent JSON store
- Returns: total checks, pass rate, trust tier, trend

---

## Universal Check Engine

`core/trustgate_checks.py` supports 9 payload types with 11 checks:

| Payload Type | Description |
|---|---|
| `numeric_1d` | List of floats — sensor, price feed, time series |
| `numeric_tabular` | List of dicts, all numeric values |
| `mixed_tabular` | List of dicts, mixed numeric/string |
| `categorical_tabular` | List of dicts, all string values |
| `text` | List of strings |
| `nested_json` | Single dict |
| `raw_text` | Single string |
| `mixed_raw` | Mixed type list |
| `unknown` | Fallback |

**Checks:** `missing`, `outliers`, `drift`, `flatline`, `range`, `temporal`, `duplicates`, `schema_consistency`, `cardinality`, `null_pattern`, `encoding_validity`

Scoring starts at 100, with deductions per flag. Pass threshold = 70. Payload hash uses **Keccak256**.

---

## Trust Tiers

| Tier | Min Checks | Min Pass Rate |
|---|---|---|
| Gold | 10 | 90% |
| Silver | 5 | 75% |
| Bronze | 1 | 0% |
| Unrated | 0 | — |

Trend is computed from last 5 vs prior history (needs 6+ checks): `improving` / `degrading` / `stable` / `insufficient_data`

---

## Quick Start (Judge Test)

```bash
cp .env.example .env
# Fill in your API keys and service IDs from the CROO Dashboard
pip install -r requirements.txt
python run_judge_test.py
```

### Run full test suite
```bash
pytest tests/test_pipeline.py -v
```

Expected: **10/10 passing**

---

## Environment Setup

```bash
CROO_API_URL=https://api.croo.network
CROO_WS_URL=wss://api.croo.network/ws
TRUSTGATE_API_KEY=croo_sk_...
SOURCEATTEST_API_KEY=croo_sk_...
REPUTATIONORACLE_API_KEY=croo_sk_...
TRUSTGATE_SERVICE_ID=your-trustgate-service-id
SOURCEATTEST_SERVICE_ID=your-sourceattest-service-id
REPUTATIONORACLE_SERVICE_ID=your-reputationoracle-service-id
```

---

## SDK Methods Used

| Method | Used By |
|---|---|
| `AgentClient` | All three agents |
| `EventType.ORDER_CREATED` | TrustGate, SourceAttest, ReputationOracle |
| `EventType.ORDER_PAID` | TrustGate, SourceAttest, ReputationOracle |
| `get_negotiation()` | All three agents |
| `accept_negotiation()` | All three agents |
| `pay_order()` | TrustGate (pays SourceAttest + ReputationOracle) |
| `deliver_order()` | All three agents |
| `reject_order()` | All three agents (timeout + exception handling) |
| `DeliverableType.SCHEMA` | All three agents |

---

## Production Hardening

- 2-minute timeout per pipeline step, 6-minute hard ceiling
- `reject_order()` on timeout and exceptions — releases escrow automatically
- Retry with exponential backoff (3 retries, 2s base)
- Persistent reputation store with atomic writes
- Thread safety with `threading.Lock()`
- Concurrency limit: 3 parallel orders max
- Per-requester rate limiting: 10s cooldown
- Structured logging to `logs/trustgate.log`

---

## File Structure

```
veritas-fides/
├── agents/
│   ├── trustgate_agent.py
│   ├── sourceattest_agent.py
│   └── reputation_oracle_agent.py
├── core/
│   ├── trustgate_checks.py       # Universal check engine, 11 checks
│   ├── sourceattest_logic.py     # Keccak256, certificate logic
│   ├── reputation_oracle_logic.py # Persistent JSON store
│   └── orchestrator.py           # Timeouts, retry, escrow release
├── data/                         # certificate_store.json (gitignored)
├── logs/                         # trustgate.log (gitignored)
├── mock_payloads/
│   ├── clean_data.json
│   └── corrupted_data.json
├── tests/
│   └── test_pipeline.py          # 10/10 passing
├── docs/
│   └── CTS-1.md
├── .env.example
├── requirements.txt
└── run_judge_test.py
```

---

## Input Contract

**Option A — Inline payload:**
```json
{
  "source_id": "unique-identifier",
  "data_type": "sensor",
  "payload": [1.2, 3.4, 5.6],
  "metadata": {"expected_range": [0, 100], "frequency_hz": 10}
}
```

**Option B — Remote URL:**
```json
{
  "source_id": "unique-identifier",
  "data_type": "sensor",
  "data_url": "https://example.com/data.json",
  "metadata": {}
}
```

---

## License

MIT — see [LICENSE](./LICENSE)
