import json

import pytest
from fastapi.testclient import TestClient

from app.keys import KeyConfigError, address_from_pubkey, load_key
from app.main import HISTORY, RECEIVED, app
from app.verify import N, InvalidAttestation, verify_attestation

c = TestClient(app)
MNEMONIC = "abandon " * 11 + "about"


def score(**kw):
    body = {"context": "aur_transfer", "sender": "aur1alice", "recipient": "aur1bob", "amount": 20,
            "currency": "AUR", "record": False}
    body.update(kw)
    r = c.post("/v1/risk/score", json=body)
    assert r.status_code == 200
    return r.json()


def setup_function():
    HISTORY.clear()
    RECEIVED.clear()


def test_signature_verifies_and_payload_matches_response():
    r = score(amount=12.5, chain_id="aurix-x")
    att = r["attestation"]
    data = verify_attestation(att, expected_pubkey=c.get("/pubkey").json()["pubkey"])
    assert data["score"] == r["risk_score"] and data["decision"] == r["level"]
    assert data["from"] == "aur1alice" and data["to"] == "aur1bob" and data["amount"] == "12.5"
    assert data["denom"] == "AUR" and data["chain_id"] == "aurix-x" and isinstance(data["issued_at"], int)
    assert data["model_version"].startswith(r["model_version"] + "+")
    assert set(r) >= {"risk_score", "level", "reasons", "model_version", "advisory_only"}


def test_canonical_key_order_and_no_spaces():
    p = score()["attestation"]["payload"]
    assert " " not in p
    keys = list(json.loads(p))
    assert keys == sorted(keys) == ["amount", "chain_id", "decision", "denom", "from", "issued_at",
                                   "model_version", "score", "to"]
    assert json.loads(p)["amount"] == "20"


def test_tampered_payload_or_signature_fails():
    att = score()["attestation"]
    bad = dict(att, payload=att["payload"].replace('"aur1bob"', '"aur1eve"'))
    with pytest.raises(InvalidAttestation):
        verify_attestation(bad)
    sig = bytearray.fromhex(att["signature"]); sig[5] ^= 1
    with pytest.raises(InvalidAttestation):
        verify_attestation(dict(att, signature=sig.hex()))
    with pytest.raises(InvalidAttestation):
        verify_attestation(dict(att, guardian="aur1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"))


def test_low_s_and_high_s_rejected():
    for i in range(20):
        att = score(amount=1 + i)["attestation"]
        sig = bytes.fromhex(att["signature"])
        s = int.from_bytes(sig[32:], "big")
        assert len(sig) == 64 and s <= N // 2
    high = sig[:32] + (N - s).to_bytes(32, "big")  # mathematically valid but malleated
    with pytest.raises(InvalidAttestation, match="high-S"):
        verify_attestation(dict(att, signature=high.hex()))


def test_pubkey_endpoint():
    k = c.get("/pubkey").json()
    assert k["address"].startswith("aur1") and len(bytes.fromhex(k["pubkey"])) == 33
    assert address_from_pubkey(bytes.fromhex(k["pubkey"])) == k["address"]
    assert score()["attestation"]["guardian"] == k["address"]


def test_key_sources_and_production_refusal(tmp_path, caplog):
    with pytest.raises(KeyConfigError):
        load_key({"AURIX_ENV": "production", "GUARDIAN_SEED": "x"})
    with pytest.raises(KeyConfigError):
        load_key({"AURIX_ENV": "production"})
    dev = load_key({"GUARDIAN_SEED": "x"})
    assert dev.source == "dev_seed" and "never use on a public network" in caplog.text
    # cosmos test vector: cosmos19rl4cm2hmr8afy4kldpxz3fka4jguq0auqdal4 re-encoded with "aur"
    m = load_key({"AURIX_ENV": "production", "GUARDIAN_MNEMONIC": MNEMONIC})
    assert m.address == address_from_pubkey(m.pubkey)
    from bip_utils import Bech32Decoder
    assert Bech32Decoder.Decode("cosmos", "cosmos19rl4cm2hmr8afy4kldpxz3fka4jguq0auqdal4") == \
        Bech32Decoder.Decode("aur", m.address)
    f = tmp_path / "k"; f.write_text(m.priv.secret.hex()); f.chmod(0o600)
    kf = load_key({"AURIX_ENV": "production", "GUARDIAN_KEY_FILE": str(f)})
    assert kf.source == "key_file" and kf.address == m.address
