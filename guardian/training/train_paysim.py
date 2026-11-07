"""LEGACY (model v1.0, superseded): LightGBM on a 2-way split whose thresholds were read off the test set.
The shipped model and its evaluation now come from training/evaluate.py (train/validation/test protocol).
This module is kept for its shared helpers (build, sha256) and to reproduce the v1.0 numbers.

Trains Neural Guardian on the PaySim dataset (Kaggle: ealaxi/paysim1).

    python -m training.train_paysim /path/to/PS_20174392719_1491204439457_log.csv [dropped,features]

The shipped model drops "hour": in PaySim fraud is spread uniformly over 24 h while
legitimate traffic follows a daily cycle, so "night" alone predicts fraud. That is a
simulation artefact we do not want the model to rely on.

PaySim: 6,362,620 mobile-money transactions over 744 hourly steps (30 days),
simulated from real aggregated logs of an African mobile-money service; 8,213 are
labelled fraud (0.13%). Expected SHA-256 of the CSV:
16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b

Evaluation is out-of-time: train on steps 1-500, test on steps 501-744 with the
true fraud prevalence (no resampling of the test set).
Outputs models/paysim_lgbm.txt and models/paysim_metrics.json.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.features import CATEGORICAL, FEATURES, RATIO_CAP, TX_TYPES

OUT = Path(__file__).resolve().parents[1] / "models"
SPLIT_STEP = 500
EXPECTED_SHA256 = "16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("step", kind="stable").reset_index(drop=True)
    out = pd.DataFrame(index=df.index)
    out["log_amount"] = np.log1p(df["amount"])
    bal = df["oldbalanceOrg"].where(df["oldbalanceOrg"] > 0)
    ratio = df["amount"] / bal
    # exact drain (fraud pattern); amount > balance happens in PaySim but is impossible on AURIX
    out["drains_account"] = ratio.between(0.99, 1.01).astype(float)
    out["amount_to_balance"] = ratio.clip(upper=RATIO_CAP)
    out["tx_type"] = df["type"].map(TX_TYPES).astype(float)
    out["hour"] = (df["step"] % 24).astype(float)
    g = df.groupby("nameOrig", sort=False)["amount"]
    out["sender_prior_txs"] = g.cumcount().astype(float)
    prev_sum = g.cumsum() - df["amount"]
    prev_avg = (prev_sum / out["sender_prior_txs"]).where((out["sender_prior_txs"] > 0) & (prev_sum > 0))
    out["amount_vs_sender_avg"] = (df["amount"] / prev_avg).clip(upper=RATIO_CAP)
    out["recipient_prior_txs"] = df.groupby("nameDest", sort=False).cumcount().astype(float)
    out["step"], out["y"] = df["step"], df["isFraud"]
    num = out.select_dtypes("number")
    assert np.isfinite(num.fillna(0).to_numpy()).all(), "non-finite feature values"
    return out


def roc_auc(y, p) -> float:
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def pr_auc(y, p) -> float:
    order = np.argsort(-p, kind="mergesort")
    tp = np.cumsum(y[order]); fp = np.cumsum(1 - y[order])
    prec, rec = tp / (tp + fp), tp / y.sum()
    return float(np.sum(np.diff(np.concatenate([[0], rec])) * prec))


def at_threshold(y, p, t) -> dict:
    flag = p >= t
    tp = int((flag & (y == 1)).sum())
    return {"threshold": t, "precision": round(tp / max(flag.sum(), 1), 3), "recall": round(tp / y.sum(), 3),
            "flag_rate": round(float(flag.mean()), 5), "true_positives": tp, "flagged": int(flag.sum())}


def main(path: str, drop: list[str] | None = None) -> None:
    """drop: features excluded from training (e.g. ["hour"], a known PaySim artefact)."""
    t0 = time.time()
    feats = [f for f in FEATURES if f not in (drop or [])]
    digest = sha256(path)
    if digest != EXPECTED_SHA256:
        print(f"warning: unexpected SHA-256 {digest}", file=sys.stderr)
    df = pd.read_csv(path, usecols=["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "isFraud"],
                     dtype={"type": "category", "isFraud": "int8"})
    data = build(df)
    del df
    train, test = data[data.step <= SPLIT_STEP], data[data.step > SPLIT_STEP]
    print(f"rows {len(data):,} | train {len(train):,} ({int(train.y.sum())} fraud) | "
          f"test {len(test):,} ({int(test.y.sum())} fraud) | features in {time.time() - t0:.0f}s")

    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 200,
              "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1,
              "seed": 7, "deterministic": True, "num_threads": 4}
    dtrain = lgb.Dataset(train[feats], label=train.y, categorical_feature=[c for c in CATEGORICAL if c in feats],
                         free_raw_data=False)
    booster = lgb.train(params, dtrain, num_boost_round=300)
    p = booster.predict(test[feats])
    y = test.y.to_numpy()

    metrics = {
        "dataset": "PaySim (Kaggle ealaxi/paysim1), 6,362,620 transactions, 8,213 fraud",
        "dataset_sha256": digest,
        "features": feats, "dropped_features": drop or [],
        "split": f"out-of-time: train steps 1-{SPLIT_STEP}, test steps {SPLIT_STEP + 1}-744 (true prevalence)",
        "train_rows": len(train), "train_fraud": int(train.y.sum()),
        "test_rows": len(test), "test_fraud": int(y.sum()), "test_prevalence": round(float(y.mean()), 5),
        "roc_auc": round(roc_auc(y, p), 4), "pr_auc": round(pr_auc(y, p), 4),
        "thresholds": [at_threshold(y, p, t) for t in (0.05, 0.1, 0.3, 0.5, 0.7, 0.9)],
        "by_type": {k: {"test_rows": int((test.tx_type == v).sum()), "test_fraud": int(test.y[test.tx_type == v].sum())}
                    for k, v in TX_TYPES.items()},
        "feature_importance_gain": dict(sorted(zip(feats, map(float, booster.feature_importance("gain")), strict=True),
                                               key=lambda kv: -kv[1])),
    }
    OUT.mkdir(exist_ok=True)
    booster.save_model(str(OUT / "paysim_lgbm.txt"))
    (OUT / "paysim_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps({k: metrics[k] for k in ("roc_auc", "pr_auc", "thresholds")}, indent=1))
    print(f"done in {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1], [d for d in sys.argv[2].split(",") if d] if len(sys.argv) > 2 else None)
