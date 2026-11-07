"""Neural Guardian risk model: regularised logistic regression trained on PaySim, with exact linear
SHAP explanations, an isotonic probability calibration and thresholds chosen on a validation split.

Artefacts (produced by `python -m training.evaluate <paysim.csv> --ship`, see research/PLAN.md step 2.1):
  models/guardian_logreg.json      standardisation + coefficients (trained on PaySim steps 1-400)
  models/guardian_calibration.json isotonic map and raw-score thresholds (fitted on steps 401-550)
  models/paysim_metrics.json       test metrics (steps 551-744, read once) and model selection
LightGBM was evaluated as well and rejected on validation: its PR-AUC varied from ~0.96 to ~0.004
across training seeds. The score is advisory only.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .features import FEATURES as ALL_FEATURES
from .features import lr_base_feature, lr_columns, lr_row

MODELS = Path(__file__).resolve().parents[1] / "models"
MODEL_FILE = MODELS / "guardian_logreg.json"
CALIBRATION_FILE, METRICS_FILE = MODELS / "guardian_calibration.json", MODELS / "paysim_metrics.json"
MODEL_VERSION = "v1.1-logreg-paysim"
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


class RiskModel:
    def __init__(self) -> None:
        self.model = json.loads(MODEL_FILE.read_text())
        if self.model["columns"] != lr_columns():
            raise RuntimeError("guardian_logreg.json columns do not match app.features.lr_columns()")
        self.calibration = json.loads(CALIBRATION_FILE.read_text())
        self.metrics = json.loads(METRICS_FILE.read_text())
        self.mean, self.scale = np.array(self.model["mean"]), np.array(self.model["scale"])
        self.coef, self.intercept = np.array(self.model["coef"]), float(self.model["intercept"])
        self.thresholds = self.calibration["thresholds"]  # on the RAW score
        self.features = list(dict.fromkeys(lr_base_feature(c) for c in self.model["columns"]))
        digest = hashlib.sha256(MODEL_FILE.read_bytes() + CALIBRATION_FILE.read_bytes()).hexdigest()
        self.model_sha256 = digest
        # Version signed into attestations: release tag + hash of the model and calibration actually loaded.
        self.signed_version = f"{MODEL_VERSION}+{digest[:12]}"

    def level_for(self, raw: float) -> str:
        if raw >= self.thresholds["high"]:
            return "high"
        if raw >= self.thresholds["medium"]:
            return "medium"
        return "low"

    def calibrated(self, raw: float) -> float:
        return float(np.interp(raw, self.calibration["x"], self.calibration["y"]))

    def info(self) -> dict:
        m = self.metrics
        cal = {k: round(self.calibrated(v), 4) for k, v in self.thresholds.items()}
        return {
            "model_version": MODEL_VERSION, "signed_model_version": self.signed_version,
            "model_sha256": self.model_sha256,
            "model_type": "logistic regression (L2, class-balanced) + isotonic calibration",
            "training_data": {"name": m["dataset"], "sha256": m["dataset_sha256"], "split": m["split"],
                              "synthetic": False, "note": "PaySim is itself a simulator calibrated on real "
                              "mobile-money logs; it is not AURIX or PayPal data."},
            "features": [{"name": n, "description": FEATURE_DESCRIPTIONS[n]} for n in self.features],
            "dropped_features": m.get("dropped_features", []),
            "levels": {"rule": self.calibration["threshold_rules"], "raw_thresholds": self.thresholds,
                       "as_calibrated_probability": cal},
            "metrics": {k: m.get(k) for k in ("roc_auc", "pr_auc", "ci95", "test_rows", "test_fraud",
                                               "test_prevalence", "thresholds", "calibration_test",
                                               "model_selection", "alternatives_test")},
            "advisory_only": True,
        }

    def score(self, feats: dict[str, float], top_k: int = 3) -> Assessment:
        z = (np.array(lr_row(feats)) - self.mean) / self.scale
        parts = self.coef * z  # exact SHAP values of a linear model (vs the training mean)
        raw = 1.0 / (1.0 + math.exp(-(self.intercept + float(parts.sum()))))
        contrib: dict[str, float] = {}
        for col, v in zip(self.model["columns"], parts, strict=True):
            base = lr_base_feature(col)
            contrib[base] = contrib.get(base, 0.0) + float(v)
        order = sorted(contrib, key=lambda k: -contrib[k])
        reasons = [{"feature": k, "impact": round(contrib[k], 3), "text": reason_text(k, feats)}
                   for k in order[:top_k] if contrib[k] > 0]
        level = self.level_for(raw)
        if level != "low" and not reasons:  # always explain a medium/high score
            k = order[0]
            reasons = [{"feature": k, "impact": round(contrib[k], 3), "text": reason_text(k, feats)}]
        return Assessment(round(self.calibrated(raw), 4), level, reasons)

assert set(ALL_FEATURES) >= set(FEATURE_DESCRIPTIONS) - {"hour"}
