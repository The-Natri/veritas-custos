# Contributing to Veritas Custos

Thank you for your interest in Veritas Custos.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/veritas-custos.git`
3. Create a branch: `git checkout -b feature/your-feature-name`

## Development Setup

**Requirements:**
- Python 3.12+
- WSL2 Ubuntu 24.04 (recommended)
- casper-client
- Rust + cargo-odra (for contract changes only)

**Run the server:**
```bash
cd agent_verifier
python3 -u server.py
```

**Test the pipeline:**
```bash
curl -s "http://localhost:8402/verify?account_hash=<account_hash>" \
  -H "X-PAYMENT: dev_payment" | python3 -m json.tool
```

**Run the consuming agent:**
```bash
cd consuming_agent
python3 trust_gate_client.py <account_hash>
```

## Guidelines

- Zero external Python dependencies — stdlib only
- Do not redeploy the smart contract
- Do not commit `.env` files or `keys/secret_key.pem`
- One focused change per pull request

## Reporting Issues

Open a GitHub issue with a clear description and steps to reproduce.

## License

By contributing, you agree your contributions are licensed under the MIT License.
