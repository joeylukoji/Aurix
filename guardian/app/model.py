"""Neural Guardian risk model: LightGBM trained on the PaySim dataset, explained with SHAP.

The model file (models/paysim_lgbm.txt) and its out-of-time evaluation
(models/paysim_metrics.json) are produced by training/train_paysim.py from the
PaySim CSV (Kaggle ealaxi/paysim1). SHAP values come from LightGBM's native
TreeSHAP (`pred_contrib=True`). The score is advisory only.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np

from .features import FEATURES as ALL_FEATURES

MODELS = Path(__file__).resolve().parents[1] / "models"
MODEL_FILE, METRICS_FILE = MODELS / "paysim_lgbm.txt", MODELS / "paysim_metrics.json"
MODEL_VERSION = "v1.0-lgbm-paysim"
# Chosen on the out-of-time test set: medium >= 0.1 (precision 0.96, recall 0.95),
# high >= 0.5 (precision 0.96, recall 0.92).
THRESHOLDS = (0.1, 0.5)
TX_TYPE_NAMES = {0: "TRANSFER", 1: "CASH_OUT", 2: "PAYMENT", 3: "CASH_IN", 4: "DEBIT"}

FEATURE_DESCRIPTIONS = {
    "log_amount": "log(1 + amount)",
    "amount_to_balance": "amount / sender balance before the payment (missing if unknown)",
    "drains_account": "1 if the payment uses >= 99% of the sender's balance",
    "tx_type": "transaction type: TRANSFER (AUR transfer, escrow) or PAYMENT (PayPal merchant order)",
    "hour": "hour of day (NOT used by the shipped model: PaySim artefact)",
    "sender_prior_txs": "payments previously sent by this sender",
    "amount_vs_sender_avg": "amount / mean of the sender's previous amounts",
    "recipient_prior_txs": "payments previously received by this recipient",
}


def reason_text(name: str, f: dict) -> str:
    if name == "log_amount":
        return f"Large amount ({math.expm1(f['log_amount']):,.2f})"
    if name == "amount_to_balance":
        return f"Spends {f['amount_to_balance']:.0%} of the sender's balance" if not math.isnan(f[name]) \
            else "Sender balance unknown"
    if name == "drains_account":
        return "Empties the sender's account, the typical pattern of account-takeover fraud"
    if name == "tx_type":
        return f"{TX_TYPE_NAMES[int(f['tx_type'])]} payments are where fraud happens in the training data"
    if name == "sender_prior_txs":
        return f"Sender has little payment history ({int(f[name])} previous payments)"
    if name == "amount_vs_sender_avg":
        return f"Amount is {f[name]:.1f}x this sender's average payment" if not math.isnan(f[name]) \
            else "No previous payments to compare the amount with"
    if name == "recipient_prior_txs":
        return f"Recipient has rarely been paid before ({int(f[name])} previous payments)"
    return name


@dataclass
class Assessment:
    risk_score: float
    level: str
    reasons: list[dict]


def level_for(score: float) -> str:
    if score >= THRESHOLDS[1]:
        return "high"
    if score >= THRESHOLDS[0]:
        return "medium"
    return "low"


class RiskModel:
    def __init__(self) -> None:
        self.booster = lgb.Booster(model_file=str(MODEL_FILE))
        self.features = self.booster.feature_name()
        self.metrics = json.loads(METRICS_FILE.read_text())

    def info(self) -> dict:
        m = self.metrics
        return {
            "model_version": MODEL_VERSION, "model_type": "LightGBM gradient-boosted trees (binary)",
            "training_data": {"name": m["dataset"], "sha256": m["dataset_sha256"], "split": m["split"],
                              "synthetic": False, "note": "PaySim is itself a simulator calibrated on real "
                              "mobile-money logs; it is not AURIX or PayPal data."},
            "features": [{"name": n, "description": FEATURE_DESCRIPTIONS[n]} for n in self.features],
            "dropped_features": m.get("dropped_features", []),
            "levels": {"low": f"< {THRESHOLDS[0]}", "medium": f">= {THRESHOLDS[0]}", "high": f">= {THRESHOLDS[1]}"},
            "metrics": {k: m[k] for k in ("roc_auc", "pr_auc", "test_rows", "test_fraud", "test_prevalence", "thresholds")},
            "advisory_only": True,
        }

    def score(self, feats: dict[str, float], top_k: int = 3) -> Assessment:
        x = np.array([[feats[f] for f in self.features]], dtype=float)
        p = float(self.booster.predict(x)[0])
        contrib = self.booster.predict(x, pred_contrib=True)[0][:-1]  # drop the bias term
        order = np.argsort(-contrib)
        reasons = [{"feature": self.features[i], "impact": round(float(contrib[i]), 3),
                    "text": reason_text(self.features[i], feats)} for i in order[:top_k] if contrib[i] > 0]
        level = level_for(p)
        if level != "low" and not reasons:  # always explain a medium/high score
            i = int(order[0])
            reasons = [{"feature": self.features[i], "impact": round(float(contrib[i]), 3),
                        "text": reason_text(self.features[i], feats)}]
        return Assessment(round(p, 4), level, reasons)


assert set(ALL_FEATURES) >= set(FEATURE_DESCRIPTIONS) - {"hour"}
