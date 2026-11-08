"""Evidence-released USD escrow (MVP 0.2): PayPal authorize -> signed evidence -> capture or void,
then the proof package is re-verified offline by aurix-verify (which replays the same rule)."""
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from aurix_common.clients import GuardianClient, PayPalClient
from aurix_payments import escrow as E
from aurix_payments.config import Settings
from aurix_payments.main import create_app
from aurix_verify import crypto
from aurix_verify.package import read_zip, verify_package

from .conftest import SIM
from .test_attestation import FakeChain, guardian_transport


class Clock:
    def __init__(self):
        self.t = datetime.now(timezone.utc).replace(microsecond=0)

    def __call__(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, **kw):
        self.t += timedelta(**kw)


def setup(tmp_path, monkeypatch, threshold=1):
    clock = Clock()
    pp = PayPalClient(SIM.PUBLIC_URL, SIM.CLIENT_ID, SIM.CLIENT_SECRET, SIM.WEBHOOK_ID)
    pp.http = TestClient(SIM.app, base_url=SIM.PUBLIC_URL)
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "e.db"),
                 attest_poll_interval=0, paypal_env="simulator")
    c = TestClient(create_app(s, chain=FakeChain(threshold=threshold), paypal=pp, escrow_clock=clock,
                              guardian=GuardianClient("http://g", transport=guardian_transport())))
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 0)

    class Delivered:
        def __init__(self, r):
            self.status_code, self.text = r.status_code, r.text

    monkeypatch.setattr(SIM.httpx, "post", lambda url, json, headers, timeout:
                        Delivered(c.post("/api/paypal/webhook", json=json, headers=headers)))
    return c, TestClient(SIM.app), clock


def authorized_order(c, sim, amount="120.00", **extra):
    r = c.post("/api/escrow/orders", json={"amount_usd": amount, "deadline_days": 7} | extra)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert r.json()["terms"]["rule"] == "delivery-attested/1"
    sim.post(f"/sim/orders/{oid}/approve")
    assert c.post(f"/api/escrow/orders/{oid}/authorize", json={"confirm": False}).status_code == 400
    o = c.post(f"/api/escrow/orders/{oid}/authorize", json={"confirm": True}).json()
    assert o["status"] == "AUTHORIZED" and o["authorization_id"]
    return oid


def carrier_attestation(oid, when, signer=E.DEV_CARRIER, provider=E.DEMO_CARRIER, n=1, etype="shipment.delivered"):
    a = {"schema": "aurix.financial-attestation", "schema_version": "1.0", "attestation_id": f"dlv_{oid}_{n}",
         "operation_id": oid,
         "source": {"provider": provider, "source_type": "simulated", "environment": "demo",
                    "provider_event_id": f"DLV-{oid}-{n}", "provider_reference": {"order_id": oid,
                                                                                  "tracking": f"TRK{n}"},
                    "verification_method": "ed25519_source_key", "verification_status": "verified"},
         "event": {"type": etype, "provider_status": "DELIVERED"},
         "timestamps": {"source_event_time": when, "attested_at": when}}
    return {"attestation": a, "signature": signer.sign(a, when)}


def arbiter_attestation(oid, when, action, n=1):
    a = carrier_attestation(oid, when, E.DEV_ARBITER, provider="arbiter", n=n, etype="arbiter.decision")["attestation"]
    a["event"] = {"type": "arbiter.decision", "action": action}
    a["attestation_id"], a["source"]["provider_event_id"] = f"arb_{oid}_{n}", f"ARB-{oid}-{n}"
    a["source"]["source_type"] = "real"
    return {"attestation": a, "signature": E.DEV_ARBITER.sign(a, when)}


def wait(c, oid, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        c.app.state.refresh_pending()
        o = c.get(f"/api/escrow/orders/{oid}").json()
        if o["status"] == status:
            return o
        time.sleep(0.05)
    raise AssertionError(f"{oid} is {o['status']}, expected {status}")


def verify(c, oid):
    zipped = c.get(f"/api/escrow/orders/{oid}/proof")
    assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"
    keys = c.get("/api/escrow/keys").json()
    return verify_package(read_zip(zipped.content), keys)


def test_delivery_evidence_releases_the_payment(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)
    o = c.post(f"/api/escrow/orders/{oid}/evaluate").json()
    assert o["decision"] == "WAIT" and not o["final"]
    clock.advance(days=2)
    r = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock())).json()
    assert r["result"] == "stored" and r["evaluation"]["decision"] == "RELEASE"
    o = wait(c, oid, "ANCHORED")  # capture -> signed webhook -> k-of-n anchoring (existing path)
    assert o["decision"]["decision"] == "RELEASE" and o["capture_id"]
    c.app.state.refresh_pending()  # records the on-chain proof as evidence
    types = [t["type"] for t in c.get(f"/api/escrow/orders/{oid}").json()["timeline"]]
    assert types.count("payment.authorized") == 1  # API response and webhook deduplicated
    assert {"payment.authorized", "shipment.delivered", "payment.captured", "payment.anchored"} <= set(types)
    rep = verify(c, oid)
    assert rep["result"] == "VALID", rep["errors"]
    assert rep["decision"]["recorded"] == rep["decision"]["replayed"] == "RELEASE"
    srcs = {s["provider"]: s["type"] for s in rep["sources"]}
    assert srcs == {"paypal": "simulated", "aurix_demo_delivery": "simulated", "aurix_chain": "real"}
    # a second delivery report changes nothing: the decision is final
    again = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock(), n=2)).json()
    assert again["evaluation"]["final"] is True


def test_no_evidence_before_deadline_voids_the_authorization(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)
    clock.advance(days=6)
    c.app.state.refresh_pending()
    assert c.get(f"/api/escrow/orders/{oid}").json()["status"] == "AUTHORIZED"
    clock.advance(days=1, seconds=1)
    o = wait(c, oid, "VOIDED")
    auth = SIM.AUTHORIZATIONS[o["authorization_id"]]
    assert auth["status"] == "VOIDED"  # the buyer was never charged
    assert o["capture_id"] is None
    rep = verify(c, oid)
    assert rep["result"] == "VALID", rep["errors"]
    assert rep["decision"]["replayed"] == "VOID"
    # a delivery reported after the void cannot resurrect the payment
    late = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock())).json()
    assert late["evaluation"]["decision"] == "VOID" and late["evaluation"]["final"]
    assert verify(c, oid)["result"] == "VALID"


def test_forged_or_misattributed_evidence_is_refused(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)
    intruder = crypto.Signer.from_seed("intruder", b"x" * 32)
    r = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock(), signer=intruder))
    assert r.status_code == 422 and "unknown_key" in r.text
    tampered = carrier_attestation(oid, clock())
    tampered["attestation"]["operation_id"] = "OTHER"
    assert c.post("/api/escrow/evidence", json=tampered).status_code == 422
    wrong_role = carrier_attestation(oid, clock(), signer=E.DEV_ARBITER)  # arbiter key posing as carrier
    assert c.post("/api/escrow/evidence", json=wrong_role).status_code == 422
    assert c.get(f"/api/escrow/orders/{oid}").json()["status"] == "AUTHORIZED"


def test_arbiter_hold_then_void(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim, arbiter_key_id=E.DEV_ARBITER.key_id)
    clock.advance(hours=1)
    assert c.post("/api/escrow/evidence", json=arbiter_attestation(oid, clock(), "HOLD")).json()[
        "evaluation"]["decision"] == "HOLD"
    clock.advance(hours=1)
    r = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock())).json()
    assert r["evaluation"]["decision"] == "HOLD"  # delivery attested, but the dispute blocks the release
    clock.advance(hours=1)
    r = c.post("/api/escrow/evidence", json=arbiter_attestation(oid, clock(), "VOID", n=2)).json()
    assert r["evaluation"]["decision"] == "VOID"
    wait(c, oid, "VOIDED")
    assert verify(c, oid)["result"] == "VALID"


def test_arbiter_of_another_order_is_refused(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)  # no arbiter designated
    r = c.post("/api/escrow/evidence", json=arbiter_attestation(oid, clock(), "RELEASE"))
    assert r.status_code == 403


def test_expired_authorization_fails_cleanly(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)
    sim.post(f"/sim/authorizations/{c.get(f'/api/escrow/orders/{oid}').json()['authorization_id']}/expire")
    clock.advance(hours=1)
    c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock()))
    o = c.get(f"/api/escrow/orders/{oid}").json()
    assert o["status"] == "AUTH_FAILED" and "AUTHORIZATION_EXPIRED" in o["last_error"]
    assert o["capture_id"] is None  # never presented as paid


def test_input_validation(tmp_path, monkeypatch):
    c, sim, _ = setup(tmp_path, monkeypatch)
    assert c.post("/api/escrow/orders", json={"amount_usd": "10", "deadline_days": 30}).status_code == 422
    assert c.post("/api/escrow/orders", json={"amount_usd": "10.001"}).status_code == 422
    assert c.post("/api/escrow/orders", json={"amount_usd": "10", "carrier": "dhl"}).status_code == 422
    assert c.post("/api/escrow/orders", json={"amount_usd": "10", "arbiter_key_id": "nobody"}).status_code == 422
    oid = c.post("/api/escrow/orders", json={"amount_usd": "10.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    # the pay-now endpoint must not capture a conditional order
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).status_code == 409
    assert c.get("/api/escrow/orders/UNKNOWN").status_code == 404


def test_tampered_export_is_rejected(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = authorized_order(c, sim)
    clock.advance(days=1)
    c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock()))
    files = read_zip(c.get(f"/api/escrow/orders/{oid}/proof").content)
    path = next(p for p in files if p.startswith("attestations/") and b"payment.authorized" in files[p])
    files[path] = files[path].replace(b'"120.00"', b'"1.00"')
    assert verify_package(files, c.get("/api/escrow/keys").json())["result"] == "INVALID"


def test_demo_deadline_in_minutes_voids_live(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    r = c.post("/api/escrow/orders", json={"amount_usd": "15.00", "deadline_minutes": 2}).json()
    assert r["terms"]["deadline"] > r["terms"]["created_at"]
    oid = r["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    c.post(f"/api/escrow/orders/{oid}/authorize", json={"confirm": True})
    clock.advance(minutes=2, seconds=1)
    assert wait(c, oid, "VOIDED")["decision"]["decision"] == "VOID"
    assert c.post("/api/escrow/orders", json={"amount_usd": "1.00", "deadline_minutes": 0}).status_code == 422
