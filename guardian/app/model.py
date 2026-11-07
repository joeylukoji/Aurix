"""Risk model: LightGBM trained on a synthetic dataset, explained with SHAP.

The training data is SYNTHETIC (generated below). It encodes simple, documented
fraud heuristics so that the demo is reproducible; it is not real payment data.
SHAP values come from LightGBM's native TreeSHAP (`pred_contrib=True`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np

FEATURES = [
    "log_amount",
    "amount_vs_history",
    "new_recipient",
    "tx_last_hour",
    "night_time",
    "is_usd",
]

REASON_TEXT = {
    "log_amount": "High absolute amount",
    "amount_vs_history": "Amount {ratio:.1f}x the sender's average",
    "new_recipient": "First payment to this recipient",
    "tx_last_hour": "{count} payments in the last hour",
    "night_time": "Unusual time of day (night)",
    "is_usd": "External fiat payment (PayPal)",
}

MODEL_VERSION = "v0.1-lgbm-synthetic"


def generate_synthetic(n: int = 6000, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    amount = rng.lognormal(mean=3.0, sigma=1.2, size=n)
    ratio = np.clip(rng.lognormal(0.0, 0.8, size=n), 0.05, 50)
    new_rcpt = rng.random(n) < 0.3
    tx_hour = rng.poisson(1.0, size=n)
    night = rng.random(n) < 0.15
    usd = rng.random(n) < 0.4
    logit = (
        -3.2
        + 1.1 * np.log(ratio)
        + 1.2 * new_rcpt
        + 0.45 * tx_hour
        + 0.8 * night
        + 0.15 * np.log1p(amount)
        + 0.6 * (new_rcpt & (ratio > 4))
    )
    p = 1 / (1 + np.exp(-logit))
    y = (rng.random(n) < p).astype(int)
    X = np.column_stack([np.log1p(amount), ratio, new_rcpt, tx_hour, night, usd]).astype(float)
    return X, y


def train() -> lgb.Booster:
    X, y = generate_synthetic()
    ds = lgb.Dataset(X, label=y, feature_name=FEATURES)
    params = {"objective": "binary", "learning_rate": 0.08, "num_leaves": 15,
              "min_data_in_leaf": 40, "verbose": -1, "seed": 7, "deterministic": True}
    return lgb.train(params, ds, num_boost_round=150)


@dataclass
class Assessment:
    risk_score: float
    level: str
    reasons: list[dict]


def level_for(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


class RiskModel:
    def __init__(self) -> None:
        self.booster = train()

    def score(self, feats: dict[str, float], top_k: int = 3) -> Assessment:
        x = np.array([[feats[f] for f in FEATURES]], dtype=float)
        p = float(self.booster.predict(x)[0])
        contrib = self.booster.predict(x, pred_contrib=True)[0][:-1]  # drop bias term
        order = np.argsort(-contrib)
        reasons = []
        for i in order[:top_k]:
            if contrib[i] <= 0:
                break
            name = FEATURES[i]
            text = REASON_TEXT[name].format(ratio=feats["amount_vs_history"], count=int(feats["tx_last_hour"]))
            reasons.append({"feature": name, "impact": round(float(contrib[i]), 3), "text": text})
        return Assessment(round(p, 3), level_for(p), reasons)


def build_features(amount: float, currency: str, history: list[tuple[str, float, float]],
                   recipient: str, ts: float, hour: int) -> dict[str, float]:
    """history: list of (recipient, amount, unix_ts) for the sender."""
    amounts = [a for _, a, _ in history]
    avg = sum(amounts) / len(amounts) if amounts else amount
    return {
        "log_amount": math.log1p(amount),
        "amount_vs_history": min(amount / avg, 50.0) if avg > 0 else 1.0,
        "new_recipient": float(all(r != recipient for r, _, _ in history)),
        "tx_last_hour": float(sum(1 for _, _, t in history if ts - t < 3600)),
        "night_time": float(hour < 6),
        "is_usd": float(currency.upper() == "USD"),
    }
