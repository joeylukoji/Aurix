import io
import json
import zipfile

import pytest

from aurix_verify import crypto
from aurix_verify.__main__ import main
from aurix_verify.jcs import canonicalize
from aurix_verify.package import FORBIDDEN_WORDS, read_zip, verify_package
from aurix_verify.rule import Evidence, decide

from helpers import ARBITER, AURIX, CARRIER, INTRUDER, att, keys, package, signed, terms


# ---------- JCS ----------
def test_jcs_sorts_keys_by_utf16_and_has_no_whitespace():
    assert canonicalize({"b": 1, "a": [True, None, "x"], "é": "€"}) == '{"a":[true,null,"x"],"b":1,"é":"€"}'.encode()
    # UTF-16 order: U+1F600 is the surrogate pair D83D DE00, which sorts before U+E000 (code point order would not)
    assert canonicalize({"\ue000": 1, "\U0001F600": 2}) == '{"\U0001F600":2,"\ue000":1}'.encode()


def test_jcs_escapes_like_es6_and_rejects_floats():
    assert canonicalize("a\"\\\n\u0001") == b'"a\\"\\\\\\n\\u0001"'
    with pytest.raises(TypeError):
        canonicalize({"amount": 1.5})


def test_jcs_is_order_independent():
    assert canonicalize({"x": {"b": 2, "a": 1}}) == canonicalize({"x": {"a": 1, "b": 2}})


# ---------- signatures ----------
def test_signature_verdicts():
    reg = crypto.KeyRegistry(keys())
    obj = {"hello": "world"}
    sig = AURIX.sign(obj, "2026-11-08T08:00:00Z")
    assert reg.verify(obj, sig) == crypto.VALID
    assert reg.verify({"hello": "World"}, sig) == crypto.INVALID
    assert reg.verify(obj, INTRUDER.sign(obj, "2026-11-08T08:00:00Z")) == crypto.UNKNOWN_KEY
    forged = dict(sig, key_id=CARRIER.key_id)  # AURIX's signature claimed as the carrier's
    assert reg.verify(obj, forged) == crypto.INVALID


def test_revoked_key_verdicts():
    reg = crypto.KeyRegistry(keys(revoked_carrier_at="2026-11-09T00:00:00Z"))
    obj = {"a": "1"}
    assert reg.verify(obj, CARRIER.sign(obj, "2026-11-08T12:00:00Z")) == crypto.VALID_KEY_REVOKED_LATER
    assert reg.verify(obj, CARRIER.sign(obj, "2026-11-10T12:00:00Z")) == crypto.REVOKED_KEY


# ---------- rule ----------
def ev(items, kd=None):
    reg = crypto.KeyRegistry(kd or keys())
    return [Evidence(a, s["key_id"], reg.role(s["key_id"]), reg.verify(a, s)) for a, s in items]


AUTH = signed(att("payment.authorized"))
DELIV = signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"))


def test_release_when_authorised_and_delivered():
    out = decide(terms(), ev([AUTH, DELIV]), "2026-11-10T09:00:00Z")
    assert out.decision == "RELEASE" and len(out.inputs) == 2


def test_wait_then_void_after_deadline():
    assert decide(terms(), ev([AUTH]), "2026-11-12T00:00:00Z").decision == "WAIT"
    assert decide(terms(), ev([AUTH]), "2026-11-16T00:00:00Z").decision == "VOID"


def test_delivery_not_yet_happened_at_evaluation_time():
    assert decide(terms(), ev([AUTH, DELIV]), "2026-11-10T07:00:00Z").decision == "WAIT"


def test_delivery_after_deadline_is_ignored():
    late = signed(att("shipment.delivered", when="2026-11-16T08:00:00Z"))
    assert decide(terms(), ev([AUTH, late]), "2026-11-17T00:00:00Z").decision == "VOID"


def test_forged_delivery_is_ignored():
    fake = signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"), INTRUDER)
    out = decide(terms(), ev([AUTH, fake]), "2026-11-10T09:00:00Z")
    assert out.decision == "WAIT" and any("unknown_key" in r for r in out.reasons)


def test_delivery_signed_by_aurix_instead_of_carrier_is_ignored():
    wrong = signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"), AURIX)
    assert decide(terms(), ev([AUTH, wrong]), "2026-11-10T09:00:00Z").decision == "WAIT"


def test_amount_mismatch_blocks_release():
    auth = signed(att("payment.authorized", amount="12.00"))
    out = decide(terms(), ev([auth, DELIV]), "2026-11-10T09:00:00Z")
    assert out.decision == "WAIT" and any("different amount" in r for r in out.reasons)


def test_delivery_for_another_order_is_ignored():
    other = signed(att("shipment.delivered", order_id="ORDER-2", when="2026-11-10T08:00:00Z"))
    assert decide(terms(), ev([AUTH, other]), "2026-11-10T09:00:00Z").decision == "WAIT"


def test_arbiter_hold_blocks_then_decides():
    t = terms(arbiter=ARBITER.key_id)
    hold = signed(att("arbiter.decision", when="2026-11-09T12:00:00Z", action="HOLD"))
    assert decide(t, ev([AUTH, DELIV, hold]), "2026-11-10T09:00:00Z").decision == "HOLD"
    void = signed(att("arbiter.decision", when="2026-11-10T10:00:00Z", n=2, action="VOID"))
    assert decide(t, ev([AUTH, DELIV, hold, void]), "2026-11-10T11:00:00Z").decision == "VOID"
    unhold = signed(att("arbiter.decision", when="2026-11-10T10:00:00Z", n=3, action="UNHOLD"))
    assert decide(t, ev([AUTH, DELIV, hold, unhold]), "2026-11-10T11:00:00Z").decision == "RELEASE"


def test_arbiter_actions_ignored_without_designated_arbiter():
    hold = signed(att("arbiter.decision", when="2026-11-09T12:00:00Z", action="HOLD"))
    assert decide(terms(), ev([AUTH, DELIV, hold]), "2026-11-10T09:00:00Z").decision == "RELEASE"


# ---------- package ----------
def files_of(data):
    return read_zip(data)


def test_valid_package_is_accepted_and_decision_replayed():
    r = verify_package(files_of(package()), keys())
    assert r["result"] == "VALID", r["errors"]
    assert r["decision"]["recorded"] == r["decision"]["replayed"] == "RELEASE" and r["decision"]["matches"]
    assert r["consistency"] == "consistent" and r["source_verification"] == "verified"
    assert {s["provider"]: s["type"] for s in r["sources"]} == {"paypal": "real", "aurix_demo_delivery": "simulated"}
    assert any("simulated" in l for l in r["limitations"])
    assert any("physically delivered" in l for l in r["limitations"])


def rewrite(data, path, fn):
    files = files_of(data)
    files[path] = fn(files[path])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return buf.getvalue()


def test_one_byte_change_is_detected():
    bad = rewrite(package(), "attestations/att_payment_authorized_1.json",
                  lambda b: b.replace(b'"120.00"', b'"920.00"'))
    r = verify_package(files_of(bad), keys())
    assert r["result"] == "INVALID" and r["integrity"] == "invalid"


def test_tampering_with_manifest_hash_too_breaks_the_signature():
    data = package()
    files = files_of(data)
    m = json.loads(files["manifest.json"])
    files["attestations/att_payment_authorized_1.json"] = files["attestations/att_payment_authorized_1.json"].replace(b"120.00", b"920.00")
    import hashlib
    m["files"]["attestations/att_payment_authorized_1.json"] = "sha256:" + hashlib.sha256(files["attestations/att_payment_authorized_1.json"]).hexdigest()
    files["manifest.json"] = json.dumps(m).encode()
    r = verify_package(files, keys())
    assert r["result"] == "INVALID" and r["integrity"] == "valid" and r["signatures"] != "valid"


def test_package_signed_by_unknown_issuer_is_rejected_with_external_keys():
    other = keys()
    other["keys"][0]["public_key"] = INTRUDER.public_key
    r = verify_package(files_of(package()), other)
    assert r["result"] == "INVALID" and any("manifest.sig.json" in e for e in r["errors"])


def test_self_declared_keys_are_flagged():
    r = verify_package(files_of(package()))
    assert r["trust_source"].startswith("package") and any("--keys" in l for l in r["limitations"])


def test_falsified_recorded_decision_is_detected():
    r = verify_package(files_of(package(decision_override="VOID")), keys())
    assert r["result"] == "INVALID" and not r["decision"]["matches"]


def test_revoked_carrier_key_is_reported():
    kd = keys(revoked_carrier_at="2026-11-09T00:00:00Z")
    r = verify_package(files_of(package(key_doc=kd)), kd)
    assert r["result"] == "INVALID" and any("revoked_key" in e for e in r["errors"])


def test_amount_mismatch_is_an_inconsistency():
    items = [signed(att("payment.authorized", amount="12.00")),
             signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"))]
    r = verify_package(files_of(package(items=items)), keys())
    assert r["consistency"] == "inconsistent" and any(a["code"] == "amount_mismatch" for a in r["anomalies"])
    assert r["decision"]["replayed"] == "WAIT"


def test_unsafe_zip_paths_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.json", b"{}")
    with pytest.raises(ValueError):
        read_zip(buf.getvalue())


def test_garbage_is_reported_not_raised():
    assert verify_package({"manifest.json": b"not json"})["result"] == "INVALID"


def test_report_never_uses_forbidden_wording():
    text = json.dumps(verify_package(files_of(package()), keys())).lower()
    assert not any(w in text for w in FORBIDDEN_WORDS)


def test_cli(tmp_path, capsys):
    p = tmp_path / "proof.zip"
    p.write_bytes(package())
    k = tmp_path / "keys.json"
    k.write_text(json.dumps(keys()))
    assert main([str(p), "--keys", str(k)]) == 0
    assert "VALID" in capsys.readouterr().out
    p.write_bytes(rewrite(package(), "decision.json", lambda b: b.replace(b"RELEASE", b"VOID")))
    assert main([str(p), "--keys", str(k)]) == 1
