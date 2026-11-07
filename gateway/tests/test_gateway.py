import base64
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


def make(urls, tmp_path, paypal=None, guardian_fail=False):
    s = Settings(chain_url=urls[0], chain_rest_url=urls[1], database_path=str(tmp_path / "t.db"))
    g = GuardianClient("http://guardian", transport=guardian_transport(fail=guardian_fail))
    return TestClient(create_app(s, chain=ChainClient(*urls), guardian=g, paypal=paypal))


def funded(c):
    k = crypto.Key(secrets.token_hex(16))
    assert c.post(f"/api/wallets/{k.address}/faucet").json()["code"] == 0
    return k


def user_tx(c, key, msg: dict, encoded: bytes):
    """Simulates the browser: prepare -> build + sign locally -> submit."""
    p = c.post("/api/txs/prepare", json={"from": key.address, "msg": msg})
    assert p.status_code == 200, p.text
    p = p.json()
    raw = crypto.build_tx(key, [encoded], p["chain_id"], p["account_number"], p["sequence"])
    return p, c.post("/api/txs", json={"tx_bytes": base64.b64encode(raw).decode()})


def test_faucet_transfer_with_advisory_risk(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    a, b = funded(c), crypto.Key(secrets.token_hex(16))
    p, r = user_tx(c, a, {"type": "MsgSend", "to": b.address, "amount": 5 * crypto.MICRO},
                   crypto.msg_send(a.address, b.address, 5 * crypto.MICRO))
    assert p["risk"]["level"] == "high" and p["advisory_only"]
    assert r.json()["code"] == 0  # high risk does NOT block: the user decides (invariant 9)
    assert c.get(f"/api/wallets/{b.address}").json()["balance"] == 5 * crypto.MICRO
    hist = c.get(f"/api/wallets/{a.address}").json()["history"]
    assert any(t["to"] == b.address and t["amount"] == 5 * crypto.MICRO for t in hist)
    # fees are charged: 100 AUR - 5 AUR - fee
    assert c.get(f"/api/wallets/{a.address}").json()["balance"] == 95 * crypto.MICRO - crypto.FEE


def test_guardian_down_does_not_block(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path, guardian_fail=True)
    a, b = funded(c), crypto.Key("x").address
    p, r = user_tx(c, a, {"type": "MsgSend", "to": b, "amount": 1}, crypto.msg_send(a.address, b, 1))
    assert p["risk"] is None and p["risk_status"] == "unavailable"
    assert r.json()["code"] == 0


def test_replay_rejected(chain_urls, tmp_path):  # invariant 4: sequence = nonce
    c = make(chain_urls, tmp_path)
    a, b = funded(c), crypto.Key("y").address
    acct = c.get(f"/api/wallets/{a.address}").json()
    raw = crypto.build_tx(a, [crypto.msg_send(a.address, b, 1)], c.get("/api/chain/status").json()["chain_id"],
                          acct["account_number"], acct["sequence"])
    body = {"tx_bytes": base64.b64encode(raw).decode()}
    assert c.post("/api/txs", json=body).status_code == 200
    r = c.post("/api/txs", json=body)
    assert r.status_code == 400
    assert "sequence" in r.json()["detail"] or "replay" in r.json()["detail"]


def test_insufficient_funds_rejected(chain_urls, tmp_path):  # invariant 5
    c = make(chain_urls, tmp_path)
    a, b = funded(c), crypto.Key("z").address
    _, r = user_tx(c, a, {"type": "MsgSend", "to": b, "amount": 1000 * crypto.MICRO},
                   crypto.msg_send(a.address, b, 1000 * crypto.MICRO))
    assert r.status_code == 400


def test_escrow_release(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    a, b = funded(c), crypto.Key(secrets.token_hex(8)).address
    h = c.get("/api/chain/status").json()["height"]
    _, r = user_tx(c, a, {"type": "MsgCreateEscrow", "to": b, "amount": 3 * crypto.MICRO, "deadline_height": h + 1000},
                   crypto.msg_create_escrow(a.address, b, 3 * crypto.MICRO, h + 1000))
    eid = r.json()["escrow_id"]
    assert c.get(f"/api/escrows/{eid}").json()["status"] == "LOCKED"
    _, r = user_tx(c, a, {"type": "MsgReleaseEscrow", "escrow_id": eid}, crypto.msg_settle_escrow("Release", a.address, eid))
    assert r.json()["code"] == 0 and r.json()["to"] == b
    assert c.get(f"/api/escrows/{eid}").json()["status"] == "RELEASED"
    assert c.get(f"/api/wallets/{b}").json()["balance"] == 3 * crypto.MICRO
    _, r = user_tx(c, a, {"type": "MsgReleaseEscrow", "escrow_id": eid}, crypto.msg_settle_escrow("Release", a.address, eid))
    assert r.status_code == 400  # invariant 3


def test_escrow_refund_after_deadline(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    a, b = funded(c), crypto.Key(secrets.token_hex(8)).address
    h = c.get("/api/chain/status").json()["height"]
    _, r = user_tx(c, a, {"type": "MsgCreateEscrow", "to": b, "amount": 1000, "deadline_height": h + 3},
                   crypto.msg_create_escrow(a.address, b, 1000, h + 3))
    eid = r.json()["escrow_id"]
    while c.get("/api/chain/status").json()["height"] < h + 3:
        pass
    _, r = user_tx(c, a, {"type": "MsgRefundEscrow", "escrow_id": eid}, crypto.msg_settle_escrow("Refund", a.address, eid))
    assert r.json()["code"] == 0
    assert c.get(f"/api/escrows/{eid}").json()["status"] == "REFUNDED"


def test_explorer(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    funded(c)
    height = c.get("/api/chain/status").json()["height"]
    blocks = c.get("/api/explorer/blocks?limit=25").json()
    assert len(blocks) == min(25, height) and blocks[0]["height"] > blocks[-1]["height"]
    assert any(b["num_txs"] > 0 for b in c.get("/api/explorer/blocks?limit=200").json())
    assert any(t["type"] == "MsgSend" for t in c.get("/api/explorer/txs").json())


def test_paypal_mock_flow_anchors_proof(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)  # no credentials -> mock PayPal
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


def test_bad_inputs(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    for v in ("-1", "abc", "1.234"):
        assert c.post("/api/paypal/orders", json={"amount_usd": v}).status_code == 422
    assert c.post("/api/wallets/cosmos1abc/faucet").status_code == 400
    assert c.post("/api/txs", json={"tx_bytes": "%%%"}).status_code == 400


# ---- real PayPal client against a fake PayPal API ----

def fake_paypal(verified: bool, order_id: str):
    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T"})
        if p == "/v2/checkout/orders":
            return httpx.Response(201, json={"id": order_id, "status": "CREATED",
                                             "links": [{"rel": "approve", "href": "https://x/approve"}]})
        if p.endswith("/capture"):
            return httpx.Response(201, json={"id": order_id, "status": "COMPLETED", "purchase_units": [
                {"payments": {"captures": [{"id": "CAP-" + order_id, "status": "COMPLETED"}]}}]})
        if p == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS" if verified else "FAILURE"})
        return httpx.Response(404)
    return PayPalClient("https://paypal", "id", "secret", "WH", transport=httpx.MockTransport(handler))


HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.paypal.com/cert",
           "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "now"}


def event(order_id):
    return {"id": "WH-" + order_id, "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": "CAP-" + order_id, "supplementary_data": {"related_ids": {"order_id": order_id}}}}


@pytest.mark.parametrize("verified", [True, False])
def test_webhook_signature_and_idempotency(chain_urls, tmp_path, verified):
    oid = "ORDER-" + secrets.token_hex(4)
    c = make(chain_urls, tmp_path, paypal=fake_paypal(verified, oid))
    assert c.post("/api/paypal/orders", json={"amount_usd": "10"}).json()["order_id"] == oid
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).json()["status"] == "CAPTURED"
    r = c.post("/api/paypal/webhook", json=event(oid), headers=HEADERS)
    if not verified:  # invariant 7
        assert r.status_code == 400
        assert c.get(f"/api/paypal/orders/{oid}").json()["status"] == "CAPTURED"
        return
    assert r.json()["result"] == "anchored"
    first = c.get(f"/api/paypal/orders/{oid}").json()
    # invariant 6: replay is a 200 no-op
    assert c.post("/api/paypal/webhook", json=event(oid), headers=HEADERS).json()["result"] == "duplicate"
    assert c.get(f"/api/paypal/orders/{oid}").json()["anchor_tx"] == first["anchor_tx"]


def test_webhook_missing_headers_rejected(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path, paypal=fake_paypal(True, "O"))
    assert c.post("/api/paypal/webhook", json=event("O")).status_code == 400
