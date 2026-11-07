import hashlib
import secrets

import httpx
import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.clients import ChainClient, GuardianClient, PayPalClient
from app.config import Settings
from app.main import create_app

from .conftest import guardian_transport


def make(chain_url, tmp_path, paypal=None, guardian_fail=False):
    s = Settings(chain_url=chain_url, database_path=str(tmp_path / "t.db"))
    g = GuardianClient("http://guardian", transport=guardian_transport(fail=guardian_fail))
    return TestClient(create_app(s, chain=ChainClient(chain_url), guardian=g, paypal=paypal))


def new_key():
    k = crypto.key_from_seed(secrets.token_hex(16))
    return k, crypto.address_from_pub(crypto.pubkey_bytes(k))


def sign_prepared(c, key, addr, msg):
    p = c.post("/api/txs/prepare", json={"from": addr, "msg": msg}).json()
    sig = key.sign(p["sign_bytes"].encode()).hex()
    return p, {"msg": p["msg"], "from": addr, "pub_key": crypto.pubkey_bytes(key).hex(),
               "nonce": p["nonce"], "fee": p["fee"], "signature": sig}


def test_faucet_transfer_with_advisory_risk(chain_url, tmp_path):
    c = make(chain_url, tmp_path)
    ka, a = new_key()
    _, b = new_key()
    assert c.post(f"/api/wallets/{a}/faucet").json()["code"] == 0
    p, tx = sign_prepared(c, ka, a, {"type": "MsgSend", "to": b, "amount": 5 * crypto.MICRO})
    assert p["risk"]["level"] == "high" and p["advisory_only"]
    # high risk does NOT block: the user decides (invariant 9)
    assert c.post("/api/txs", json=tx).json()["code"] == 0
    assert c.get(f"/api/wallets/{b}").json()["balance"] == 5 * crypto.MICRO
    assert any(t["to"] == b for t in c.get(f"/api/wallets/{a}").json()["history"])


def test_guardian_down_does_not_block(chain_url, tmp_path):
    c = make(chain_url, tmp_path, guardian_fail=True)
    ka, a = new_key()
    _, b = new_key()
    c.post(f"/api/wallets/{a}/faucet")
    p, tx = sign_prepared(c, ka, a, {"type": "MsgSend", "to": b, "amount": 1})
    assert p["risk"] is None and p["risk_status"] == "unavailable"
    assert c.post("/api/txs", json=tx).json()["code"] == 0


def test_replay_rejected(chain_url, tmp_path):
    c = make(chain_url, tmp_path)
    ka, a = new_key()
    _, b = new_key()
    c.post(f"/api/wallets/{a}/faucet")
    _, tx = sign_prepared(c, ka, a, {"type": "MsgSend", "to": b, "amount": 1})
    assert c.post("/api/txs", json=tx).status_code == 200
    assert c.post("/api/txs", json=tx).status_code == 400


def test_escrow_release(chain_url, tmp_path):
    c = make(chain_url, tmp_path)
    ka, a = new_key()
    _, b = new_key()
    c.post(f"/api/wallets/{a}/faucet")
    h = c.get("/api/chain/status").json()["height"]
    _, tx = sign_prepared(c, ka, a, {"type": "MsgCreateEscrow", "to": b, "amount": 3 * crypto.MICRO,
                                     "deadline_height": h + 1000})
    eid = c.post("/api/txs", json=tx).json()["escrow_id"]
    assert c.get(f"/api/escrows/{eid}").json()["status"] == "LOCKED"
    _, tx = sign_prepared(c, ka, a, {"type": "MsgReleaseEscrow", "escrow_id": eid})
    assert c.post("/api/txs", json=tx).json()["code"] == 0
    assert c.get(f"/api/escrows/{eid}").json()["status"] == "RELEASED"
    assert c.get(f"/api/wallets/{b}").json()["balance"] == 3 * crypto.MICRO


def test_paypal_mock_flow_anchors_proof(chain_url, tmp_path):
    c = make(chain_url, tmp_path)  # no credentials -> mock PayPal
    o = c.post("/api/paypal/orders", json={"amount_usd": "25.50"}).json()
    assert o["mock"] and o["risk"]["level"] == "high"
    oid = o["order_id"]
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={}).status_code == 400  # invariant 8
    done = c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).json()
    assert done["status"] == "ANCHORED"
    expected = hashlib.sha256(f"{oid}|{done['capture_id']}".encode()).hexdigest()
    assert done["proof_hash"] == expected
    assert c.get(f"/api/explorer/proofs/{expected}").json()["external_ref"] == f"paypal:{oid}"
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).status_code == 409


def test_bad_amount(chain_url, tmp_path):
    c = make(chain_url, tmp_path)
    for v in ("-1", "abc", "1.234"):
        assert c.post("/api/paypal/orders", json={"amount_usd": v}).status_code == 422


# ---- real PayPal client against a fake PayPal API ----

def fake_paypal(verified: bool):
    state = {}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T"})
        if p == "/v2/checkout/orders":
            return httpx.Response(201, json={"id": "ORDER1", "status": "CREATED",
                                             "links": [{"rel": "approve", "href": "https://x/approve"}]})
        if p.endswith("/capture"):
            state["captured"] = True
            return httpx.Response(201, json={"id": "ORDER1", "status": "COMPLETED", "purchase_units": [
                {"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}]})
        if p == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS" if verified else "FAILURE"})
        return httpx.Response(404)
    return PayPalClient("https://paypal", "id", "secret", "WH", transport=httpx.MockTransport(handler))


EVENT = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED",
         "resource": {"id": "CAP1", "supplementary_data": {"related_ids": {"order_id": "ORDER1"}}}}
HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.paypal.com/cert",
           "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "now"}


@pytest.mark.parametrize("verified", [True, False])
def test_webhook_signature_and_idempotency(chain_url, tmp_path, verified):
    c = make(chain_url, tmp_path, paypal=fake_paypal(verified))
    assert c.post("/api/paypal/orders", json={"amount_usd": "10"}).json()["order_id"] == "ORDER1"
    assert c.post("/api/paypal/orders/ORDER1/approve", json={"confirm": True}).json()["status"] == "CAPTURED"
    r = c.post("/api/paypal/webhook", json=EVENT, headers=HEADERS)
    if not verified:  # invariant 7
        assert r.status_code == 400
        assert c.get("/api/paypal/orders/ORDER1").json()["status"] == "CAPTURED"
        return
    assert r.json()["result"] == "anchored"
    first = c.get("/api/paypal/orders/ORDER1").json()
    # invariant 6: replay is a 200 no-op
    assert c.post("/api/paypal/webhook", json=EVENT, headers=HEADERS).json()["result"] == "duplicate"
    assert c.get("/api/paypal/orders/ORDER1").json()["anchor_tx"] == first["anchor_tx"]


def test_webhook_missing_headers_rejected(chain_url, tmp_path):
    c = make(chain_url, tmp_path, paypal=fake_paypal(True))
    assert c.post("/api/paypal/webhook", json=EVENT).status_code == 400
