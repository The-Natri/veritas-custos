# CTS-1: CROO Trust Standard v1
**Version:** 1.0.0-draft  
**Status:** Draft — open for comment  
**Authors:** Veritas Fides (CROO Agent Hackathon 2025)  
**Date:** June 2025  
**License:** CC0 1.0 Universal (public domain)

---

## Abstract

CTS-1 defines a minimal, interoperable standard for data quality certification in the CROO agent economy. Any agent that produces or consumes structured data payloads MAY implement CTS-1 to signal that its data has been independently verified, attested on-chain, and assigned a trust tier based on historical performance. CTS-1 is intentionally domain-agnostic — it applies equally to price feeds, sensor streams, research outputs, and any other time-series or structured data exchanged between agents.

---

## 1. Motivation

Autonomous agents in the CROO network pay each other for data without human oversight. Once a `PayOrder` settles on-chain, no refund is possible. This creates an asymmetric trust problem: the buyer has no way to verify data quality before payment releases, and the seller has no verifiable reputation to offer.

CTS-1 addresses this by defining:
1. A standard schema for what a verified payload looks like
2. A standard certificate format for on-chain attestation
3. A standard reputation model for historical trust scoring

A data payload that carries a valid CTS-1 certificate is one that has been:
- Statistically verified by an independent agent
- Permanently attested on-chain with a tamper-proof hash
- Linked to a queryable reputation history for its source

---

## 2. Definitions

| Term | Definition |
|---|---|
| **Payload** | The raw data array submitted for verification |
| **Source** | The originating agent or data feed, identified by `source_id` |
| **Certificate** | The on-chain record of a completed verification event |
| **Attestor** | The agent that performs verification and issues certificates |
| **Trust Tier** | A categorical reputation score assigned to a source based on historical certificates |
| **CTS-1 Compliant** | A payload or agent that fully satisfies the requirements in Section 4 |

---

## 3. Payload Schema

A CTS-1 compliant input payload MUST conform to the following structure:

```json
{
  "source_id": "string — unique identifier for the data source",
  "data_type": "price_feed | sensor | time_series",
  "payload": ["array of numeric values"],
  "metadata": {
    "expected_range": ["[min, max] — declared valid range for values"],
    "frequency_hz": "number — sampling frequency of the data"
  }
}
```

### 3.1 Field Requirements

| Field | Type | Required | Notes |
|---|---|---|---|
| `source_id` | string | YES | Stable identifier, consistent across submissions |
| `data_type` | enum | YES | Must be one of the declared types |
| `payload` | number[] | YES | Minimum 10 values required for meaningful analysis |
| `metadata.expected_range` | [number, number] | RECOMMENDED | Enables range validation check |
| `metadata.frequency_hz` | number | RECOMMENDED | Enables temporal consistency check |

---

## 4. Certificate Schema

A CTS-1 certificate is the on-chain record produced by a compliant Attestor after verification. It MUST contain:

```json
{
  "cert_id": "string — unique certificate UUID",
  "source_id": "string — matches input payload source_id",
  "quality_score": "integer 0-100",
  "passed": "boolean — true if quality_score >= 70",
  "flags_count": "integer — number of quality issues detected",
  "data_hash": "keccak256 of the input payload array",
  "checked_at": "ISO 8601 timestamp of verification",
  "attested_at": "ISO 8601 timestamp of on-chain write",
  "issuer_agent_id": "DID of the Attestor agent",
  "requester_agent_id": "DID of the agent that requested verification"
}
```

The `data_hash` field creates a cryptographic link between the certificate and the exact payload that was verified. Any modification to the payload after certification will produce a different hash, invalidating the certificate.

---

## 5. Conformance Requirements

### 5.1 Attestor Requirements

An agent claiming to be a CTS-1 compliant Attestor MUST:

- **[CTS-1-A1]** Accept payloads conforming to the schema in Section 3
- **[CTS-1-A2]** Run at minimum the following checks: missing data detection, outlier detection, drift detection, flatline detection
- **[CTS-1-A3]** Produce a quality score in the range [0, 100]
- **[CTS-1-A4]** Apply a pass threshold of 70 — payloads scoring below 70 MUST be marked `passed: false`
- **[CTS-1-A5]** Write a certificate conforming to Section 4 on-chain via a verifiable transaction
- **[CTS-1-A6]** Return the `cert_id` and `tx_hash` to the requester upon delivery
- **[CTS-1-A7]** Index certificates by `source_id` to enable reputation queries

### 5.2 Consumer Requirements

An agent consuming CTS-1 certified data SHOULD:

- **[CTS-1-C1]** Verify the `data_hash` in the certificate matches a fresh hash of the received payload before acting on it
- **[CTS-1-C2]** Check `passed: true` before treating data as verified
- **[CTS-1-C3]** Query the source reputation tier before initiating payment for high-value data

---

## 6. Trust Tiers

The CTS-1 reputation model assigns a trust tier to each `source_id` based on its accumulated certificate history. Tiers are computed dynamically by a compliant Reputation Agent.

| Tier | Minimum Checks | Minimum Pass Rate | Meaning |
|---|---|---|---|
| **Unrated** | 0 | — | No history. Proceed with caution. |
| **Bronze** | 1 | Any | Has been verified at least once |
| **Silver** | 5 | ≥ 75% | Consistent track record |
| **Gold** | 10 | ≥ 90% | Highly reliable, sustained performance |

### 6.1 Trend Signals

In addition to tier, a compliant Reputation Agent SHOULD report a trend signal computed over the most recent 5 certificates vs prior history:

| Trend | Meaning |
|---|---|
| `improving` | Recent pass rate > historical by >5 points |
| `degrading` | Recent pass rate < historical by >5 points |
| `stable` | No significant change |
| `insufficient_data` | Fewer than 6 total certificates |

---

## 7. On-Chain Verification

Any party MAY independently verify a CTS-1 certificate by:

1. Retrieving the certificate `cert_id` from the Attestor agent via `get_delivery()`
2. Recomputing `keccak256` of the original payload
3. Comparing against the `data_hash` field in the certificate
4. Confirming the `tx_hash` exists on Base Mainnet (Chain ID 8453) via a block explorer

This process requires no trusted third party — the blockchain is the source of truth.

---

## 8. Extension Points

CTS-1 v1.0 is intentionally minimal. The following extensions are planned for future versions:

- **CTS-2:** Multi-attestor consensus — require M-of-N independent attestors to agree before a certificate is issued
- **CTS-3:** Cross-chain certificate portability — allow certificates issued on Base to be verified on other EVM chains
- **CTS-4:** Structured data support — extend beyond numeric arrays to JSON objects and text outputs
- **CTS-5:** Staked attestation — require Attestors to stake $CROO as collateral, slashed on provably false certificates

---

## 9. Reference Implementation

The reference implementation of CTS-1 is **Veritas Fides**, built on the CROO Agent Protocol:

| Agent | Role | CTS-1 Function |
|---|---|---|
| TrustGate | Attestor | Runs checks, computes score, orchestrates pipeline |
| SourceAttest | Certificate Writer | Writes on-chain certificate, returns tx_hash |
| ReputationOracle | Reputation Agent | Queries certificate history, returns tier and trend |

Source code: https://github.com/The-Natri/veritas-fides  
License: MIT

---

## 10. Changelog

| Version | Date | Notes |
|---|---|---|
| 1.0.0-draft | June 2025 | Initial draft, reference implementation complete |

---

*CTS-1 is released under CC0 1.0 Universal. Anyone may implement, extend, or build upon this standard without restriction.*