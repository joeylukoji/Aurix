"""Rule conditions/1: composable conditions (all / any / at_least) and the buyer dispute window."""
import pytest

from aurix_verify import crypto
from aurix_verify.package import read_zip, verify_package
from aurix_verify.rule import Evidence, decide, validate_condition

from helpers import ARBITER, att, cond_terms, keys, package, signed

DELIVERED = {"evidence": "shipment.delivered", "source": "aurix_demo_delivery"}
INSPECTED = {"evidence": "inspection.passed", "source": "aurix_demo_inspection"}


def ev(*items, received=None):
    reg = crypto.KeyRegistry(keys())
    rec = received or {}
    return [Evidence(a, s["key_id"], reg.role(s["key_id"]), reg.verify(a, s),
                     rec.get(a["attestation_id"], a["timestamps"]["attested_at"])) for a, s in items]


AUTH = signed(att("payment.authorized"))
DLV = signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"))
INSP = signed(att("inspection.passed", when="2026-11-11T08:00:00Z"))


def test_all_waits_for_every_piece_of_evidence():
    out = decide(cond_terms(), ev(AUTH, DLV), "2026-11-10T09:00:00Z")
    assert out.decision == "WAIT" and "waiting for inspection.passed from aurix_demo_inspection" in out.reasons
    out = decide(cond_terms(), ev(AUTH, DLV, INSP), "2026-11-11T09:00:00Z")
    assert out.decision == "RELEASE" and len(out.inputs) == 3


def test_any_and_at_least():
    assert decide(cond_terms({"any": [DELIVERED, INSPECTED]}), ev(AUTH, INSP), "2026-11-11T09:00:00Z").decision == "RELEASE"
    two = {"at_least": 2, "of": [DELIVERED, INSPECTED]}
    assert decide(cond_terms(two), ev(AUTH, DLV), "2026-11-11T09:00:00Z").decision == "WAIT"
    assert decide(cond_terms(two), ev(AUTH, DLV, INSP), "2026-11-11T09:00:00Z").decision == "RELEASE"


def test_leaf_time_limit():
    late_ok = {"all": [DELIVERED | {"before": "2026-11-09T00:00:00Z"}]}
    out = decide(cond_terms(late_ok), ev(AUTH, DLV), "2026-11-10T09:00:00Z")
    assert out.decision == "WAIT" and any("after its time limit" in r for r in out.reasons)
    assert decide(cond_terms(late_ok), ev(AUTH, DLV), "2026-11-16T00:00:01Z").decision == "VOID"


def test_dispute_window_then_release():
    t = cond_terms(dispute_hours=48)
    assert decide(t, ev(AUTH, DLV, INSP), "2026-11-12T09:00:00Z").decision == "WAIT"
    assert decide(t, ev(AUTH, DLV, INSP), "2026-11-13T08:00:00Z").decision == "RELEASE"


def test_dispute_holds_then_voids_at_deadline_unless_arbiter_lifts_it():
    t = cond_terms(dispute_hours=48, arbiter=ARBITER.key_id)
    dispute = signed(att("buyer.disputed", when="2026-11-12T08:00:00Z"))
    assert decide(t, ev(AUTH, DLV, INSP, dispute), "2026-11-12T09:00:00Z").decision == "HOLD"
    assert decide(t, ev(AUTH, DLV, INSP, dispute), "2026-11-16T00:00:01Z").decision == "VOID"
    unhold = signed(att("arbiter.decision", when="2026-11-12T10:00:00Z", action="UNHOLD"))
    assert decide(t, ev(AUTH, DLV, INSP, dispute, unhold), "2026-11-13T09:00:00Z").decision == "RELEASE"


def test_dispute_after_the_window_is_ignored():
    t = cond_terms(dispute_hours=24)
    dispute = signed(att("buyer.disputed", when="2026-11-13T08:00:00Z"))
    assert decide(t, ev(AUTH, DLV, INSP, dispute), "2026-11-13T09:00:00Z").decision == "RELEASE"


def test_evidence_from_the_wrong_source_does_not_count():
    forged = signed(att("inspection.passed", when="2026-11-11T08:00:00Z", n=2),
                    signer=__import__("helpers").CARRIER)  # carrier key signing an inspection
    out = decide(cond_terms(), ev(AUTH, DLV, forged), "2026-11-11T09:00:00Z")
    assert out.decision == "WAIT"


@pytest.mark.parametrize("bad", [
    {}, {"all": []}, {"evidence": "money.sent", "source": "x"}, {"at_least": 3, "of": [DELIVERED, INSPECTED]},
    {"all": [DELIVERED], "any": [INSPECTED]}, {"evidence": "shipment.delivered"},
    {"all": [{"all": [{"all": [{"all": [DELIVERED]}]}]}]}, {"all": [DELIVERED] * 11},
    DELIVERED | {"run": "code"},
])
def test_validate_refuses_bad_conditions(bad):
    with pytest.raises(ValueError):
        validate_condition(bad)


def test_validate_checks_what_each_source_may_attest():
    sources = {"aurix_demo_delivery": {"shipment.delivered"}}
    assert validate_condition({"all": [DELIVERED]}, sources) == 1
    with pytest.raises(ValueError):
        validate_condition({"all": [INSPECTED]}, sources)


def test_package_with_custom_condition_replays_and_detects_tampering():
    data = package(t=cond_terms(), items=[AUTH, DLV, INSP], decided_at="2026-11-11T09:00:00Z")
    rep = verify_package(read_zip(data), keys())
    assert rep["result"] == "VALID" and rep["decision"]["replayed"] == "RELEASE" and rep["decision"]["matches"]
    forged = package(t=cond_terms(), items=[AUTH, DLV], decided_at="2026-11-11T09:00:00Z", decision_override="RELEASE")
    assert verify_package(read_zip(forged), keys())["result"] == "INVALID"
