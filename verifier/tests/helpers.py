"""Builds realistic packages for the tests: AURIX key, demo carrier key, arbiter key."""
from aurix_verify import crypto
from aurix_verify.attestation import make_attestation
from aurix_verify.jcs import digest
from aurix_verify.package import build_package, receipts_doc
from aurix_verify.rule import RULE_ID, decide, Evidence

T0 = "2026-11-08T08:00:00Z"
AURIX = crypto.Signer.from_seed("aurix-escrow-test-1", b"a" * 32)
CARRIER = crypto.Signer.from_seed("aurix-demo-delivery-1", b"c" * 32)
ARBITER = crypto.Signer.from_seed("arbiter-test-1", b"r" * 32)
INTRUDER = crypto.Signer.from_seed("intruder", b"x" * 32)


def keys(revoked_carrier_at=None):
    def k(s, role, owner, revoked=None):
        return {"key_id": s.key_id, "alg": "Ed25519", "public_key": s.public_key, "role": role, "owner": owner,
                "valid_from": "2026-01-01T00:00:00Z", "valid_until": None, "revoked_at": revoked,
                "revocation_reason": "test" if revoked else None}
    return {"keys": [k(AURIX, "aurix", "AURIX escrow service"),
                     k(CARRIER, "source:aurix_demo_delivery", "AURIX demo carrier (simulated)", revoked_carrier_at),
                     k(ARBITER, "arbiter", "test arbiter")]}


def terms(order_id="ORDER-1", amount="120.00", deadline="2026-11-15T23:59:59Z", arbiter=None):
    return {"schema": "aurix.escrow-terms", "schema_version": "1.0", "order_id": order_id,
            "amount": {"value": amount, "currency": "USD"}, "deadline": deadline, "rule": RULE_ID,
            "condition": "delivery-attested", "carrier": "aurix_demo_delivery", "arbiter_key_id": arbiter,
            "created_at": T0}


def att(kind, order_id="ORDER-1", amount="120.00", when="2026-11-09T10:00:00Z", n=1, action=None):
    src = {"payment.authorized": ("paypal", "real", "sandbox", "paypal_webhook_signature"),
           "shipment.delivered": ("aurix_demo_delivery", "simulated", "demo", "ed25519_source_key"),
           "arbiter.decision": ("arbiter", "real", "demo", "ed25519_arbiter_key")}[kind]
    return make_attestation(
        attestation_id=f"att_{kind.replace('.', '_')}_{n}", operation_id=order_id, provider=src[0],
        provider_event_id=f"EV-{kind}-{n}", event_type=kind, source_type=src[1], environment=src[2],
        verification_method=src[3], action=action,
        provider_status="CREATED" if kind == "payment.authorized" else None,
        amount={"value": amount, "currency": "USD"} if kind == "payment.authorized" else None,
        source_event_time=when, aurix_received_at=when, attested_at=when,
        provenance={"connector_id": "test", "raw_payload_hash": "sha256:" + "0" * 64})


def signed(a, signer=None):
    signer = signer or {"payment.authorized": AURIX, "shipment.delivered": CARRIER,
                        "arbiter.decision": ARBITER}[a["event"]["type"]]
    return a, signer.sign(a, a["timestamps"]["attested_at"])


def package(t=None, items=None, decided_at="2026-11-10T09:00:00Z", decision_override=None, key_doc=None,
            received=None):
    t = t or terms()
    items = items if items is not None else [signed(att("payment.authorized")),
                                             signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"))]
    kd = key_doc or keys()
    reg = crypto.KeyRegistry(kd)
    rec = received or {}  # attestation_id -> received_at (default: when it was attested)
    when = [(a, rec.get(a["attestation_id"], a["timestamps"]["attested_at"])) for a, _ in items]
    ev = [Evidence(a, s["key_id"], reg.role(s["key_id"]), reg.verify(a, s), w) for (a, s), (_, w) in zip(items, when)]
    at = crypto.parse_time(decided_at)
    out = decide(t, [e for e, (_, w) in zip(ev, when) if crypto.parse_time(w) <= at], decided_at)
    receipts = receipts_doc(t["order_id"], when)
    decision = {"schema": "aurix.escrow-decision", "schema_version": "1.0", "order_id": t["order_id"],
                "rule": RULE_ID, "decision": decision_override or out.decision,
                "inputs": sorted(digest(a) for a in out.inputs), "decided_at": decided_at,
                "deadline": t["deadline"], "reasons": out.reasons}
    meta = {"sources": {"paypal": {"type": "real"}, "aurix_demo_delivery": {"type": "simulated"}}}
    return build_package(terms=t, terms_sig=AURIX.sign(t, T0), attestations=items, receipts=receipts,
                         receipts_sig=AURIX.sign(receipts, decided_at), decision=decision,
                         decision_sig=AURIX.sign(decision, decided_at), keys=kd, trust_metadata=meta,
                         signer=AURIX, created_at=decided_at)
