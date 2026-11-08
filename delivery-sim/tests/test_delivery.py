from fastapi.testclient import TestClient

from aurix_verify import crypto
from app import main


class FakePayments:
    def __init__(self, status=200):
        self.got, self.status = [], status

    def post(self, url, json):
        self.got.append((url, json))
        status = self.status

        class R:
            status_code = status
            headers = {"content-type": "application/json"}

            def json(self):
                return {"result": "stored"}
        return R()


def test_delivery_is_signed_with_the_registered_carrier_key(monkeypatch):
    fake = FakePayments()
    monkeypatch.setattr(main, "http", fake)
    c = TestClient(main.app)
    r = c.post("/deliver/ORDER-1", params={"when": "2026-11-10T08:00:00Z"}).json()
    url, body = fake.got[0]
    assert url.endswith("/api/escrow/evidence") and r["simulated"] is True
    a, s = body["attestation"], body["signature"]
    assert a["source"]["source_type"] == "simulated" and a["event"]["type"] == "shipment.delivered"
    assert a["timestamps"]["source_event_time"] == "2026-11-10T08:00:00Z"
    reg = crypto.KeyRegistry({"keys": [c.get("/key").json()]})
    assert reg.verify(a, s) == crypto.VALID


def test_matches_the_payments_dev_carrier():
    """The dev carrier key here must be the one payments trusts by default."""
    import hashlib
    expected = crypto.Signer.from_seed("aurix-demo-delivery-dev-1", hashlib.sha256(b"aurix-dev-delivery").digest())
    assert main.SIGNER.public_key == expected.public_key


def test_forged_delivery_uses_an_unknown_key(monkeypatch):
    fake = FakePayments(422)
    monkeypatch.setattr(main, "http", fake)
    c = TestClient(main.app)
    r = c.post("/deliver/ORDER-1", params={"forge": True}).json()
    reg = crypto.KeyRegistry({"keys": [c.get("/key").json()]})
    _, body = fake.got[0]
    assert reg.verify(body["attestation"], body["signature"]) == crypto.UNKNOWN_KEY
    assert r["aurix_status"] == 422


def test_bad_time_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "http", FakePayments())
    assert TestClient(main.app).post("/deliver/X", params={"when": "tomorrow"}).status_code == 422
