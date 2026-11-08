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
    return [Evidence(a, s["key_id"], reg.role(s["key_id"]), reg.verify(a, s), a["timestamps"]["attested_at"])
            for a, s in items]


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


def test_late_received_delivery_does_not_invalidate_an_honest_void():
    """Carrier attests on time but AURIX only receives it after voiding at the deadline: VOID stays valid."""
    items = [signed(att("payment.authorized")), signed(att("shipment.delivered", when="2026-11-15T20:00:00Z"))]
    data = package(items=items, decided_at="2026-11-16T00:00:01Z",
                   received={"att_shipment_delivered_1": "2026-11-16T03:00:00Z"})
    r = verify_package(files_of(data), keys())
    assert r["result"] == "VALID", r["errors"]
    assert r["decision"]["recorded"] == "VOID" == r["decision"]["replayed"]


def test_missing_receipts_are_reported():
    data = rewrite(package(), "receipts.json", lambda b: b.replace(b'"receipts": [', b'"receipts": [], "x": ['))
    r = verify_package(files_of(data), keys())
    assert r["result"] == "INVALID"


def test_backdated_delivery_received_after_deadline_is_ignored():
    """The carrier claims a time before the deadline, but AURIX received it after: no release."""
    late = signed(att("shipment.delivered", when="2026-11-15T20:00:00Z"))
    e = ev([AUTH, late])
    e[1].received_at = "2026-11-16T01:00:00Z"
    out = decide(terms(), e, "2026-11-16T02:00:00Z")
    assert out.decision == "VOID" and any("received by AURIX after the deadline" in r for r in out.reasons)


def test_arbiter_hold_still_in_place_at_deadline_voids():
    t = terms(arbiter=ARBITER.key_id)
    hold = signed(att("arbiter.decision", when="2026-11-09T12:00:00Z", action="HOLD"))
    assert decide(t, ev([AUTH, DELIV, hold]), "2026-11-16T00:00:00Z").decision == "VOID"
    late_release = signed(att("arbiter.decision", when="2026-11-16T01:00:00Z", n=2, action="RELEASE"))
    assert decide(t, ev([AUTH, DELIV, hold, late_release]), "2026-11-16T02:00:00Z").decision == "VOID"


def test_mismatched_order_references_are_ignored():
    mixed = att("shipment.delivered", when="2026-11-10T08:00:00Z")
    mixed["source"]["provider_reference"]["order_id"] = "ORDER-2"  # operation_id says ORDER-1
    assert decide(terms(), ev([AUTH, signed(mixed)]), "2026-11-10T09:00:00Z").decision == "WAIT"


def test_expired_key_with_backdated_signature_is_rejected_by_receipt_time():
    kd = keys()
    kd["keys"][1]["valid_until"] = "2026-11-09T00:00:00Z"  # carrier key expired before AURIX received the delivery
    items = [signed(att("payment.authorized")), signed(att("shipment.delivered", when="2026-11-08T20:00:00Z"))]
    data = package(items=items, key_doc=kd, received={"att_shipment_delivered_1": "2026-11-10T08:00:00Z"},
                   decided_at="2026-11-10T09:00:00Z")
    r = verify_package(files_of(data), kd)
    assert r["result"] == "INVALID" and any("key not valid when received" in x for x in r["errors"])


def test_make_attestation_shape_and_optional_parts():
    from aurix_verify.attestation import make_attestation
    a = make_attestation(attestation_id="x1", operation_id="O1", provider="p", provider_event_id="e1",
                         event_type="shipment.delivered", source_type="simulated", environment="demo",
                         verification_method="m", source_event_time="2026-11-08T10:00:00Z",
                         attested_at="2026-11-08T10:00:01Z")
    assert a["source"]["provider_reference"] == {"order_id": "O1"}
    assert "verified_by" not in a["source"] and "provenance" not in a and "amount" not in a["event"]
    assert list(a["timestamps"]) == ["source_event_time", "attested_at"]
    b = make_attestation(attestation_id="x2", operation_id="O1", provider="paypal", provider_event_id="e2",
                         event_type="payment.authorized", source_type="real", environment="sandbox",
                         verification_method="m", source_event_time="t", attested_at="t", aurix_received_at="t",
                         amount={"value": "1.00", "currency_code": "USD"}, verified_by="aurix-payments",
                         provenance={"raw_payload_hash": None})
    assert b["event"]["amount"] == {"value": "1.00", "currency": "USD"} and b["source"]["verified_by"]
    canonicalize(b)  # signable: no floats


def test_forged_decision_without_time_is_reported_not_raised():
    for bad in (b'"decided_at": "2026-11-10T09:00:00Z"', ):
        data = rewrite(package(), "decision.json", lambda b: b.replace(bad, b'"decided_at": 42'))
        r = verify_package(files_of(data), keys())
        assert r["result"] == "INVALID" and any("decided_at" in e for e in r["errors"])
    data = rewrite(package(), "decision.json", lambda b: b.replace(b'"decided_at"', b'"decided_on"'))
    assert verify_package(files_of(data), keys())["result"] == "INVALID"
