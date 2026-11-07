import base64
import json

import httpx
import pytest

from attestor import crypto, keys
from attestor.service import Attestor, Config, normalize_amount, proof_hash

SEED = "attestor test seed"
ORDER, CAPTURE = "5O190127TN364715T", "3C679366HH908993F"


def decode(buf: bytes) -> dict:
    """Minimal protobuf decoder: {field: [values]} (varint -> int, len -> bytes)."""
    out, i = {}, 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            v, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"wire type {wire}")
        out.setdefault(field, []).append(v)
    return out


def _varint(b, i):
    shift = n = 0
    while True:
        x = b[i]
        i += 1
        n |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            return n, i


class Fake:
    """Mock chain REST + PayPal."""

    def __init__(self, proofs, capture):
        self.proofs, self.capture, self.broadcasts, self.capture_calls = proofs, capture, [], 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/aurix/smartmoney/v1/proofs_pending":
            return httpx.Response(200, json={"proofs": self.proofs, "pagination": {"next_key": None, "total": "0"}})
        if p.startswith("/cosmos/auth/v1beta1/account_info/"):
            return httpx.Response(200, json={"info": {"address": p.rsplit("/", 1)[1], "account_number": "7",
                                                      "sequence": "3"}})
        if p == "/cosmos/tx/v1beta1/txs" and req.method == "POST":
            body = json.loads(req.content)
            assert body["mode"] == "BROADCAST_MODE_SYNC"
            self.broadcasts.append(base64.b64decode(body["tx_bytes"]))
            return httpx.Response(200, json={"tx_response": {"code": 0, "txhash": "AB" * 32}})
        if p == "/v1/oauth2/token":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok"})
        if p.startswith("/v2/payments/captures/"):
            assert req.headers["authorization"] == "Bearer tok"
            self.capture_calls += 1
            return httpx.Response(200, json=self.capture)
        return httpx.Response(404, json={})


def proof(**kw):
    return {"hash": proof_hash(ORDER, CAPTURE), "external_ref": ORDER, "capture_ref": CAPTURE,
            "amount": "12.50", "currency": "USD",
            "status": "PROOF_STATUS_PENDING", "attestations": ["aur1gatewayxxxx"]} | kw


def capture(**kw):
    return {"id": CAPTURE, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "12.50"},
            "supplementary_data": {"related_ids": {"order_id": ORDER}}} | kw


def make(proofs, cap):
    fake = Fake(proofs, cap)
    att = Attestor(Config(seed=SEED, chain_id="aurix-test"), transport=httpx.MockTransport(fake))
    return att, fake


def test_attests_valid_completed_capture():
    att, fake = make([proof()], capture())
    assert att.run_once() == {proof()["hash"]: "attested"}
    assert len(fake.broadcasts) == 1 and proof()["hash"] in att.attested
    assert att.health()["attested"] == 1 and att.health()["address"] == att.address


def test_refuses_not_completed():
    att, fake = make([proof()], capture(status="PENDING"))
    assert list(att.run_once().values()) == ["refused"]
    assert fake.broadcasts == [] and "COMPLETED" in next(iter(att.refused.values()))


def test_refuses_order_id_mismatch():
    att, fake = make([proof()], capture(supplementary_data={"related_ids": {"order_id": "OTHERORDER"}}))
    assert list(att.run_once().values()) == ["refused"]
    assert fake.broadcasts == []


def test_refuses_hash_mismatch():
    att, fake = make([proof(hash="00" * 32)], capture())
    assert list(att.run_once().values()) == ["refused"]
    assert fake.broadcasts == [] and "hash" in next(iter(att.refused.values()))


def test_skips_already_attested():
    key = crypto.Key(SEED)
    att, fake = make([proof(attestations=["aur1gatewayxxxx", key.address])], capture())
    assert list(att.run_once().values()) == ["skipped"]
    assert fake.broadcasts == [] and fake.capture_calls == 0
    # local set: a proof attested in a previous cycle is not re-sent even before the chain reflects it
    att2, fake2 = make([proof()], capture())
    att2.run_once()
    att2.run_once()
    assert len(fake2.broadcasts) == 1 and fake2.capture_calls == 1


def test_sequence_increments_within_a_cycle():
    h2 = proof_hash("ORDER2", "CAP2")
    p2 = proof(hash=h2, external_ref="ORDER2", capture_ref="CAP2")
    att, fake = make([proof(), p2], capture())
    caps = {CAPTURE: capture(), "CAP2": capture(id="CAP2", supplementary_data={"related_ids": {"order_id": "ORDER2"}})}
    att.paypal.capture = lambda cid: caps[cid]
    att.run_once()
    seqs = [decode(decode(decode(t)[2][0])[1][0])[3][0] for t in fake.broadcasts]
    assert seqs == [3, 4]


def test_tx_bytes_decode_correctly():
    att, fake = make([proof()], capture())
    att.run_once()
    tx = decode(fake.broadcasts[0])
    body_b, auth_b, sig = tx[1][0], tx[2][0], tx[3][0]
    body = decode(body_b)
    any_ = decode(body[1][0])
    assert any_[1][0].decode() == "/aurix.smartmoney.v1.MsgAnchorProof"
    m = decode(any_[2][0])
    assert m[1][0].decode() == att.address
    assert m[2][0].decode() == proof()["hash"]
    assert m[3][0].decode() == ORDER
    assert m[4][0].decode() == CAPTURE
    assert m[5][0].decode() == "12.50" and m[6][0].decode() == "USD"
    assert set(m) == {1, 2, 3, 4, 5, 6}
    auth = decode(auth_b)
    si = decode(auth[1][0])
    pk = decode(si[1][0])
    assert pk[1][0].decode() == "/cosmos.crypto.secp256k1.PubKey"
    assert decode(pk[2][0])[1][0] == att.key.pubkey
    assert decode(decode(si[2][0])[1][0])[1][0] == 1  # SIGN_MODE_DIRECT
    assert si[3][0] == 3  # sequence
    fee = decode(auth[2][0])
    assert decode(fee[1][0]) == {1: [b"uaur"], 2: [str(crypto.FEE).encode()]} and fee[2][0] == crypto.GAS_LIMIT
    assert len(sig) == 64
    assert att.key.verify(crypto.sign_doc(body_b, auth_b, "aurix-test", 7), sig)


def test_key_matches_gateway_derivation():
    # address derivation is deterministic and bech32 "aur"
    a = crypto.Key(SEED).address
    assert a.startswith("aur1") and a == crypto.Key(SEED).address and len(a) == 42


@pytest.mark.parametrize("missing", ["", None])
def test_refuses_without_capture_ref(missing):
    att, fake = make([proof(capture_ref=missing)], capture())
    assert list(att.run_once().values()) == ["refused"] and fake.broadcasts == []


def test_health_endpoint():
    att, _ = make([], capture())
    att.cfg.port = 0
    srv = att.serve_health()
    try:
        r = httpx.get(f"http://127.0.0.1:{srv.server_address[1]}/health", trust_env=False)
        assert r.json()["address"] == att.address and r.json()["attested"] == 0 and r.json()["last_error"] is None
    finally:
        srv.shutdown()


def test_accepts_gateway_prefixed_external_ref():
    att, fake = make([proof(external_ref=f"paypal:{ORDER}")], capture())
    assert list(att.run_once().values()) == ["attested"] and len(fake.broadcasts) == 1


# ---------- H1: amount / currency ----------

def test_refuses_amount_mismatch():
    att, fake = make([proof(amount="99.00")], capture())
    assert list(att.run_once().values()) == ["refused"]
    assert fake.broadcasts == [] and "amount" in next(iter(att.refused.values()))


def test_refuses_currency_mismatch():
    att, fake = make([proof(currency="EUR")], capture())
    assert list(att.run_once().values()) == ["refused"]
    assert fake.broadcasts == [] and "currency" in next(iter(att.refused.values()))


@pytest.mark.parametrize("kw", [{"amount": ""}, {"amount": None}, {"currency": ""}, {}])
def test_refuses_empty_amount(kw):
    p = proof(**kw) if kw else {k: v for k, v in proof().items() if k not in ("amount", "currency")}
    att, fake = make([p], capture())
    assert list(att.run_once().values()) == ["refused"] and fake.broadcasts == []


def test_amount_normalisation():
    assert normalize_amount("12.5") == normalize_amount("12.50") == "12.50"
    assert normalize_amount("12") == "12.00"
    for bad in ("0", "-1", "abc", "1.005", "NaN", "Infinity"):
        with pytest.raises(Exception):
            normalize_amount(bad)
    # proof "12.5" vs capture "12.50" -> attested; the tx carries the proof's own string
    att, fake = make([proof(amount="12.5")], capture())
    assert list(att.run_once().values()) == ["attested"]
    m = decode(decode(decode(decode(fake.broadcasts[0])[1][0])[1][0])[2][0])
    assert m[5][0] == b"12.5"


# ---------- H2: keys ----------

VECTOR = "abandon " * 11 + "about"
COSMOS_ADDR = "cosmos19rl4cm2hmr8afy4kldpxz3fka4jguq0auqdal4"


def _bech32_data(addr: str) -> bytes:
    data = [crypto._CHARSET.index(c) for c in addr.rsplit("1", 1)[1][:-6]]
    return bytes(crypto._convertbits(data, 5, 8)[:20])


def test_mnemonic_vector():
    k = keys.key_from_mnemonic(VECTOR)
    assert k.address == crypto.bech32_encode("aur", _bech32_data(COSMOS_ADDR))
    assert crypto.bech32_encode("cosmos", _bech32_data(k.address)) == COSMOS_ADDR


def test_key_priority(tmp_path, caplog):
    kf = tmp_path / "k.hex"
    kf.write_text("11" * 32 + "\n")
    kf.chmod(0o600)
    m = keys.key_from_mnemonic(VECTOR).address
    f = crypto.Key(private_key=bytes.fromhex("11" * 32)).address
    s = crypto.Key(SEED).address
    assert keys.load_key(VECTOR, str(kf), SEED).address == m
    assert keys.load_key("", str(kf), SEED).address == f
    with caplog.at_level("WARNING"):
        assert keys.load_key("", "", SEED).address == s
    assert keys.DEV_WARNING in caplog.text
    assert Attestor(Config.from_env({"ATTESTOR_MNEMONIC": VECTOR, "ATTESTOR_SEED": SEED})).address == m
    with pytest.raises(SystemExit):
        keys.load_key("", "", "")
    bad = tmp_path / "bad"
    bad.write_text("zz")
    with pytest.raises(SystemExit):
        keys.load_key("", str(bad), "")


def test_production_refuses_seed():
    with pytest.raises(SystemExit, match="production"):
        Attestor(Config.from_env({"ATTESTOR_SEED": SEED, "AURIX_ENV": "production"}))
    assert Attestor(Config.from_env({"ATTESTOR_MNEMONIC": VECTOR, "AURIX_ENV": "production"})).address
