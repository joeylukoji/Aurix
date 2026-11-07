import math
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.features import online_features
from app.main import HISTORY, RECEIVED, app

c = TestClient(app)
NOON = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc).isoformat()


def req(**kw):
    body = {"context": "aur_transfer", "sender": "aur1alice", "recipient": "aur1bob",
            "amount": 20, "currency": "AUR", "timestamp": NOON}
    body.update(kw)
    return c.post("/v1/risk/score", json=body)


def setup_function():
    HISTORY.clear()
    RECEIVED.clear()


def test_health_and_model_card_endpoint():
    h = c.get("/health").json()
    assert h["status"] == "ok" and h["training_data"] == "PaySim" and h["advisory_only"] is True
    info = c.get("/v1/model").json()
    assert info["training_data"]["synthetic"] is False
    assert info["training_data"]["sha256"] == "16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b"
    # out-of-time evaluation on real PaySim prevalence
    assert info["metrics"]["roc_auc"] >= 0.95 and info["metrics"]["pr_auc"] >= 0.85
    assert {f["name"] for f in info["features"]}.isdisjoint({"hour", "log_amount"})


def test_always_advisory_and_bounded():
    r = req().json()
    assert r["advisory_only"] is True and 0 <= r["risk_score"] <= 1
    assert r["level"] in {"low", "medium", "high"}


def test_account_draining_transfer_to_unknown_recipient_is_high():
    r = req(amount=99.998, sender_balance=99.998, recipient="aur1stranger").json()
    assert r["level"] == "high"
    assert {x["feature"] for x in r["reasons"]} & {"amount_to_balance", "recipient_prior_txs", "drains_account"}


def test_small_share_of_balance_is_low():
    assert req(amount=5, sender_balance=100).json()["level"] == "low"


def test_known_recipient_lowers_risk():
    for _ in range(6):
        req(sender="aur1other", recipient="aur1shop", amount=10)
    known = req(amount=50, sender_balance=50, recipient="aur1shop").json()
    unknown = req(amount=50, sender_balance=50, recipient="aur1new").json()
    # the calibrated probability is a step function (isotonic), so compare it non-strictly and check
    # that the recipient history is what lowers the linear score
    assert known["risk_score"] <= unknown["risk_score"]
    from app.features import online_features
    from app.main import model

    def recipient_impact(prior: int) -> float:
        a = model.score(online_features(50, 50, "TRANSFER", 12, [], prior), top_k=10)
        return next((r["impact"] for r in a.reasons if r["feature"] == "recipient_prior_txs"), 0.0)

    assert recipient_impact(0) > recipient_impact(6)  # a recipient paid before lowers the score


def test_features_are_finite_or_missing():
    f = online_features(10, 0, "TRANSFER", 3, [0.0, 0.0], 0)
    assert math.isnan(f["amount_to_balance"]) and math.isnan(f["amount_vs_sender_avg"])
    assert online_features(1e9, 1, "TRANSFER", 3, [1.0], 0)["amount_to_balance"] == 10.0


def test_validation():
    assert req(amount=-1).status_code == 422
    assert req(currency="EUR").status_code == 422
    assert req(sender_balance=-5).status_code == 422
