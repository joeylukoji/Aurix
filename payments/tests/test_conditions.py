"""Custom conditions (rule conditions/1): AI draft, buyer approval, multi-source evidence, dispute window."""
from aurix_payments import escrow as E
from aurix_payments.advisor import Advisor, AdvisorConfig
from aurix_payments.escrow_keys import DEMO_INSPECTOR, DEV_INSPECTOR

from .test_escrow import carrier_attestation, setup, verify, wait

CONDITION = {"all": [{"evidence": "shipment.delivered", "source": E.DEMO_CARRIER},
                     {"evidence": "inspection.passed", "source": DEMO_INSPECTOR}]}


def custom_order(c, sim, **extra):
    r = c.post("/api/escrow/orders", json={"amount_usd": "120.00", "deadline_days": 7, "condition": CONDITION} | extra)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert r.json()["terms"]["rule"] == "conditions/1"
    sim.post(f"/sim/orders/{oid}/approve")
    assert c.post(f"/api/escrow/orders/{oid}/authorize", json={"confirm": True}).json()["status"] == "AUTHORIZED"
    return oid


def inspection(oid, when, n=2):
    return carrier_attestation(oid, when, DEV_INSPECTOR, provider=DEMO_INSPECTOR, n=n, etype="inspection.passed")


def test_every_source_is_needed_then_the_proof_replays(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = custom_order(c, sim)
    r = c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock())).json()
    assert r["evaluation"]["decision"] == "WAIT"
    assert any("inspection.passed" in x for x in r["evaluation"]["reasons"])
    r = c.post("/api/escrow/evidence", json=inspection(oid, clock())).json()
    assert r["evaluation"]["decision"] == "RELEASE"
    wait(c, oid, "ANCHORED")
    rep = verify(c, oid)
    assert rep["result"] == "VALID" and rep["decision"]["matches"] and rep["decision"]["rule"] == "conditions/1"


def test_a_source_may_only_attest_its_own_kind_of_event(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = custom_order(c, sim)
    bad = carrier_attestation(oid, clock(), DEV_INSPECTOR, provider=DEMO_INSPECTOR)  # inspector claims a delivery
    assert c.post("/api/escrow/evidence", json=bad).status_code == 422


def test_dispute_window_and_dispute(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = custom_order(c, sim, dispute_window_hours=48)
    c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock()))
    r = c.post("/api/escrow/evidence", json=inspection(oid, clock())).json()
    assert r["evaluation"]["decision"] == "WAIT"
    assert any("dispute window open" in x for x in r["evaluation"]["reasons"])
    r = c.post(f"/api/escrow/orders/{oid}/dispute", json={"reason": "wrong model"}).json()
    assert r["evaluation"]["decision"] == "HOLD"
    clock.advance(days=8)
    assert c.post(f"/api/escrow/orders/{oid}/evaluate").json()["decision"] == "VOID"
    wait(c, oid, "VOIDED")
    assert verify(c, oid)["result"] == "VALID"


def test_no_dispute_releases_after_the_window(tmp_path, monkeypatch):
    c, sim, clock = setup(tmp_path, monkeypatch)
    oid = custom_order(c, sim, dispute_window_hours=24)
    c.post("/api/escrow/evidence", json=carrier_attestation(oid, clock()))
    c.post("/api/escrow/evidence", json=inspection(oid, clock()))
    clock.advance(hours=25)
    assert c.post(f"/api/escrow/orders/{oid}/evaluate").json()["decision"] == "RELEASE"


def test_dispute_refused_without_a_window(tmp_path, monkeypatch):
    c, sim, _ = setup(tmp_path, monkeypatch)
    oid = custom_order(c, sim)
    assert c.post(f"/api/escrow/orders/{oid}/dispute", json={}).status_code == 409


def test_invalid_conditions_are_refused_before_paypal(tmp_path, monkeypatch):
    c, _, _ = setup(tmp_path, monkeypatch)
    for cond in ({"all": []}, {"evidence": "customs.cleared", "source": "nobody"},
                 {"evidence": "shipment.delivered", "source": E.DEMO_CARRIER, "within_days": 30}):
        r = c.post("/api/escrow/orders", json={"amount_usd": "10.00", "deadline_days": 7, "condition": cond})
        assert r.status_code == 422, (cond, r.text)
    assert c.post("/api/escrow/orders", json={"amount_usd": "10.00", "dispute_window_hours": 9999}).status_code == 422


def test_relative_limits_become_absolute_times(tmp_path, monkeypatch):
    c, _, _ = setup(tmp_path, monkeypatch)
    cond = {"evidence": "shipment.shipped", "source": E.DEMO_CARRIER, "within_days": 3}
    t = c.post("/api/escrow/orders", json={"amount_usd": "10.00", "condition": cond}).json()["terms"]
    assert "within_days" not in t["condition"] and t["condition"]["before"] < t["deadline"]


def test_template_proposal_for_an_import(tmp_path, monkeypatch):
    c, _, _ = setup(tmp_path, monkeypatch)
    r = c.post("/api/escrow/propose", json={"description": "I buy goods from a supplier in China, delivered in DRC",
                                                  "lang": "English"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["generated_by"] == "template" and p["advisory_only"]
    kinds = {lf["evidence"] for lf in p["condition"]["all"]}
    assert {"shipment.shipped", "inspection.passed", "customs.cleared", "shipment.delivered"} <= kinds
    assert any("air freight" in w for w in p["warnings"]) and p["summary"].startswith("The payment is released if")
    fr = c.post("/api/escrow/propose", json={"description": "Achat en Chine, livré en RDC", "lang": "French"}).json()
    assert fr["summary"].startswith("Le paiement est libéré si") and " ET " in fr["summary"]
    assert c.post("/api/escrow/propose", json={"description": " "}).status_code == 422


class FakeLLM:
    def __init__(self, content):
        self.content = content

    def __call__(self, request):
        import httpx
        return httpx.Response(200, json={"model": "gemma-test", "choices": [{"message": {"content": self.content}}]})


def test_llm_proposal_is_validated_and_bad_output_falls_back():
    import httpx
    catalog = [{"source": E.DEMO_CARRIER, "evidence": ["shipment.delivered"], "simulated": True}]
    good = ('{"condition": {"evidence": "shipment.delivered", "source": "aurix_demo_delivery"}, "deadline_days": 99,'
            ' "dispute_window_hours": 24, "explanation": "ok", "warnings": ["w"]}')
    adv = Advisor(AdvisorConfig(url="http://llm"), transport=httpx.MockTransport(FakeLLM(good)))
    p = adv.propose_condition("phones", catalog, "100", deadline_max=29)
    assert p["generated_by"] == "llm" and p["deadline_days"] == 29 and p["dispute_window_hours"] == 24
    adv = Advisor(AdvisorConfig(url="http://llm"), transport=httpx.MockTransport(FakeLLM("not json")))
    assert adv.propose_condition("phones", catalog, "100")["generated_by"] == "template"


def test_json_mode_rejected_is_retried_and_thoughts_are_dropped():
    import json

    import httpx
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(500, json=[{"error": {"code": 500}}])
        content = ('<thought>maybe {"evidence": "x"}</thought>```json\n{"condition": {"evidence": '
                   '"shipment.delivered", "source": "aurix_demo_delivery"}, "deadline_days": 7}\n```')
        return httpx.Response(200, json={"model": "gemma-4-31b-it", "choices": [{"message": {"content": content}}]})

    catalog = [{"source": E.DEMO_CARRIER, "evidence": ["shipment.delivered"], "simulated": True}]
    adv = Advisor(AdvisorConfig(url="http://llm"), transport=httpx.MockTransport(handler))
    p = adv.propose_condition("phones", catalog, "100")
    assert calls == [True, False] and p["generated_by"] == "llm" and p["model"] == "gemma-4-31b-it"
    adv.propose_condition("phones", catalog, "100")
    assert calls == [True, False, False]  # the refusal is remembered
    assert p["condition"] == {"evidence": "shipment.delivered", "source": "aurix_demo_delivery"}
