"""AURIX demo carrier, inspector and customs — SIMULATED organisations, for demos and tests only.

It stands for an organisation independent of PayPal and of AURIX: it signs its own delivery events with its
own Ed25519 key and sends them to the AURIX escrow (payments /api/escrow/evidence). Every attestation says
source_type "simulated": it must never be presented as a real carrier.

  POST /ship/{order_id}       shipment.shipped   (informational)
  POST /deliver/{order_id}    shipment.delivered (the evidence that releases an escrow)
       ?when=<ISO time>       backdate/postdate the event (deadline scenarios)
       ?forge=true            sign with a key AURIX does not know (must be refused)
  POST /inspect/{order_id}    inspection.passed  signed by the demo inspection company (its own key)
  POST /customs/{order_id}    customs.cleared    signed by the demo customs (its own key)
  GET  /key                   this carrier's public key entry (for the AURIX trust registry)
  GET  /keys                  the three demo sources' key entries
  GET  /health
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aurix_verify import crypto
from aurix_verify.attestation import make_attestation

PROVIDER = "aurix_demo_delivery"
INSPECTOR, CUSTOMS = "aurix_demo_inspection", "aurix_demo_customs"
PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://localhost:8085").rstrip("/")
KEY_ID = os.environ.get("DELIVERY_SIM_KEY_ID", "aurix-demo-delivery-dev-1")


def _load_signer() -> crypto.Signer:
    path = os.environ.get("DELIVERY_SIM_KEY_FILE", "").strip()
    if path:
        with open(path) as f:
            return crypto.Signer.from_seed(KEY_ID, bytes.fromhex(f.read().strip()))
    if os.environ.get("AURIX_ENV", "").lower() == "production":
        raise SystemExit("DELIVERY_SIM_KEY_FILE is required when AURIX_ENV=production")
    # Same dev seed as payments' built-in demo carrier entry (aurix_payments.escrow.DEV_CARRIER).
    return crypto.Signer.from_seed(KEY_ID, hashlib.sha256(os.environ.get("DELIVERY_SIM_SEED",
                                                                         "aurix-dev-delivery").encode()).digest())


SIGNER = _load_signer()
# Same dev seeds as payments' built-in entries (aurix_payments.escrow_keys.DEV_INSPECTOR / DEV_CUSTOMS).
OTHERS = {INSPECTOR: crypto.Signer.from_seed("aurix-demo-inspection-dev-1",
                                             hashlib.sha256(b"aurix-dev-inspection").digest()),
          CUSTOMS: crypto.Signer.from_seed("aurix-demo-customs-dev-1", hashlib.sha256(b"aurix-dev-customs").digest())}
OWNERS = {PROVIDER: "AURIX demo carrier (simulated)", INSPECTOR: "AURIX demo inspection company (simulated)",
          CUSTOMS: "AURIX demo customs (simulated)"}
app = FastAPI(title="AURIX demo carrier (SIMULATED)", version="0.2.0")
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
if _origins:  # the web wallet calls the demo carrier directly (demo buttons)
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["*"])
http = httpx.Client(timeout=15)


def now() -> str:
    return crypto.utcnow()


def attestation(order_id: str, etype: str, status: str, when: str, tracking: str, provider: str = PROVIDER) -> dict:
    eid = f"{etype}:{order_id}:{tracking}"
    return make_attestation(attestation_id="dlv_" + hashlib.sha256(eid.encode()).hexdigest()[:20],
                            operation_id=order_id, provider=provider, provider_event_id=eid, event_type=etype,
                            provider_status=status, source_type="simulated", environment="demo",
                            verification_method="ed25519_source_key", refs={"tracking": tracking},
                            source_event_time=when, attested_at=now())


def send(order_id: str, etype: str, status: str, when: str | None, forge: bool, provider: str = PROVIDER) -> dict:
    if when:
        try:
            datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "when must be an ISO 8601 time")
    tracking = "TRK" + hashlib.sha256(order_id.encode()).hexdigest()[:10].upper()
    att = attestation(order_id, etype, status, when or now(), tracking, provider)
    signer = crypto.Signer.from_seed("unregistered-key", secrets.token_bytes(32)) if forge \
        else OTHERS.get(provider, SIGNER)
    sig = signer.sign(att, att["timestamps"]["attested_at"])
    try:
        r = http.post(f"{PAYMENTS_URL}/api/escrow/evidence", json={"attestation": att, "signature": sig})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"AURIX payments unreachable: {type(e).__name__}")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"detail": r.text}
    return {"sent": att, "aurix_status": r.status_code, "aurix_response": body, "simulated": True}


@app.post("/ship/{order_id}")
def ship(order_id: str, when: str | None = None):
    return send(order_id, "shipment.shipped", "IN_TRANSIT", when, False)


@app.post("/deliver/{order_id}")
def deliver(order_id: str, when: str | None = None, forge: bool = False):
    return send(order_id, "shipment.delivered", "DELIVERED", when, forge)


@app.post("/inspect/{order_id}")
def inspect(order_id: str, when: str | None = None, forge: bool = False):
    return send(order_id, "inspection.passed", "PASSED", when, forge, INSPECTOR)


@app.post("/customs/{order_id}")
def customs(order_id: str, when: str | None = None, forge: bool = False):
    return send(order_id, "customs.cleared", "CLEARED", when, forge, CUSTOMS)


def _entry(provider: str, signer: crypto.Signer) -> dict:
    return {"key_id": signer.key_id, "alg": crypto.ALG, "public_key": signer.public_key,
            "role": f"source:{provider}", "owner": OWNERS[provider], "source_type": "simulated",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": None, "revoked_at": None, "revocation_reason": None}


@app.get("/key")
def key():
    return _entry(PROVIDER, SIGNER)


@app.get("/keys")
def keys():
    return {"keys": [_entry(PROVIDER, SIGNER)] + [_entry(p, s) for p, s in OTHERS.items()]}


@app.get("/health")
def health():
    return {"status": "ok", "provider": PROVIDER, "simulated": True, "key_id": SIGNER.key_id,
            "fingerprint": crypto.fingerprint(SIGNER.public_key)}
