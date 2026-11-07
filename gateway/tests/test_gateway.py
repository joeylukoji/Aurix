import base64
import hashlib
import importlib.util
import secrets
import time
from pathlib import Path

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


def test_bad_inputs(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    assert c.post("/api/wallets/cosmos1abc/faucet").status_code == 400
    assert c.post("/api/txs", json={"tx_bytes": "%%%"}).status_code == 400


# ---- PayPal flow against the local PayPal simulator (paypal-sim/) ----

def _load_sim():
    spec = importlib.util.spec_from_file_location("paypal_sim", Path(__file__).resolve().parents[2] / "paypal-sim/app/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SIM = _load_sim()


@pytest.fixture
def paypal_env(chain_urls, tmp_path, monkeypatch):
    """Gateway wired to the simulator through the real PayPalClient; the simulator's
    webhooks are delivered to the gateway under test."""
    pp = PayPalClient(SIM.PUBLIC_URL, SIM.CLIENT_ID, SIM.CLIENT_SECRET, SIM.WEBHOOK_ID)
    pp.http = TestClient(SIM.app, base_url=SIM.PUBLIC_URL)
    c = make(chain_urls, tmp_path, paypal=pp)
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 0)

    class Delivered:
        def __init__(self, r):
            self.status_code, self.text = r.status_code, r.text

    monkeypatch.setattr(SIM.httpx, "post", lambda url, json, headers, timeout:
                        Delivered(c.post("/api/paypal/webhook", json=json, headers=headers)))
    return c, TestClient(SIM.app)


def wait_status(c, oid, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        o = c.get(f"/api/paypal/orders/{oid}").json()
        assert "status" in o, o
        if o["status"] == status:
            return o
        time.sleep(0.1)
    raise AssertionError(f"order {oid} stuck in {o['status']}")


def test_paypal_flow_with_simulator(paypal_env):
    c, sim = paypal_env
    o = c.post("/api/paypal/orders", json={"amount_usd": "25.50"}).json()
    assert o["risk"]["level"] == "high" and o["approve_url"].startswith(SIM.PUBLIC_URL + "/checkoutnow")
    oid = o["order_id"]
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={}).status_code == 400  # invariant 8 (AURIX user)
    r = c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    assert r.status_code == 409 and r.json()["issue"] == "ORDER_NOT_APPROVED"  # buyer has not paid on PayPal
    sim.post(f"/sim/orders/{oid}/approve")  # buyer approves on the PayPal page
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).json()["status"] == "CAPTURED"
    done = wait_status(c, oid, "ANCHORED")  # anchored only after the signed webhook
    expected = hashlib.sha256(f"{oid}|{done['capture_id']}".encode()).hexdigest()
    assert done["proof_hash"] == expected
    assert c.get(f"/api/explorer/proofs/{expected}").json()["external_ref"] == f"paypal:{oid}"
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).status_code == 409

    event_id = next(e["id"] for e in sim.get("/sim/events").json() if e["order_id"] == oid)
    # invariant 6: the identical signed webhook replayed is a 200 no-op
    replay = sim.post(f"/sim/events/{event_id}/resend").json()
    assert replay["status"] == 200 and "duplicate" in replay["response"]
    # invariant 7: tampered payload with the original signature is rejected
    assert sim.post(f"/sim/events/{event_id}/forge").json()["status"] == 400
    assert c.get(f"/api/paypal/orders/{oid}").json()["anchor_tx"] == done["anchor_tx"]


def test_webhook_missing_headers_rejected(paypal_env):
    c, _ = paypal_env
    ev = {"id": "WH-X", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C"}}
    assert c.post("/api/paypal/webhook", json=ev).status_code == 400


def test_paypal_disabled_without_credentials(chain_urls, tmp_path):
    c = make(chain_urls, tmp_path)
    assert c.get("/api/health").json()["paypal_mode"] == "disabled"
    assert c.post("/api/paypal/orders", json={"amount_usd": "1"}).status_code == 503


def test_bad_amounts(paypal_env):
    c, _ = paypal_env
    for v in ("-1", "abc", "1.234"):
        assert c.post("/api/paypal/orders", json={"amount_usd": v}).status_code == 422
