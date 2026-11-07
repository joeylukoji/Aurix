"""Gateway features that need no chain node: /api/txs/observed and CORS."""
from fastapi.testclient import TestClient

from app import crypto
from app.clients import ChainError, GuardianClient
from app.config import Settings
from app.main import create_app

from .conftest import guardian_transport

GATEWAY = crypto.Key("aurix-dev-gateway").address
OTHER = crypto.Key("attestor-2").address


class TxChain:
    """Minimal in-memory chain: the gateway features below only read status, accounts and txs."""

    def __init__(self):
        self.known = {}

    def status(self):
        return {"chain_id": "aurix-test", "height": 10, "denom": "uaur"}

    def account(self, addr):
        return {"address": addr, "account_number": 1, "sequence": 0, "exists": True, "balance": 10**9}

    def tx(self, h):
        if h not in self.known:
            raise ChainError(404, "tx not found")
        return self.known[h]


def test_observed_tx_recorded_from_chain_data(tmp_path, monkeypatch):
    chain = TxChain()
    g = guardian_transport()
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "o.db"))
    c = TestClient(create_app(s, chain=chain, guardian=GuardianClient("http://g", transport=g)))
    h = "AB" * 32
    assert c.post("/api/txs/observed", json={"txhash": "zz"}).status_code == 400
    assert c.post("/api/txs/observed", json={"txhash": h}).status_code == 404
    chain.known[h] = {"hash": h, "height": 5, "type": "MsgSend", "from": GATEWAY, "to": OTHER, "amount": 3_000_000,
                      "code": 0}
    r = c.post("/api/txs/observed", json={"txhash": h.lower(), "amount": 1, "from": "forged"}).json()
    assert r["recorded"] is True and r["tx"]["from"] == GATEWAY
    import json as _json
    body = _json.loads(g.calls[-1].content)
    assert body["sender"] == GATEWAY and body["amount"] == 3.0  # from chain data, not the client
    assert c.post("/api/txs/observed", json={"txhash": h}).json()["recorded"] is False  # idempotent
    assert len(g.calls) == 1


def test_cors(tmp_path, monkeypatch):
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "c.db"),
                 cors_origins=["https://wallet.example"])
    c = TestClient(create_app(s, chain=TxChain(), guardian=GuardianClient("http://g", transport=guardian_transport())))
    r = c.options("/api/health", headers={"Origin": "https://wallet.example", "Access-Control-Request-Method": "POST"})
    assert r.headers["access-control-allow-origin"] == "https://wallet.example"
    r = c.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert Settings().cors_origins == []
    monkeypatch.delenv("CORS_ORIGINS")
    assert Settings().cors_origins == ["*"]


class ProofChain(TxChain):
    def anchor_threshold(self):
        return 2

    def proof_raw(self, h):
        if h != "ab" * 32:
            raise ChainError(404, "proof not found")
        return {"hash": h, "external_ref": "paypal:O", "capture_ref": "C", "height": "7", "submitter": GATEWAY,
                "attestations": [GATEWAY], "status": "PROOF_STATUS_PENDING"}


def test_proof_endpoint(tmp_path):
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "p.db"))
    c = TestClient(create_app(s, chain=ProofChain(), guardian=GuardianClient("http://g", transport=guardian_transport())))
    assert c.get("/api/proofs/nothex").status_code == 400
    assert c.get("/api/proofs/" + "0" * 64).status_code == 404
    p = c.get("/api/proofs/" + "AB" * 32).json()
    assert p["status"] == "PENDING" and p["threshold"] == 2 and p["attestations"] == [GATEWAY]
