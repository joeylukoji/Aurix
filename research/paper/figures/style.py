"""Shared matplotlib settings for the paper's plots: Latin Modern to match the text, thin axes, no chart junk."""
from pathlib import Path

import matplotlib as mpl
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

for d in ("/usr/share/texmf/fonts/opentype/public/lm", "/usr/share/fonts/opentype/lmodern"):
    for f in Path(d).glob("lm*.otf"):
        font_manager.fontManager.addfont(str(f))

INK, GREY, LIGHT = "#1a1a1a", "#7a7a7a", "#d9d9d9"
BLUE, RED, OCHRE = "#1f4e79", "#a3261b", "#c08a1e"

mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Latin Modern Roman", "DejaVu Serif"], "mathtext.fontset": "cm",
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.linewidth": 0.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "xtick.minor.width": 0.35, "ytick.minor.width": 0.35, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "xtick.minor.size": 1.5, "ytick.minor.size": 1.5, "xtick.direction": "out", "ytick.direction": "out",
    "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "text.color": INK, "lines.linewidth": 1.0, "legend.frameon": False,
    "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "axes.unicode_minus": False,
})


def panel_label(ax, text):
    ax.text(0.0, 1.04, text, transform=ax.transAxes, ha="left", va="bottom", fontsize=8)
