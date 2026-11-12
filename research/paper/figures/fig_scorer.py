"""Figure: the risk-scorer study. (a) transactions and fraud per hourly step with the time-ordered split;
(b) precision-recall curves on the test split; (c) reliability of the score before and after calibration.

    python research/paper/figures/fig_scorer.py        (reads ../data/ml_curves.json, writes fig_scorer.pdf)
"""
import json

import matplotlib.pyplot as plt
import numpy as np

from style import BLUE, DATA, GREY, HERE, INK, LIGHT, OCHRE, RED, panel_label

c = json.loads((DATA / "ml_curves.json").read_text())
fig, (a, b, r) = plt.subplots(1, 3, figsize=(6.6, 1.8), gridspec_kw={"width_ratios": [1.25, 1, 1], "wspace": 0.42})

# (a) volume and fraud per step
step = np.array(c["per_step"]["step"])
tx = np.array(c["per_step"]["transactions"])
fraud = np.array(c["per_step"]["fraud"])
a.set_yscale("log")
a.plot(step, tx, color=LIGHT, lw=0.5)
day = np.convolve(tx, np.ones(24) / 24, mode="valid")  # 24-step moving average
a.plot(step[23:], day, color=GREY, lw=0.9)
a.plot(step, np.where(fraud > 0, fraud, np.nan), color=INK, lw=0, marker=".", ms=1.3)
for x in (400.5, 550.5):
    a.axvline(x, color=INK, lw=0.5, ls=(0, (3, 2)))
for x, name, share in ((200, "train", "0.08% fraud"), (475.5, "valid.", "0.42%"), (647, "test", "1.09%")):
    a.text(x, 2.6e6, name, ha="center", va="bottom", fontsize=7)
    a.text(x, 6.5e5, share, ha="center", va="bottom", fontsize=6.3, color=GREY)
box = dict(facecolor="white", edgecolor="none", pad=0.6, alpha=0.9)
a.text(645, 1.3e3, "transactions", fontsize=6.3, color=GREY, ha="center", bbox=box)
a.text(645, 2.0, "fraud", fontsize=6.3, ha="center", bbox=box)
a.set_xlim(0, 745)
a.set_ylim(0.7, 1.2e7)
a.set_xticks([1, 200, 400, 550, 743])
a.set_yticks([1, 1e2, 1e4, 1e6])
a.set_xlabel("hourly step")
a.set_ylabel("count per step")
panel_label(a, "(a) volume, fraud and the split")

# (b) precision-recall on test
pr = c["pr_curves_test"]
for i, s in enumerate(pr["lightgbm_by_seed"]):
    rec, prec = np.array(s["recall"]), np.array(s["precision"])
    keep = rec > 0
    b.plot(rec[keep], prec[keep], color=GREY, lw=0.6, label="LightGBM, 5 seeds" if i == 0 else None)
rules = pr["rules"]
b.plot(rules["recall"][1:], rules["precision"][1:], color=OCHRE, lw=0.8, marker="s", ms=2.4, label="business rule")
lr = pr["logistic_regression"]
b.plot(lr["recall"][1:], lr["precision"][1:], color=BLUE, lw=1.1, label="logistic regression")
for name, xy in (("medium", (0.80, 0.70)), ("high", (0.60, 0.82))):
    m = c["check"]["test_at_thresholds"][name]
    b.plot(m["recall"], m["precision"], marker="o", ms=3.2, mfc="white", mec=BLUE, mew=0.9, lw=0, zorder=4)
    b.annotate(name, (m["recall"], m["precision"]), xytext=xy, fontsize=6.5, ha="center", va="center", color=BLUE,
               arrowprops=dict(arrowstyle="-", lw=0.4, color=BLUE, shrinkA=1, shrinkB=2.5))
b.axhline(c["prevalence_test"], color=INK, lw=0.4, ls=(0, (1, 2)))
b.text(0.02, c["prevalence_test"] + 0.025, "prevalence", fontsize=6.3, color=GREY)
b.set_xlim(0, 1.005)
b.set_ylim(0, 1.03)
b.set_xlabel("recall")
b.set_ylabel("precision")
b.legend(loc="center left", bbox_to_anchor=(0.0, 0.47), handlelength=1.5, borderpad=0, labelspacing=0.25)
panel_label(b, "(b) precision and recall, test")

# (c) reliability
FLOOR = 2e-6
r.set_xscale("log")
r.set_yscale("log")
r.plot([FLOOR, 1], [FLOOR, 1], color=INK, lw=0.4, ls=(0, (1, 2)))
for key, color, marker, label in (("raw", GREY, "o", "raw score"), ("calibrated", BLUE, "D", "calibrated")):
    bins = [x for x in c["reliability_test"][key] if x["mean_predicted"] > 0]
    x = np.array([v["mean_predicted"] for v in bins])
    y = np.array([v["observed"] for v in bins])
    size = np.array([2.2 + 0.9 * np.log10(v["n"]) for v in bins])
    pos = y > 0
    r.scatter(x[pos], y[pos], s=size[pos] ** 2, facecolor=color, edgecolor="white", linewidth=0.3, marker=marker, label=label,
              zorder=3)
    r.scatter(x[~pos], np.full((~pos).sum(), FLOOR * 1.6), s=size[~pos] ** 2, facecolor="white", edgecolor=color,
              linewidth=0.6, marker=marker, zorder=3)
r.set_xlim(FLOOR, 1.6)
r.set_ylim(FLOOR, 1.6)
r.set_xticks([1e-5, 1e-3, 1e-1, 1])
r.set_xticklabels([r"$10^{-5}$", r"$10^{-3}$", r"$10^{-1}$", "1"])
r.set_yticks([FLOOR * 1.6, 1e-5, 1e-3, 1e-1])
r.set_yticklabels(["0", r"$10^{-5}$", r"$10^{-3}$", r"$10^{-1}$"])
r.minorticks_off()
r.set_xlabel("predicted probability (bin mean)")
r.set_ylabel("observed fraud rate")
r.legend(loc="upper left", handletextpad=0.2, borderpad=0, labelspacing=0.25)
panel_label(r, "(c) reliability, test")

fig.savefig(HERE / "fig_scorer.pdf")
print("written", HERE / "fig_scorer.pdf")
