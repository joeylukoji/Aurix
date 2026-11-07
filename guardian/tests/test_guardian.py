from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import HISTORY, app

c = TestClient(app)
NOON = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc).isoformat()
NIGHT = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc).isoformat()


def req(**kw):
    body = {"context": "aur_transfer", "sender": "aur1alice", "recipient": "aur1bob",
            "amount": 20, "currency": "AUR", "timestamp": NOON}
    body.update(kw)
    return c.post("/v1/risk/score", json=body)


def setup_function():
    HISTORY.clear()


def test_health():
    assert c.get("/health").json()["status"] == "ok"


def test_always_advisory():
    r = req().json()
    assert r["advisory_only"] is True
    assert 0 <= r["risk_score"] <= 1
    assert r["level"] in {"low", "medium", "high"}


def test_risky_payment_scores_higher_with_reasons():
    for _ in range(5):
        req(amount=20)
    normal = req(amount=22).json()
    risky = req(amount=900, recipient="aur1stranger", timestamp=NIGHT).json()
    assert risky["risk_score"] > normal["risk_score"]
    assert risky["level"] in {"medium", "high"}
    assert len(risky["reasons"]) >= 2
    feats = {r["feature"] for r in risky["reasons"]}
    assert "amount_vs_history" in feats or "new_recipient" in feats


def test_validation():
    assert req(amount=-1).status_code == 422
    assert req(currency="EUR").status_code == 422
