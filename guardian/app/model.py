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
    "log_amount": "Large payment amount ({amount:,.2f})",
    "amount_vs_history": "Amount is {ratio:.1f}x this sender's average payment",
    "new_recipient": "First payment from this sender to this recipient",
    "tx_last_hour": "{count} payments from this sender in the last hour",
    "night_time": "Sent at an unusual time of day (00:00-06:00 UTC)",
    "is_usd": "External fiat payment (USD via PayPal)",
}

FEATURE_DESCRIPTIONS = {
    "log_amount": "log(1 + amount)",
    "amount_vs_history": "amount / sender's average past amount (capped at 50)",
    "new_recipient": "1 if the sender never paid this recipient before",
    "tx_last_hour": "number of sender payments in the previous hour",
    "night_time": "1 if the UTC hour is before 06:00",
    "is_usd": "1 if currency is USD (PayPal rail)",
}

MODEL_VERSION = "v0.2-lgbm-synthetic"
TRAINING_DATA = (
    "6000 SYNTHETIC transactions (seed 42) whose fraud labels are sampled from a "
    "hand-written logistic rule (amount vs history, new recipient, velocity, night "
    "time, amount). NOT real payment data. 80/20 stratified-free random hold-out split."
)
THRESHOLDS = (0.4, 0.7)


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


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based (Mann-Whitney) ROC-AUC, ties averaged."""
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    pos = y == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def evaluate(booster: lgb.Booster, X: np.ndarray, y: np.ndarray) -> dict:
    p = booster.predict(X)
    out = {"holdout_size": int(len(y)), "holdout_positive_rate": round(float(y.mean()), 3),
           "roc_auc": round(roc_auc(y, p), 3), "thresholds": {}}
    for t in THRESHOLDS:
        pred = p >= t
        tp = int((pred & (y == 1)).sum())
        precision = tp / int(pred.sum()) if pred.sum() else 0.0
        recall = tp / int((y == 1).sum()) if (y == 1).sum() else 0.0
        out["thresholds"][str(t)] = {"precision": round(precision, 3), "recall": round(recall, 3),
                                     "flagged_rate": round(float(pred.mean()), 3)}
    return out


def train() -> tuple[lgb.Booster, dict]:
    """Train on 80% of the synthetic data; return the model and hold-out metrics."""
    X_all, y_all = generate_synthetic()
    idx = np.random.default_rng(123).permutation(len(y_all))
    cut = int(0.8 * len(idx))
    tr, te = idx[:cut], idx[cut:]
    X, y = X_all[tr], y_all[tr]
    ds = lgb.Dataset(X, label=y, feature_name=FEATURES)
    params = {"objective": "binary", "learning_rate": 0.08, "num_leaves": 15,
              "min_data_in_leaf": 40, "verbose": -1, "seed": 7, "deterministic": True}
    booster = lgb.train(params, ds, num_boost_round=150)
    return booster, evaluate(booster, X_all[te], y_all[te])


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
        self.booster, self.metrics = train()

    def info(self) -> dict:
        return {"model_version": MODEL_VERSION, "model_type": "LightGBM binary classifier",
                "explanations": "LightGBM TreeSHAP (pred_contrib)",
                "training_data": TRAINING_DATA, "synthetic_data": True,
                "features": [{"name": f, "description": FEATURE_DESCRIPTIONS[f]} for f in FEATURES],
                "levels": {"low": "< 0.4", "medium": "0.4 - 0.7", "high": ">= 0.7"},
                "metrics": self.metrics, "advisory_only": True}

    def score(self, feats: dict[str, float], top_k: int = 3) -> Assessment:
        x = np.array([[feats[f] for f in FEATURES]], dtype=float)
        p = float(self.booster.predict(x)[0])
        contrib = self.booster.predict(x, pred_contrib=True)[0][:-1]  # drop bias term
        order = np.argsort(-contrib)
        level = level_for(p)
        picked = [i for i in order[:top_k] if contrib[i] > 0]
        if not picked and level != "low":
            picked = [int(order[0])]  # always explain a medium/high score
        reasons = []
        for i in picked:
            name = FEATURES[i]
            text = REASON_TEXT[name].format(ratio=feats["amount_vs_history"], count=int(feats["tx_last_hour"]),
                                            amount=math.expm1(feats["log_amount"]))
            reasons.append({"feature": name, "impact": round(float(contrib[i]), 3), "text": text})
        return Assessment(round(p, 3), level, reasons)


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
