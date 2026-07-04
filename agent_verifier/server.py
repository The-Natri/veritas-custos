"""
Veritas Custos - x402-compliant HTTP server
x402Version: 2 (Casper facilitator requires v2)
Flow: 402 (no payment) -> client signs -> retry with X-PAYMENT -> POST /settle -> 200
No /verify pre-call - Casper facilitator uses settle-only flow.
"""
import http.server
import json
import os
import sys
import urllib.request
import urllib.parse
import base64
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "identity_checker"))

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

def _load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

ENV = _load_env()
PORT = int(ENV.get("SERVER_PORT", "8402"))
FACILITATOR_URL = ENV.get("CASPER_X402_FACILITATOR_URL", "")
CASPER_NETWORK = ENV.get("CASPER_NETWORK", "casper:casper-test")
PAY_TO = ENV.get("PAY_TO_ADDRESS", "")
PRICE_MOTES = ENV.get("PRICE_MOTES", "100000000")
DECISIONS_DIR = os.path.join(os.path.dirname(__file__), "decisions")
DEV_MODE = not FACILITATOR_URL

def build_payment_required(resource_path: str) -> str:
    requirements = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": CASPER_NETWORK,
                "maxAmountRequired": PRICE_MOTES,
                "resource": f"http://localhost:{PORT}{resource_path}",
                "description": "Veritas Custos agent trust verification",
                "mimeType": "application/json",
                "payTo": PAY_TO,
                "maxTimeoutSeconds": 300,
                "asset": "CSPR",
                "extra": {
                    "facilitator": FACILITATOR_URL or "pending",
                    "devMode": DEV_MODE,
                }
            }
        ],
        "error": "Payment required to access Veritas Custos trust verification"
    }
    return base64.b64encode(json.dumps(requirements).encode()).decode()

def settle_payment(payment_header: str, resource_path: str) -> tuple[bool, dict]:
    """
    Live mode: POST to facilitator /settle.
    Dev mode: accept any non-empty X-PAYMENT header, return simulated response.
    Casper facilitator uses settle-only flow (no separate /verify step).
    """
    if DEV_MODE:
        return True, {
            "settled": True,
            "txHash": "dev_mode_no_settlement",
            "network": CASPER_NETWORK,
        }
    try:
        settle_url = FACILITATOR_URL.rstrip("/") + "/settle"
        body = json.dumps({
            "x402Version": 2,
            "paymentPayload": payment_header,
            "paymentRequirements": {
                "scheme": "exact",
                "network": CASPER_NETWORK,
                "maxAmountRequired": PRICE_MOTES,
                "resource": f"http://localhost:{PORT}{resource_path}",
                "payTo": PAY_TO,
                "asset": "CSPR",
            }
        }).encode()
        req = urllib.request.Request(
            settle_url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            success = result.get("settled", False) or result.get("success", False)
            return success, result
    except Exception as e:
        return False, {"settled": False, "error": str(e)}


class VeritasCustosHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {format % args}")

    def send_json(self, status: int, data: dict, extra_headers: dict = None):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "PAYMENT-REQUIRED, PAYMENT-RESPONSE")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: str, content_type: str):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-PAYMENT, Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/ui"):
            ui_path = os.path.join(os.path.dirname(__file__), "ui.html")
            self.send_file(ui_path, "text/html; charset=utf-8")
            return

        if path == "/assets/bg_corridor.png":
            asset_path = os.path.join(os.path.dirname(__file__), "assets", "bg_corridor.png")
            if os.path.exists(asset_path):
                self.send_file(asset_path, "image/png")
            else:
                self.send_json(404, {"error": "Asset not found"})
            return

        if path == "/health":
            self.send_json(200, {
                "status": "ok",
                "service": "Veritas Custos",
                "x402Version": 2,
                "mode": "dev" if DEV_MODE else "live",
                "network": CASPER_NETWORK,
                "facilitator": FACILITATOR_URL or "not_configured",
            })
            return

        if path.startswith("/decisions/"):
            tx_hash = path.split("/decisions/")[-1].strip("/")
            verdict_path = os.path.join(DECISIONS_DIR, f"{tx_hash}.json")
            if os.path.exists(verdict_path):
                with open(verdict_path) as f:
                    self.send_json(200, json.load(f))
            else:
                self.send_json(404, {"error": f"No verdict found for tx_hash: {tx_hash}"})
            return

        if path == "/verify":
            account_hash = params.get("account_hash", [None])[0]
            if not account_hash:
                self.send_json(400, {"error": "Missing required parameter: account_hash"})
                return

            payment_header = self.headers.get("X-PAYMENT", "").strip()

            if not payment_header:
                pr_value = build_payment_required(self.path)
                self.send_json(402, {
                    "x402Version": 2,
                    "error": "Payment required",
                    "price": f"{int(PRICE_MOTES) / 1_000_000_000} CSPR",
                    "network": CASPER_NETWORK,
                    "devMode": DEV_MODE,
                }, extra_headers={"PAYMENT-REQUIRED": pr_value})
                return

            # Payment present: settle first (Casper facilitator is settle-only, no /verify step)
            settled, settlement = settle_payment(payment_header, self.path)

            if not settled and not DEV_MODE:
                pr_value = build_payment_required(self.path)
                self.send_json(402, {
                    "x402Version": 2,
                    "error": "Payment settlement failed",
                    "reason": settlement.get("error", "unknown"),
                }, extra_headers={"PAYMENT-REQUIRED": pr_value})
                return

            # Settlement ok (or dev mode) - run pipeline
            try:
                from trustgate import run_pipeline
                result = run_pipeline(account_hash)
                result["x402_settlement"] = settlement
                result["payment_verified"] = True
                result["dev_mode"] = DEV_MODE
                result["x402Version"] = 2

                settlement_b64 = base64.b64encode(
                    json.dumps(settlement).encode()
                ).decode()

                self.send_json(200, result, extra_headers={
                    "PAYMENT-RESPONSE": settlement_b64
                })
            except Exception as e:
                self.send_json(500, {
                    "error": f"Pipeline error: {type(e).__name__}: {str(e)[:300]}"
                })
            return

        self.send_json(404, {"error": f"Unknown endpoint: {path}"})


if __name__ == "__main__":
    print(f"Veritas Custos x402 Server")
    print(f"  x402Version : 2")
    print(f"  Mode        : {'DEV (no facilitator verification)' if DEV_MODE else 'LIVE'}")
    print(f"  Network     : {CASPER_NETWORK}")
    print(f"  Port        : {PORT}")
    print(f"  Facilitator : {FACILITATOR_URL or 'not configured'}")
    print(f"  Endpoints   :")
    print(f"    GET  /           -> UI")
    print(f"    GET  /health     -> status")
    print(f"    GET  /verify     -> x402 verification (requires X-PAYMENT)")
    print(f"    GET  /decisions/ -> verdict lookup by tx_hash")
    print()
    server = http.server.HTTPServer(("0.0.0.0", PORT), VeritasCustosHandler)
    print(f"Listening on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
