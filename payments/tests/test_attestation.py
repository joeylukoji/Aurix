"""k-of-n anchoring: the payments service is the first attestor; orders are ANCHORED only once the
chain proof is CONFIRMED. Uses an in-memory fake chain so thresholds > 1 can be exercised."""
import hashlib
import re
import sqlite3
import time

from fastapi.testclient import TestClient

from aurix_common import crypto, proto
from aurix_common.clients import ChainError, GuardianClient, PayPalClient, normalize_proof
from aurix_common.testing import guardian_transport
from aurix_payments.config import Settings
from aurix_payments.db import DB
from aurix_payments.main import create_app

from .conftest import SIM

GATEWAY = crypto.Key("aurix-dev-gateway").address
OTHER = crypto.Key("attestor-2").address


class FakeChain:
    def __init__(self, threshold=1, legacy=False):
        self.threshold, self.legacy = threshold, legacy
        self.proofs, self.txs = {}, []

    def status(self):
        return {"chain_id": "aurix-test", "height": 10, "denom": "uaur"}

    def account(self, addr):
        return {"address": addr, "account_number": 1, "sequence": len(self.txs), "exists": True, "balance": 10**9}

    def anchor_threshold(self):
        return 1 if self.legacy else self.threshold  # legacy: params without anchor_threshold

    def broadcast(self, tx: bytes):
        self.txs.append(tx)
        h = re.search(rb"[0-9a-f]{64}", tx).group().decode()
        if self.legacy:
            self.proofs[h] = {"hash": h, "external_ref": "x", "height": "11", "submitter": GATEWAY}
        else:
            self.proofs[h] = {"hash": h, "external_ref": "x", "capture_ref": "c", "height": "11", "submitter": GATEWAY,
                              "attestations": [GATEWAY],
                              "status": "PROOF_STATUS_CONFIRMED" if self.threshold == 1 else "PROOF_STATUS_PENDING"}
        return {"hash": "AB" * 32, "height": 11, "code": 0}

    def attest(self, h, who):
        p = self.proofs[h]
        p["attestations"].append(who)
        if len(p["attestations"]) >= self.threshold:
            p["status"] = "PROOF_STATUS_CONFIRMED"

    def proof_raw(self, h):
        if h not in self.proofs:
            raise ChainError(404, "proof not found")
        return self.proofs[h]


def gateway(tmp_path, monkeypatch, chain):
    pp = PayPalClient(SIM.PUBLIC_URL, SIM.CLIENT_ID, SIM.CLIENT_SECRET, SIM.WEBHOOK_ID)
    pp.http = TestClient(SIM.app, base_url=SIM.PUBLIC_URL)
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "a.db"),
                 attest_poll_interval=0)
    c = TestClient(create_app(s, chain=chain, guardian=GuardianClient("http://g", transport=guardian_transport()),
                              paypal=pp))
    monkeypatch.setattr(SIM, "WEBHOOK_DELAY", 0)

    class Delivered:
        def __init__(self, r):
            self.status_code, self.text = r.status_code, r.text

    monkeypatch.setattr(SIM.httpx, "post", lambda url, json, headers, timeout:
                        Delivered(c.post("/api/paypal/webhook", json=json, headers=headers)))
    return c, TestClient(SIM.app)


def chain_proof(c, h: str) -> dict:
    """The normalised chain proof (the gateway serves it as GET /api/proofs/{hash})."""
    chain = c.app.state.chain
    return normalize_proof(chain.proof_raw(h), chain.anchor_threshold())


def pay(c, sim):
    oid = c.post("/api/paypal/orders", json={"amount_usd": "10.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    assert c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True}).status_code == 200
    end = time.time() + 10  # the simulator delivers the signed webhook asynchronously
    while c.get(f"/api/paypal/orders/{oid}").json()["status"] not in ("ATTESTING", "ANCHORED"):
        assert time.time() < end, "webhook never processed"
        time.sleep(0.05)
    return oid


def test_threshold_1_anchors_directly(tmp_path, monkeypatch):
    chain = FakeChain(threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = pay(c, sim)
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "ANCHORED"
    assert o["attestations"] == {"count": 1, "threshold": 1, "signers": [GATEWAY]}
    assert o["proof_hash"] == hashlib.sha256(f"{oid}|{o['capture_id']}".encode()).hexdigest()
    cap = o["capture_id"].encode()  # capture_ref (field 4) is in the signed MsgAnchorProof
    assert b"\x22" + bytes([len(cap)]) + cap in chain.txs[-1]


def test_threshold_2_attesting_then_anchored(tmp_path, monkeypatch):
    chain = FakeChain(threshold=2)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = pay(c, sim)
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "ATTESTING"
    assert o["attestations"] == {"count": 1, "threshold": 2, "signers": [GATEWAY]}
    p = chain_proof(c, o['proof_hash'])
    assert p["status"] == "PENDING" and p["threshold"] == 2 and p["attestations"] == [GATEWAY]
    assert len(chain.txs) == 1
    chain.attest(o["proof_hash"], OTHER)
    o = c.get(f"/api/paypal/orders/{oid}").json()  # lazy refresh on read
    assert o["status"] == "ANCHORED"
    assert o["attestations"] == {"count": 2, "threshold": 2, "signers": [GATEWAY, OTHER]}
    assert c.get("/api/paypal/orders").json()[0]["attestations"]["count"] == 2
    assert chain_proof(c, o['proof_hash'])["status"] == "CONFIRMED"


def test_poller_refresh(tmp_path, monkeypatch):
    chain = FakeChain(threshold=2)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = pay(c, sim)
    h = c.get(f"/api/paypal/orders/{oid}").json()["proof_hash"]
    chain.attest(h, OTHER)
    c.app.state.refresh_pending()
    assert c.app.state.db.one("SELECT status FROM paypal_orders WHERE order_id=?", oid)["status"] == "ANCHORED"


def test_legacy_chain_proof_without_status(tmp_path, monkeypatch):
    c, sim = gateway(tmp_path, monkeypatch, FakeChain(legacy=True))
    oid = pay(c, sim)
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "ANCHORED" and o["attestations"]["count"] == 1
    p = chain_proof(c, o['proof_hash'])
    assert p["status"] == "CONFIRMED" and p["capture_ref"] == "" and p["threshold"] == 1


def test_normalize_proof():
    p = normalize_proof({"hash": "h", "external_ref": "paypal:O", "capture_ref": "C", "status": "PROOF_STATUS_PENDING",
                         "attestations": ["a"], "height": "5", "submitter": "a", "first_seen_height": "5",
                         "confirmed_height": "0"}, 2)
    assert {k: p[k] for k in ("hash", "external_ref", "capture_ref", "status", "attestations", "threshold")} == \
        {"hash": "h", "external_ref": "paypal:O", "capture_ref": "C", "status": "PENDING", "attestations": ["a"],
         "threshold": 2}


def test_msg_anchor_proof_encoding():
    inner = proto.msg_anchor_proof("aur1x", "ab", "paypal:O", "CAP9")
    assert inner == b"\x0a\x05aur1x" + b"\x12\x02ab" + b"\x1a\x08paypal:O" + b"\x22\x04CAP9"
    assert proto.msg_anchor_proof("aur1x", "ab", "paypal:O") == b"\x0a\x05aur1x\x12\x02ab\x1a\x08paypal:O"
    assert crypto.msg_anchor_proof("aur1x", "ab", "paypal:O", "CAP9") == \
        proto.any_("/aurix.smartmoney.v1.MsgAnchorProof", inner)


def test_db_migrates_old_schema(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE paypal_orders (order_id TEXT PRIMARY KEY, amount_usd TEXT NOT NULL, payer TEXT, "
                "description TEXT, status TEXT NOT NULL, capture_id TEXT, proof_hash TEXT, anchor_tx TEXT, "
                "anchor_height INTEGER, risk_score REAL, risk_level TEXT, simulated INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT, updated_at TEXT)")
    con.execute("INSERT INTO paypal_orders(order_id, amount_usd, status) VALUES ('O1','1.00','ANCHORED')")
    con.commit()
    con.close()
    row = DB(str(path)).one("SELECT * FROM paypal_orders WHERE order_id='O1'")
    assert "attestations" in row and "attest_threshold" in row and "capture_amount" in row
    DB(str(path))  # idempotent


def test_msg_anchor_proof_amount_currency_encoding():
    inner = proto.msg_anchor_proof("aur1x", "ab", "paypal:O", "CAP9", "12.50", "USD")
    assert inner == (b"\x0a\x05aur1x" + b"\x12\x02ab" + b"\x1a\x08paypal:O" + b"\x22\x04CAP9"
                     + b"\x2a\x0512.50" + b"\x32\x03USD")
    assert crypto.msg_anchor_proof("aur1x", "ab", "paypal:O", "CAP9", "12.50", "USD") == \
        proto.any_("/aurix.smartmoney.v1.MsgAnchorProof", inner)


def test_anchor_sends_capture_amount_and_currency(tmp_path, monkeypatch):
    chain = FakeChain(threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "12.5"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    end = time.time() + 10
    while c.get(f"/api/paypal/orders/{oid}").json()["status"] != "ANCHORED":
        assert time.time() < end
        time.sleep(0.05)
    assert b"\x2a\x0512.50\x32\x03USD" in chain.txs[-1]  # amount.value / currency_code of the PayPal capture
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert (o["capture_amount"], o["capture_currency"]) == ("12.50", "USD")


class OldChain(FakeChain):
    """A chain predating H1: rejects MsgAnchorProof field 5/6 as unknown."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rejected = 0

    def broadcast(self, tx: bytes):
        if b"\x32\x03USD" in tx:
            self.rejected += 1
            raise ChainError(400, "errUnknownField: unknown field 5 in MsgAnchorProof")
        return super().broadcast(tx)


def test_anchor_retries_without_amount_on_old_chain(tmp_path, monkeypatch):
    chain = OldChain(threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = pay(c, sim)
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "ANCHORED"
    assert chain.rejected == 1 and b"USD" not in chain.txs[-1]
    cap = o["capture_id"].encode()  # capture_ref is kept on the retry
    assert b"\x22" + bytes([len(cap)]) + cap in chain.txs[-1]


def test_retry_uses_stored_capture_amount(tmp_path, monkeypatch):
    chain = FlakyChain(fail=1, threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    sent = _capture_webhook(monkeypatch)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "7.25"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    end = time.time() + 5
    while not sent:
        assert time.time() < end
        time.sleep(0.05)
    c.post("/api/paypal/webhook", json=sent[0][0], headers=sent[0][1])
    c.app.state.refresh_pending()  # poller retry: no capture payload at hand, uses the stored amount
    assert c.get(f"/api/paypal/orders/{oid}").json()["status"] == "ANCHORED"
    assert b"\x2a\x047.25\x32\x03USD" in chain.txs[-1]


class FlakyChain(FakeChain):
    """Rejects the first `fail` anchor broadcasts (node down / mempool full)."""

    def __init__(self, fail=1, **kw):
        super().__init__(**kw)
        self.fail = fail

    def broadcast(self, tx: bytes):
        if self.fail:
            self.fail -= 1
            raise ChainError(502, "node unavailable")
        return super().broadcast(tx)


def _capture_webhook(monkeypatch):
    sent = []
    monkeypatch.setattr(SIM.httpx, "post", lambda url, json, headers, timeout: sent.append((json, headers)) or
                        type("R", (), {"status_code": 200, "text": ""})())
    return sent


def test_failed_anchor_is_retried_on_redelivery(tmp_path, monkeypatch):
    chain = FlakyChain(fail=1, threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    sent = _capture_webhook(monkeypatch)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "5.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    end = time.time() + 5
    while not sent:
        assert time.time() < end
        time.sleep(0.05)
    event, headers = sent[0]
    assert c.post("/api/paypal/webhook", json=event, headers=headers).status_code >= 400
    assert c.get(f"/api/paypal/orders/{oid}").json()["status"] == "CONFIRMED"
    # PayPal redelivers the same event: it must not be dropped as a duplicate
    assert c.post("/api/paypal/webhook", json=event, headers=headers).json()["result"] == "anchored"
    assert c.get(f"/api/paypal/orders/{oid}").json()["status"] == "ANCHORED"
    assert c.post("/api/paypal/webhook", json=event, headers=headers).json()["result"] == "duplicate"


def test_failed_anchor_is_retried_by_poller(tmp_path, monkeypatch):
    chain = FlakyChain(fail=1, threshold=1)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    sent = _capture_webhook(monkeypatch)
    oid = c.post("/api/paypal/orders", json={"amount_usd": "5.00"}).json()["order_id"]
    sim.post(f"/sim/orders/{oid}/approve")
    c.post(f"/api/paypal/orders/{oid}/approve", json={"confirm": True})
    end = time.time() + 5
    while not sent:
        assert time.time() < end
        time.sleep(0.05)
    c.post("/api/paypal/webhook", json=sent[0][0], headers=sent[0][1])
    assert c.get(f"/api/paypal/orders/{oid}").json()["status"] == "CONFIRMED"
    c.app.state.refresh_pending()
    o = c.get(f"/api/paypal/orders/{oid}").json()
    assert o["status"] == "ANCHORED" and len(chain.txs) == 1


def test_retry_does_not_double_attest(tmp_path, monkeypatch):
    """Broadcast went through but its result was lost: the retry sees our attestation on chain."""
    chain = FakeChain(threshold=2)
    c, sim = gateway(tmp_path, monkeypatch, chain)
    oid = pay(c, sim)
    db = c.app.state.db
    db.update_order(oid, status="CONFIRMED", proof_hash=None)
    c.app.state.refresh_pending()
    assert db.one("SELECT status FROM paypal_orders WHERE order_id=?", oid)["status"] == "ATTESTING"
    assert len(chain.txs) == 1
