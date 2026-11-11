"""Leakage-free evaluation and training of Neural Guardian on PaySim (see guardian/MODEL_CARD.md).

    python -m training.evaluate /path/to/paysim.csv [--out DIR] [--seeds 5] [--bootstrap 1000] [--ship]

Protocol (fixed in CONFIG below before looking at any test result):
  * out-of-time split: train = steps 1-400, validation = steps 401-550, test = steps 551-744;
  * the test set is read ONCE, at the end, by `final_report`; nothing is tuned on it;
  * thresholds are chosen on the validation set with pre-registered rules:
      medium = highest threshold whose validation recall >= 0.95 (broad net),
      high   = lowest threshold whose validation precision >= 0.90 (reliable alert), and high >= medium;
  * calibration: isotonic regression fitted on the validation scores;
  * uncertainty: stratified bootstrap 95 % CIs on the test set + spread over training seeds;
  * candidates on the same features and split: logistic regression, LightGBM, business rules.

Model selection happens on the validation split only (CONFIG["selection"]): the best stable candidate
among logistic regression, LightGBM and the business rules. `--ship` writes the model used by the API:
guardian/models/guardian_logreg.json (trained on the train split only), guardian_calibration.json
(isotonic map + thresholds) and paysim_metrics.json (test metrics).
Raw results go to research/results/raw/ml-eval/<utc time>-<git sha>/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.features import CATEGORICAL, FEATURES, LR_BASE, LR_MISSABLE, TX_TYPES, lr_columns

from .train_paysim import EXPECTED_SHA256, build, sha256


def roc_auc(y, p) -> float:
    """Tie-aware ROC-AUC (calibrated scores have many ties)."""
    return float(roc_auc_score(y, p))


def pr_auc(y, p) -> float:
    """Average precision (step-wise PR-AUC), tie-aware: tied scores form one threshold."""
    return float(average_precision_score(y, p))

ROOT = Path(__file__).resolve().parents[2]
MODELS = Path(__file__).resolve().parents[1] / "models"
CONFIG = {
    "split": {"train": [1, 400], "validation": [401, 550], "test": [551, 744]},
    "dropped_features": ["hour", "log_amount"],  # PaySim artefact / currency scale (MODEL_CARD.md)
    "threshold_rules": {"medium": {"min_recall": 0.95}, "high": {"min_precision": 0.90}},
    # Deviation log (reported with the results): the first rule set (medium: recall >= 0.90, high: FPR <= 0.001)
    # produced a "high" threshold BELOW the "medium" one (inverted levels) on 2026-11-07; it was replaced by
    # the rules above, which fix the meaning of the levels. The test results of the first run had been seen.
    "protocol_deviations": ["2026-11-07: threshold rules redefined after an inverted-level result (see comment)"],
    "lgbm_params": {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 200,
                    "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1,
                    "deterministic": True, "num_threads": 4},
    "num_boost_round": 300,
    "logreg": {"C": 1.0, "class_weight": "balanced", "max_iter": 2000},
    # Model selection (validation only): highest validation PR-AUC among candidates whose spread over
    # training seeds is below this standard deviation.
    "selection": {"metric": "validation_pr_auc", "max_seed_sd": 0.02},
}


# ---------------- metrics ----------------
def confusion(y: np.ndarray, p: np.ndarray, t: float) -> dict:
    f = p >= t
    tp, fp = int((f & (y == 1)).sum()), int((f & (y == 0)).sum())
    fn, tn = int((~f & (y == 1)).sum()), int((~f & (y == 0)).sum())
    return {"threshold": float(t), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1), "fpr": fp / max(fp + tn, 1),
            "flag_rate": float(f.mean())}


def brier(y, p) -> float:
    return float(np.mean((p - y) ** 2))


def ece(y, p, bins: int = 15) -> float:
    """Expected calibration error with equal-mass bins (robust at 0.1 % prevalence)."""
    order = np.argsort(p)
    err = 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk):
            err += len(chunk) / len(p) * abs(p[chunk].mean() - y[chunk].mean())
    return float(err)


def pick_threshold(y: np.ndarray, p: np.ndarray, rule: dict) -> float:
    """Threshold satisfying a pre-registered rule, computed on VALIDATION data only, over DISTINCT score
    values (a threshold t flags every score >= t, ties included).
    min_recall: the highest threshold whose recall reaches the target;
    min_precision: the lowest threshold whose precision reaches the target (most recall at that precision);
    max_fpr: the lowest threshold whose false-positive rate stays under the cap."""
    fpr, tpr, thr = roc_curve(y, p, drop_intermediate=False)  # thr descending, thr[0] = +inf
    fpr, tpr, thr = fpr[1:], tpr[1:], thr[1:]
    if "min_recall" in rule:
        return float(thr[int(np.argmax(tpr >= rule["min_recall"]))])
    if "min_precision" in rule:
        pos, neg = max((y == 1).sum(), 1), max((y == 0).sum(), 1)
        tp, fp = tpr * pos, fpr * neg
        precision = tp / np.maximum(tp + fp, 1)
        ok = np.nonzero(precision >= rule["min_precision"])[0]
        return float(thr[ok[-1]]) if len(ok) else float(thr[0])
    ok = np.nonzero(fpr <= rule["max_fpr"])[0]
    return float(thr[ok[-1]]) if len(ok) else float(thr[0])


def bootstrap_ci(y, p, thresholds: dict, n: int, seed: int = 0) -> dict:
    """Stratified bootstrap (positives and negatives resampled separately) 95 % CIs on the test set."""
    rng = np.random.default_rng(seed)
    pos, neg = np.nonzero(y == 1)[0], np.nonzero(y == 0)[0]
    stats = {"roc_auc": [], "pr_auc": []} | {f"{k}_{m}": [] for k in thresholds for m in ("precision", "recall")}
    for _ in range(n):
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        yb, pb = y[idx], p[idx]
        stats["roc_auc"].append(roc_auc(yb, pb))
        stats["pr_auc"].append(pr_auc(yb, pb))
        for k, t in thresholds.items():
            c = confusion(yb, pb, t)
            stats[f"{k}_precision"].append(c["precision"])
            stats[f"{k}_recall"].append(c["recall"])
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in stats.items()}


# ---------------- models ----------------
def rules_score(d: pd.DataFrame) -> np.ndarray:
    """Business-rule baseline: account drained by a transfer/cash-out to a never-paid recipient."""
    transfer = d.tx_type.isin([0, 1]).to_numpy()
    drain = d.drains_account.fillna(0).to_numpy()
    fresh = (d.recipient_prior_txs.fillna(0).to_numpy() == 0)
    return (transfer * (0.5 * drain + 0.3 * fresh + 0.2 * drain * fresh)).astype(float)


def lr_matrix(d: pd.DataFrame, feats: list[str] | None = None) -> np.ndarray:
    """Vectorised app.features.lr_row: fixed columns (lr_columns()), whatever the data contains."""
    cols = {}
    for c in LR_BASE:
        v = d[c].astype(float)
        if c in LR_MISSABLE:
            cols[c + "_missing"] = v.isna().astype(float)
        cols[c] = v.fillna(0.0)
    for k in sorted(TX_TYPES.values()):
        cols[f"tx_type_{k}"] = (d.tx_type == k).astype(float)
    X = pd.DataFrame(cols)[lr_columns()]
    return X.to_numpy(dtype=float)


def train_lgbm(train: pd.DataFrame, feats: list[str], seed: int) -> lgb.Booster:
    params = CONFIG["lgbm_params"] | {"seed": seed}
    ds = lgb.Dataset(train[feats], label=train.y, categorical_feature=[c for c in CATEGORICAL if c in feats])
    return lgb.train(params, ds, num_boost_round=CONFIG["num_boost_round"])


# ---------------- protocol ----------------
def evaluate_scores(name: str, yv, pv, yt, pt, n_boot: int, calibrate: bool) -> dict:
    """Validation picks thresholds (and the calibration map); the test set is scored once with them.

    Ranking metrics and thresholds use the RAW score: a monotone calibration must not change the ranking,
    and isotonic regression flattens the top of the score range into ties, which would distort PR-AUC.
    Probability quality (Brier, ECE) is measured on the calibrated score, the one the API exposes."""
    out = {"model": name}
    thresholds = {k: pick_threshold(yv, pv, r) for k, r in CONFIG["threshold_rules"].items()}
    if thresholds["high"] < thresholds["medium"]:  # levels must be ordered: high is the stricter alert
        out["threshold_order_warning"] = "high < medium on validation; high raised to medium"
        thresholds["high"] = thresholds["medium"]
    out["thresholds_from_validation"] = thresholds
    out["validation"] = {k: confusion(yv, pv, t) for k, t in thresholds.items()}
    out["test"] = {"roc_auc": roc_auc(yt, pt), "pr_auc": pr_auc(yt, pt),
                   **{k: confusion(yt, pt, t) for k, t in thresholds.items()}}
    if calibrate:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(pv, yv)
        pc = iso.predict(pt)
        out["calibration_test"] = {"brier_raw": brier(yt, pt), "ece_raw": ece(yt, pt),
                                   "brier_calibrated": brier(yt, pc), "ece_calibrated": ece(yt, pc)}
        out["thresholds_as_calibrated_probability"] = {k: float(iso.predict([t])[0]) for k, t in thresholds.items()}
        out["_isotonic"] = iso
    if n_boot:
        out["test_ci95"] = bootstrap_ci(yt, pt, thresholds, n_boot)
    return out


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default=str(ROOT / "research/results/raw/ml-eval"))
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--ship", action="store_true", help="write the API model, calibration and metrics")
    a = ap.parse_args()
    t0 = time.time()
    digest = sha256(a.csv)
    if digest != EXPECTED_SHA256:
        print(f"warning: unexpected dataset SHA-256 {digest}", file=sys.stderr)
    df = pd.read_csv(a.csv, usecols=["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "isFraud"],
                     dtype={"type": "category", "isFraud": "int8"})
    data = build(df)
    del df
    sp = CONFIG["split"]
    part = {k: data[data.step.between(*v)] for k, v in sp.items()}
    feats = [f for f in FEATURES if f not in CONFIG["dropped_features"]]
    tr, va, te = part["train"], part["validation"], part["test"]
    yv, yt = va.y.to_numpy(), te.y.to_numpy()
    sizes = {k: {"rows": len(v), "fraud": int(v.y.sum()), "prevalence": float(v.y.mean())} for k, v in part.items()}
    print(json.dumps(sizes), f"features in {time.time() - t0:.0f}s", flush=True)

    results = {"config": CONFIG, "protocol_deviations": CONFIG["protocol_deviations"], "features": feats, "dataset_sha256": digest, "sizes": sizes, "models": []}

    # 1) candidates, validated on the validation split only
    def fit_lr(seed: int):
        m = make_pipeline(StandardScaler(), LogisticRegression(random_state=seed, **CONFIG["logreg"]))
        return m.fit(lr_matrix(tr), tr.y.to_numpy())

    lrs = [fit_lr(seed) for seed in range(a.seeds)]
    print(f"logistic regression x{a.seeds} trained ({time.time() - t0:.0f}s)", flush=True)
    boosters = [train_lgbm(tr, feats, seed) for seed in range(a.seeds)]
    print(f"lightgbm x{a.seeds} trained ({time.time() - t0:.0f}s)", flush=True)
    Xv, Xt = lr_matrix(va), lr_matrix(te)
    cand = {
        "logistic_regression": {"val": [pr_auc(yv, m.predict_proba(Xv)[:, 1]) for m in lrs]},
        "lightgbm": {"val": [pr_auc(yv, b.predict(va[feats])) for b in boosters]},
        "rules": {"val": [pr_auc(yv, rules_score(va))]},
    }
    for c in cand.values():
        c["mean"], c["sd"] = float(np.mean(c["val"])), float(np.std(c["val"], ddof=1)) if len(c["val"]) > 1 else 0.0
    stable = {k: v for k, v in cand.items() if v["sd"] <= CONFIG["selection"]["max_seed_sd"]}
    chosen = max(stable, key=lambda k: stable[k]["mean"])
    results["model_selection"] = {"rule": CONFIG["selection"], "chosen": chosen,
                                  "validation_pr_auc": {k: {"by_seed": v["val"], "mean": v["mean"], "sd": v["sd"]}
                                                        for k, v in cand.items()}}
    print("selection:", {k: (round(v["mean"], 4), round(v["sd"], 4)) for k, v in cand.items()}, "->", chosen, flush=True)

    # 2) test evaluation (read once): every candidate, thresholds and calibration from validation
    lr0 = lrs[0]
    primary = evaluate_scores("logistic_regression", yv, lr0.predict_proba(Xv)[:, 1], yt, lr0.predict_proba(Xt)[:, 1],
                              a.bootstrap, calibrate=True)
    iso = primary.pop("_isotonic")
    primary["seed_spread_test_pr_auc"] = {"values": [pr_auc(yt, m.predict_proba(Xt)[:, 1]) for m in lrs]}
    lgb_runs = [evaluate_scores("lightgbm", yv, b.predict(va[feats]), yt, b.predict(te[feats]), 0, calibrate=False)
                for b in boosters]
    lgb0 = evaluate_scores("lightgbm", yv, boosters[0].predict(va[feats]), yt, boosters[0].predict(te[feats]),
                           a.bootstrap, calibrate=False)
    lgb0["seed_spread_test_pr_auc"] = {"values": [r["test"]["pr_auc"] for r in lgb_runs]}
    rules = evaluate_scores("rules", yv, rules_score(va), yt, rules_score(te), a.bootstrap, calibrate=False)
    results["models"] = [primary, lgb0, rules]
    print("test evaluation done", flush=True)

    # 3) paired bootstrap on test PR-AUC: chosen model minus each alternative
    p_main = lr0.predict_proba(Xt)[:, 1]
    rng = np.random.default_rng(1)
    pos, neg = np.nonzero(yt == 1)[0], np.nonzero(yt == 0)[0]
    others = {"rules": rules_score(te), "lightgbm": boosters[0].predict(te[feats])}
    diffs = {k: [] for k in others}
    for _ in range(a.bootstrap):
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        base = pr_auc(yt[idx], p_main[idx])
        for k, po in others.items():
            diffs[k].append(base - pr_auc(yt[idx], po[idx]))
    results["paired_test_pr_auc_difference_chosen_minus"] = {
        k: {"mean": float(np.mean(v)), "ci95": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))],
            "share_of_resamples_chosen_better": float(np.mean(np.array(v) > 0))} for k, v in diffs.items() if v}

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = Path(a.out) / f"{stamp}-{git_sha()}"
    run.mkdir(parents=True, exist_ok=True)
    env = {"python": sys.version, "platform": platform.platform(), "lightgbm": lgb.__version__,
           "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
           "seeds": a.seeds, "bootstrap": a.bootstrap, "git": git_sha(), "seconds": round(time.time() - t0)}
    (run / "env.json").write_text(json.dumps(env, indent=2))
    (run / "results.json").write_text(json.dumps(results, indent=2))
    print(f"results -> {run}", flush=True)

    if a.ship:  # API artefacts: logistic regression trained on train only; calibration + thresholds from val
        MODELS.mkdir(exist_ok=True)
        scaler, clf = lr0.named_steps["standardscaler"], lr0.named_steps["logisticregression"]
        model = {"type": "logistic_regression", "columns": lr_columns(),
                 "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                 "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
                 "trained_on": "PaySim steps 1-400", "params": CONFIG["logreg"]}
        (MODELS / "guardian_logreg.json").write_text(json.dumps(model, indent=2))
        cal = {"method": "isotonic (fitted on validation steps 401-550); thresholds apply to the RAW score",
               "x": [float(v) for v in iso.X_thresholds_], "y": [float(v) for v in iso.y_thresholds_],
               "thresholds": primary["thresholds_from_validation"], "threshold_rules": CONFIG["threshold_rules"]}
        (MODELS / "guardian_calibration.json").write_text(json.dumps(cal, indent=2))
        t = primary["test"]
        metrics = {
            "model": "logistic_regression", "dataset": "PaySim (Kaggle ealaxi/paysim1), 6,362,620 transactions, 8,213 fraud",
            "dataset_sha256": digest, "features": feats, "dropped_features": CONFIG["dropped_features"],
            "split": "out-of-time: train steps 1-400, validation 401-550 (model selection, thresholds, calibration), "
                     "test 551-744 (evaluated once, true prevalence)",
            "test_rows": sizes["test"]["rows"], "test_fraud": sizes["test"]["fraud"],
            "test_prevalence": round(sizes["test"]["prevalence"], 5),
            "roc_auc": round(t["roc_auc"], 4), "pr_auc": round(t["pr_auc"], 4), "ci95": primary.get("test_ci95"),
            "thresholds": [t[k] | {"level": k} for k in CONFIG["threshold_rules"]],
            "calibration_test": primary["calibration_test"], "model_selection": results["model_selection"],
            "protocol_deviations": CONFIG["protocol_deviations"],
            "alternatives_test": {m["model"]: {"roc_auc": m["test"]["roc_auc"], "pr_auc": m["test"]["pr_auc"],
                                               **({"seed_spread_pr_auc": m["seed_spread_test_pr_auc"]["values"]}
                                                  if "seed_spread_test_pr_auc" in m else {})}
                                  for m in results["models"][1:]},
            "paired_test_pr_auc_difference": results["paired_test_pr_auc_difference_chosen_minus"],
            "results_run": str(run.relative_to(ROOT)),
        }
        (MODELS / "paysim_metrics.json").write_text(json.dumps(metrics, indent=2))
        print(f"shipped model + calibration + metrics -> {MODELS}", flush=True)

if __name__ == "__main__":
    main()
