"""H3 guardian quorum: the gateway queries every guardian, verifies signatures and aggregates."""
import json

import httpx

from app.guardians import GuardianClient, verify_attestation
from .conftest import sign_attestation

BODY = dict(context="aur_transfer", sender="aur1sender", recipient="aur1recipient", amount=5.0, currency="AUR")


def fake_guardians(spec: dict):
    """spec: host -> (seed, score, level, mode) with mode in ok|forged|down|unsigned|wrong_payment."""
    def handler(req: httpx.Request):
        seed, score, level, mode = spec[req.url.host]
        if mode == "down":
            return httpx.Response(503)
        body = json.loads(req.content)
        resp = {"risk_score": score, "level": level, "reasons": [], "model_version": "test", "advisory_only": True}
        if mode != "unsigned":
            att = sign_attestation(seed, body, score, level,
                                   **({"from": "aur1someoneelse"} if mode == "wrong_payment" else {}))
            if mode == "forged":  # payload altered after signing: claims "low"
                att["payload"] = att["payload"].replace(f'"decision":"{level}"', '"decision":"low"')
            resp["attestation"] = att
        return httpx.Response(200, json=resp)
    return httpx.MockTransport(handler)


def client(spec, quorum, **kw):
    return GuardianClient(",".join(f"http://{h}" for h in spec), transport=fake_guardians(spec), quorum=quorum, **kw)


def test_valid_quorum_most_severe_wins():
    spec = {"g1": ("s1", 0.2, "low", "ok"), "g2": ("s2", 0.55, "medium", "ok"), "g3": ("s3", 0.4, "medium", "ok")}
    r = client(spec, 2).score(**BODY)
    assert r["quorum_met"] and r["level"] == "medium" and r["risk_score"] == 0.55
    assert r["quorum"] == {"configured": 3, "valid": 3, "agree": 2, "required": 2, "quorum_met": True,
                           "decision": "medium"}
    assert [g["valid"] for g in r["guardians"]] == [True, True, True]
    for g in r["guardians"]:
        verify_attestation(g["attestation"])


def test_forged_signature_excluded():
    spec = {"g1": ("s1", 0.9, "high", "forged"), "g2": ("s2", 0.3, "medium", "ok")}
    r = client(spec, 1).score(**BODY)
    assert r["level"] == "medium" and r["quorum"]["valid"] == 1 and r["quorum_met"]
    bad = r["guardians"][0]
    assert not bad["valid"] and "signature" in bad["error"]


def test_quorum_not_met():
    spec = {"g1": ("s1", 0.3, "low", "ok"), "g2": ("s2", 0.3, "low", "down"),
            "g3": ("s3", 0.3, "low", "unsigned"), "g4": ("s4", 0.3, "low", "wrong_payment")}
    r = client(spec, 2).score(**BODY)
    assert r["quorum"]["valid"] == 1 and not r["quorum_met"]
    errs = [g.get("error") for g in r["guardians"]]
    assert errs[0] is None and "unreachable" in errs[1] and "unsigned" in errs[2] and "another payment" in errs[3]


def test_no_valid_guardian_is_unavailable_and_duplicates_count_once():
    assert client({"g1": ("s1", 0.9, "high", "forged")}, 1).score(**BODY) is None
    r = client({"g1": ("same", 0.3, "low", "ok"), "g2": ("same", 0.3, "low", "ok")}, 2).score(**BODY)
    assert r["quorum"]["valid"] == 1 and not r["quorum_met"]


def test_pubkey_allowlist():
    from app.crypto import Key
    spec = {"g1": ("s1", 0.3, "low", "ok"), "g2": ("s2", 0.3, "low", "ok")}
    r = client(spec, 1, pubkeys=[Key("s2").pubkey.hex()]).score(**BODY)
    assert r["quorum"]["valid"] == 1 and "GUARDIAN_PUBKEYS" in r["guardians"][0]["error"]


def test_verifies_real_guardian_signature():
    """Cross-check with an attestation produced by guardian/ (coincurve signer), fixed vector."""
    import hashlib
    import pathlib
    import subprocess
    import sys
    g = pathlib.Path(__file__).resolve().parents[2] / "guardian"
    py = g / ".venv/bin/python"
    if not py.exists():
        import pytest
        pytest.skip("guardian venv missing")
    out = subprocess.run([str(py), "-c", "import json,hashlib\nfrom app.keys import load_key\nk=load_key({'GUARDIAN_SEED':'x'})\n"
                          "p='{\"a\":1}'\nprint(json.dumps({'pubkey':k.pubkey.hex(),'sig':k.sign(p.encode()).hex()}))"],
                         cwd=g, capture_output=True, text=True, check=True).stdout
    d = json.loads(out.strip().splitlines()[-1])
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    sig = bytes.fromhex(d["sig"])
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(d["pubkey"])).verify(
        encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
        b'{"a":1}', ec.ECDSA(hashes.SHA256()))
    assert hashlib.sha256(bytes.fromhex(d["pubkey"])).digest()
