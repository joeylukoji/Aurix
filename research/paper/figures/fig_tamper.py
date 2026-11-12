"""Figure: outcome of every single-bit flip in manifest.sig.json, the one file of a proof package that no hash
in the manifest protects, before and after the fixes.

    python research/paper/figures/fig_tamper.py     (reads ../data/tamper_{before,after}.json, writes fig_tamper.pdf)
"""
import json
import re

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

from style import DATA, GREY, HERE, INK, OCHRE, RED

REJ, EXC, ACC = "#e4e4e4", OCHRE, RED
CODE = {"r": 0, "f": 0, "x": 1, "A": 2}
cmap = ListedColormap([REJ, EXC, ACC])


def strip(ax, row, title):
    sig = row["manifest_sig"]
    text, bits = sig["text"], sig["outcome_per_bit"]
    n = len(bits) // 8
    grid = np.array([CODE[ch] for ch in bits]).reshape(n, 8).T  # rows: bit 0 (least significant) .. bit 7
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=2, aspect="auto", interpolation="nearest", extent=(0, n, 8, 0))
    for y in range(1, 8):
        ax.axhline(y, color="white", lw=0.3)
    ax.set_yticks([0.5, 7.5])
    ax.set_yticklabels(["bit 0", "bit 7"])
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max_len)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_xticks([])
    counts = {k: bits.count(c) for k, c in (("exception", "x"), ("accepted", "A"))}
    ax.text(0, -0.9, f"{title}: {len(bits):,} flips, {counts['accepted']} accepted, {counts['exception']} exceptions",
            ha="left", va="bottom", fontsize=7.5)
    # the value of each field, located in the file text
    for m in re.finditer(r'"(\w+)": ("[^"]*"|\d+)', text):
        lo, hi = m.start(2), m.end(2)
        ax.plot([lo, hi], [8.9, 8.9], color=INK, lw=0.5, clip_on=False)
        for x in (lo, hi):
            ax.plot([x, x], [8.4, 8.9], color=INK, lw=0.5, clip_on=False)
        ax.text((lo + hi) / 2, 9.5, m.group(1), ha="center", va="top", fontsize=6.5, family="Latin Modern Mono", clip_on=False)


before = json.loads((DATA / "tamper_before.json").read_text())[0]
after = json.loads((DATA / "tamper_after.json").read_text())[0]
max_len = max(len(r["manifest_sig"]["outcome_per_bit"]) // 8 for r in (before, after))
fig, axes = plt.subplots(2, 1, figsize=(6.6, 1.5), gridspec_kw={"hspace": 1.25})
strip(axes[0], before, "Before the corrections")
strip(axes[1], after, "After the corrections")
axes[1].set_xticks([0, 50, 100, 150, 200, 250, 300])
axes[1].tick_params(axis="x", pad=12, length=0)
axes[1].set_xlabel("byte offset in manifest.sig.json", labelpad=1)
fig.legend(handles=[Patch(facecolor=REJ, label="rejected with a report"), Patch(facecolor=EXC, label="exception"),
                    Patch(facecolor=ACC, label="accepted")],
           loc="upper right", bbox_to_anchor=(0.905, 1.075), ncol=3, handlelength=1.0, handleheight=0.8, columnspacing=1.4,
           borderpad=0, borderaxespad=0)
fig.savefig(HERE / "fig_tamper.pdf")
print("written", HERE / "fig_tamper.pdf")
