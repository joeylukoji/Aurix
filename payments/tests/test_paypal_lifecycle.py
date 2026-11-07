"""Real-PayPal cases beyond the happy path: declined payment method, capture denied afterwards,
merchant refund (incl. after anchoring), and idempotency of those webhooks."""
import time

from .conftest import SIM
from .test_attestation import FakeChain, gateway, pay


def wait(c, oid, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        o = c.get(f"/api/paypal/orders/{oid}").json()
        if o["status"] == status:
            return o
        time.sleep(0.05)
    raise AssertionError(f"{oid} is {o['status']}, expected {status}")


def test_declined_payment_method_is_retryable(tmp_path, monkeypatch):
    c, sim = gateway(tmp_path, monkeypatch, FakeChain())
    monkeypatch.setattr(SIM, "DECLINE_ABOVE", 50.0)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "99.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    r = c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    assert r.status_code == 402 and r.json()["issue"] == "INSTRUMENT_DECLINED"
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "SCORED" and o["last_error"] == "payment method declined"  # still retryable
    assert o["proof_hash"] is None  # nothing anchored


def test_capture_denied_after_capture(tmp_path, monkeypatch):
    c, sim = gateway(tmp_path, monkeypatch, FakeChain())
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 3600)  # hold the COMPLETED webhook back
    oid = c.post("/api/paypal/orders", json={"amount_usd": "10.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    o = c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).json()
    assert o["status"] == "CAPTURED"
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 0)
    sim.post(f"/sim/captures/{o['capture_id']}/deny")
    o = wait(c, oid, "DENIED")
    assert o["proof_hash"] is None and "denied" in o["last_error"]


def test_refund_after_anchoring_and_idempotency(tmp_path, monkeypatch):
    c, sim = gateway(tmp_path, monkeypatch, FakeChain())
    oid = pay(c, sim)
    anchored = wait(c, oid, "ANCHORED")
    assert c.post(f"/api/paypal/orders/{oid}/refund", json={}).status_code == 400  # explicit confirmation
    r = c.post(f"/api/paypal/orders/{oid}/refund", json={"confirm": True}).json()
    assert r["refund_status"] == "COMPLETED"
    o = wait(c, oid, "REFUNDED")
    # the on-chain proof of the original capture is kept; the refund is recorded off-chain
    assert o["proof_hash"] == anchored["proof_hash"] and o["refund_id"] == r["refund_id"]
    assert c.post(f"/api/paypal/orders/{oid}/refund", json={"confirm": True}).status_code == 409
    ev = next(e for e in SIM.EVENTS.values() if e["event"]["event_type"] == "PAYMENT.CAPTURE.REFUNDED"
              and e["event"]["resource"]["id"] == r["refund_id"])
    assert c.post("/api/paypal/webhook", json=ev["event"], headers=ev["headers"]).json()["result"] == "duplicate"


def test_refund_requires_a_capture(tmp_path, monkeypatch):
    c, _ = gateway(tmp_path, monkeypatch, FakeChain())
    oid = c.post("/api/paypal/orders", json={"amount_usd": "3.00"}).json()["order_id"]
    assert c.post(f"/api/paypal/orders/{oid}/refund", json={"confirm": True}).status_code == 409


def test_completed_after_denied_is_ignored(tmp_path, monkeypatch):
    c, sim = gateway(tmp_path, monkeypatch, FakeChain())
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 3600)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "4.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    cap = c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).json()["capture_id"]
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 0)
    sim.post(f"/sim/captures/{cap}/deny")
    wait(c, oid, "DENIED")
    completed = next(e for e in SIM.EVENTS.values() if e["event"]["event_type"] == "PAYMENT.CAPTURE.COMPLETED"
                     and e["event"]["resource"]["id"] == cap)
    r = c.post("/api/paypal/webhook", json=completed["event"], headers=completed["headers"]).json()
    assert r["result"] == "ignored" and c.get(f"/api/paypal/orders/{oid}").json()["status"] == "DENIED"
