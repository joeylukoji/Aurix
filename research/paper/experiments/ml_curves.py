"""Curve data for the risk-scorer figures, from a second run of the frozen protocol of
guardian/training/evaluate.py (same split, features, hyper-parameters, seeds and threshold rules).

Nothing is selected or tuned here: the script refits the candidates, checks the headline numbers against the
stored run, and writes the points the figures need (precision-recall curves, reliability bins, prevalence per
time step, mean absolute attribution per feature).

    cd guardian && python ../research/paper/experiments/ml_curves.py /path/to/paysim.csv ../research/paper/data/ml_curves.json
"""
import json
import platform
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "guardian"))
from app.features import FEATURES, lr_columns  # noqa: E402
from training import evaluate as ev  # noqa: E402
from training.train_paysim import EXPECTED_SHA256, build, sha256  # noqa: E402

SEEDS = 5


def thin_pr(y, p, points=400):
    """Precision-recall curve reduced to about `points` vertices, keeping the envelope of each recall slice."""
    prec, rec, _ = precision_recall_curve(y, p)
    order = np.argsort(rec, kind="stable")
    rec, prec = rec[order], prec[order]
    edges = np.linspace(0, 1, points + 1)
    idx = np.searchsorted(rec, edges, side="left").clip(0, len(rec) - 1)
    keep = np.unique(np.concatenate([idx, [0, len(rec) - 1]]))
    return {"recall": [round(float(v), 5) for v in rec[keep]], "precision": [round(float(v), 5) for v in prec[keep]]}


def reliability(y, p, edges):
    """Mean predicted probability, observed fraud rate and count in each probability bin."""
    out = []
    which = np.digitize(p, edges[1:-1])
    for b in range(len(edges) - 1):
        m = which == b
        if m.any():
            out.append({"lo": float(edges[b]), "hi": float(edges[b + 1]), "n": int(m.sum()),
                        "mean_predicted": float(p[m].mean()), "observed": float(y[m].mean())})
    return out


def main(csv: str, out: str) -> None:
    t0 = time.time()
    digest = sha256(csv)
    assert digest == EXPECTED_SHA256, digest
    df = pd.read_csv(csv, usecols=["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "isFraud"],
                     dtype={"type": "category", "isFraud": "int8"})
    per_step = df.groupby("step").isFraud.agg(["size", "sum"])
    data = build(df)
    del df
    sp = ev.CONFIG["split"]
    tr, va, te = (data[data.step.between(*sp[k])] for k in ("train", "validation", "test"))
    feats = [f for f in FEATURES if f not in ev.CONFIG["dropped_features"]]
    yv, yt = va.y.to_numpy(), te.y.to_numpy()
    Xtr, Xv, Xt = ev.lr_matrix(tr), ev.lr_matrix(va), ev.lr_matrix(te)
    print(f"features built ({time.time() - t0:.0f}s)", flush=True)

    lr = make_pipeline(StandardScaler(), LogisticRegression(random_state=0, **ev.CONFIG["logreg"])).fit(Xtr, tr.y.to_numpy())
    pv, pt = lr.predict_proba(Xv)[:, 1], lr.predict_proba(Xt)[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(pv, yv)
    pc = iso.predict(pt)
    thresholds = {k: ev.pick_threshold(yv, pv, r) for k, r in ev.CONFIG["threshold_rules"].items()}
    print(f"logistic regression fitted ({time.time() - t0:.0f}s)", flush=True)

    boosters = [ev.train_lgbm(tr, feats, seed) for seed in range(SEEDS)]
    lgb_val = [b.predict(va[feats]) for b in boosters]
    lgb_test = [b.predict(te[feats]) for b in boosters]
    print(f"lightgbm x{SEEDS} fitted ({time.time() - t0:.0f}s)", flush=True)
    rv, rt = ev.rules_score(va), ev.rules_score(te)

    # exact attributions of the linear model: coef * (x - mean) / scale, summed over the columns of one feature
    scaler, clf = lr.named_steps["standardscaler"], lr.named_steps["logisticregression"]
    phi = clf.coef_[0] * (Xt - scaler.mean_) / scaler.scale_
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(lr_columns()):
        base = "tx_type" if c.startswith("tx_type_") else c.removesuffix("_missing")
        groups.setdefault(base, []).append(i)
    by_feature = {g: phi[:, idx].sum(axis=1) for g, idx in groups.items()}
    attributions = {g: {"mean_abs_all": float(np.abs(v).mean()), "mean_abs_fraud": float(np.abs(v[yt == 1]).mean()),
                        "mean_fraud": float(v[yt == 1].mean()), "mean_legit": float(v[yt == 0].mean())}
                    for g, v in by_feature.items()}

    edges = np.concatenate([[0.0], np.logspace(-4, 0, 13)])
    result = {
        "note": "second run of the frozen protocol; no selection or tuning",
        "env": {"python": sys.version.split()[0], "platform": platform.platform(), "sklearn": sklearn.__version__,
                "lightgbm": lgb.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "dataset_sha256": digest,
        "check": {
            "validation_pr_auc": {"logistic_regression": ev.pr_auc(yv, pv), "rules": ev.pr_auc(yv, rv),
                                  "lightgbm_by_seed": [ev.pr_auc(yv, p) for p in lgb_val]},
            "test_pr_auc": {"logistic_regression": ev.pr_auc(yt, pt), "rules": ev.pr_auc(yt, rt),
                            "lightgbm_by_seed": [ev.pr_auc(yt, p) for p in lgb_test]},
            "test_roc_auc": {"logistic_regression": ev.roc_auc(yt, pt), "rules": ev.roc_auc(yt, rt)},
            "thresholds": thresholds,
            "test_at_thresholds": {k: ev.confusion(yt, pt, t) for k, t in thresholds.items()},
            "brier": {"raw": ev.brier(yt, pt), "calibrated": ev.brier(yt, pc)},
            "ece": {"raw": ev.ece(yt, pt), "calibrated": ev.ece(yt, pc)},
        },
        "pr_curves_test": {"logistic_regression": thin_pr(yt, pt), "rules": thin_pr(yt, rt),
                           "lightgbm_by_seed": [thin_pr(yt, p) for p in lgb_test]},
        "pr_curves_validation": {"lightgbm_by_seed": [thin_pr(yv, p) for p in lgb_val]},
        "reliability_test": {"raw": reliability(yt, pt, edges), "calibrated": reliability(yt, pc, edges)},
        "prevalence_test": float(yt.mean()),
        "per_step": {"step": [int(s) for s in per_step.index], "transactions": [int(v) for v in per_step["size"]],
                     "fraud": [int(v) for v in per_step["sum"]]},
        "attributions_test": attributions,
        "seconds": round(time.time() - t0),
    }
    Path(out).write_text(json.dumps(result, indent=1))
    print(f"written {out} ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
