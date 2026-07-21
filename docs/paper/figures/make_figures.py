"""Generate the four data figures for the OpenEarth v2 paper.

Every number is read from committed repo artifacts — nothing is invented:
  - packages/core/src/openearth/methane/data/ch4_lut_v5.npz
  - scripts/data/calibration_baseline_v5.json
  - packages/api/src/openearth_api/data/noise_floor_v1.json
  - scripts/data/ml_eval_v2.json
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

# Okabe-Ito subset, validated CVD-safe on a white surface.
BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
INK = "#1a1a1a"
MUTED = "#595959"
GRID = "#d9d9d9"

mpl.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["STIX Two Text", "Times New Roman", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8.5,
        "axes.labelsize": 8.5,
        "axes.titlesize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 7.8,
        "axes.linewidth": 0.6,
        "axes.edgecolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "legend.frameon": False,
        "pdf.fonttype": 42,
        "figure.dpi": 150,
    }
)


def despine(ax, keep=("left", "bottom")):
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(side in keep)


# ---------------------------------------------------------------- Fig 1: LUT
lut = np.load(REPO / "packages/core/src/openearth/methane/data/ch4_lut_v5.npz")
dom, amf = lut["delta_omega"], lut["amf"]
m_a, m_b = lut["m_s2a"], lut["m_s2b"]


def m_at_amf(m, target):
    """Linear interpolation of the LUT along its AMF axis (matches conversion.py)."""
    i = np.searchsorted(amf, target) - 1
    i = np.clip(i, 0, len(amf) - 2)
    w = (target - amf[i]) / (amf[i + 1] - amf[i])
    return (1 - w) * m[i] + w * m[i + 1]


ANCHOR_AMF = 1.0 / np.cos(np.deg2rad(40.0)) + 1.0  # Varon anchor geometry, ≈2.305
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.2, 2.75), constrained_layout=True)

for ax in (ax1, ax2):
    ax.grid(axis="both", color=GRID, linewidth=0.4)
    ax.set_axisbelow(True)
    despine(ax)

# (a) full curves with an AMF 2→4 envelope per spacecraft
for m, color in ((m_a, BLUE), (m_b, ORANGE)):
    ax1.fill_between(dom, m[0], m[-1], color=color, alpha=0.18, linewidth=0)
    ax1.plot(dom, m_at_amf(m, 3.0), color=color, linewidth=1.4)
ax1.axhline(0, color=MUTED, linewidth=0.5)
ax1.set_xlabel(r"$\Delta\Omega$ (mol m$^{-2}$)")
ax1.set_ylabel(r"band signal $m_{\mathrm{MBSP}}$")
ax1.set_xlim(-0.5, 6.0)
ax1.text(3.4, -0.088, "Sentinel-2A", color=BLUE, fontsize=8)
ax1.text(3.4, -0.028, "Sentinel-2B", color=ORANGE, fontsize=8)
ax1.text(
    5.85,
    0.008,
    "AMF 2–4 envelope,\ncentre line AMF 3",
    fontsize=6.8,
    color=MUTED,
    ha="right",
    va="bottom",
)
ax1.set_title("(a) LUT v5 forward curves", fontsize=8.5, loc="left")

# (b) zoom with the Varon anchor comparison at AMF≈2.305, ΔΩ = 0.65
zoom = (dom >= -0.1) & (dom <= 1.5)
for m, color in ((m_a, BLUE), (m_b, ORANGE)):
    ax2.plot(dom[zoom], m_at_amf(m, ANCHOR_AMF)[zoom], color=color, linewidth=1.4)
ours_a = float(np.interp(0.65, dom, m_at_amf(m_a, ANCHOR_AMF)))
ours_b = float(np.interp(0.65, dom, m_at_amf(m_b, ANCHOR_AMF)))
ax2.plot([0.65], [ours_a], "o", color=BLUE, ms=5, zorder=5)
ax2.plot([0.65], [ours_b], "o", color=ORANGE, ms=5, zorder=5)
ax2.plot([0.65], [-0.029], "o", mfc="none", mec=BLUE, ms=5, mew=1.1, zorder=5)
ax2.plot([0.65], [-0.022], "o", mfc="none", mec=ORANGE, ms=5, mew=1.1, zorder=5)
ax2.axvline(0.65, color=MUTED, linewidth=0.5, linestyle=(0, (2, 2)))
ax2.annotate(
    "this work",
    xy=(0.68, ours_a),
    xytext=(0.86, -0.0435),
    fontsize=7.2,
    color=INK,
    arrowprops=dict(arrowstyle="-", linewidth=0.5, color=MUTED),
)
ax2.annotate(
    "Varon et al. (2021)",
    xy=(0.68, -0.0225),
    xytext=(0.86, -0.012),
    fontsize=7.2,
    color=INK,
    arrowprops=dict(arrowstyle="-", linewidth=0.5, color=MUTED),
)
ax2.set_xlabel(r"$\Delta\Omega$ (mol m$^{-2}$)")
ax2.set_xlim(-0.1, 1.5)
ax2.set_title("(b) anchor geometry (AMF $\\approx$ 2.305)", fontsize=8.5, loc="left")

fig.savefig(OUT / "fig_lut.pdf")
plt.close(fig)
print("fig_lut.pdf", f"anchor ours: S2A {ours_a:.4f}, S2B {ours_b:.4f}")

# ------------------------------------------------- Fig 2: calibration scatter
with open(REPO / "scripts/data/calibration_baseline_v5.json") as f:
    cal = json.load(f)
events = [e for e in cal["events"] if not e["excluded"] and e.get("q_ours_t_h")]
agg = cal["aggregates"]

fig, ax = plt.subplots(figsize=(4.4, 4.2), constrained_layout=True)
lims = (1.0, 120.0)
xs = np.geomspace(*lims, 50)
ax.fill_between(xs, xs / 2, xs * 2, color="#000000", alpha=0.06, linewidth=0)
ax.plot(xs, xs, color=MUTED, linewidth=0.7)
ax.plot(xs, xs / 5, color=MUTED, linewidth=0.5, linestyle=(0, (2, 2)))
ax.plot(xs, xs * 5, color=MUTED, linewidth=0.5, linestyle=(0, (2, 2)))
ax.text(80, 100, "1:1", fontsize=7, color=MUTED, rotation=45, ha="center", va="bottom")
ax.text(55, 33, r"$\times/\div\,2$", fontsize=7, color=MUTED, rotation=45, ha="center")

# Single series: the reference-contamination diagnostic is deliberately
# conservative and fires on 12/13 events, so it cannot discriminate here.
for e in events:
    ax.errorbar(
        e["published_q_t_h"],
        e["q_ours_t_h"],
        yerr=e.get("sigma_ours_t_h"),
        fmt="o",
        ms=5,
        mfc=BLUE,
        mec=BLUE,
        ecolor=BLUE,
        elinewidth=0.7,
        capsize=0,
        alpha=0.9,
        zorder=4,
    )

labels = {
    "libya-sirte-2020-01-21": ("libya-sirte", (16, 1.15), "left"),
    "gulf-of-suez-2023-09-20": ("gulf-of-suez", (24, 62), "right"),
    "korpezhe-2018-06-19": ("korpezhe", (12.3, 8.0), "left"),
    "kazakhstan-almaty-2019-09-18": ("kazakhstan-almaty", (11.5, 32), "left"),
}
for e in events:
    if e["id"] in labels:
        text, (tx, ty), ha = labels[e["id"]]
        ax.annotate(
            text,
            xy=(e["published_q_t_h"], e["q_ours_t_h"]),
            xytext=(tx, ty),
            fontsize=7,
            color=MUTED,
            ha=ha,
            arrowprops=dict(arrowstyle="-", linewidth=0.45, color=GRID),
        )

ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlim(*lims)
ax.set_ylim(*lims)
ax.set_aspect("equal")
ax.set_xlabel("published rate (t h$^{-1}$)")
ax.set_ylabel("retrieved rate, this work (t h$^{-1}$)")
despine(ax)
ax.grid(color=GRID, linewidth=0.4)
ax.set_axisbelow(True)
fig.savefig(OUT / "fig_calibration.pdf")
plt.close(fig)
n_cont = sum("possible_reference_contamination" in (e.get("flags") or []) for e in events)
print("fig_calibration.pdf", f"{len(events)} events plotted, {n_cont} contaminated-flag")

# ---------------------------------------------------- Fig 3: noise floor strip
with open(REPO / "packages/api/src/openearth_api/data/noise_floor_v1.json") as f:
    nf = json.load(f)
sites = nf["sites"]
order = sorted(sites, key=lambda s: (sites[s]["floor_kg_h"] is None, sites[s]["floor_kg_h"] or 0))
pooled = nf["global"]["floor_kg_h"] / 1000.0

fig, ax = plt.subplots(figsize=(5.8, 2.7), constrained_layout=True)
for i, s in enumerate(order):
    d = sites[s]
    qs = np.array(d["q_noise_kg_h"]) / 1000.0
    if len(qs):
        ax.plot(qs, np.full(len(qs), i), "o", ms=3.4, mfc="none", mec=BLUE, mew=0.8, alpha=0.75)
        ax.plot([d["floor_kg_h"] / 1000.0], [i], "D", ms=5.5, color=BLUE, zorder=5)
    else:
        ax.text(3.2, i, "no detection on any pair", fontsize=7.2, color=MUTED, va="center")
    rate = f"{int(d['detect_rate'] * d['n_pairs'])}/{d['n_pairs']}"
    ax.text(122, i, rate, fontsize=7.2, color=MUTED, va="center", ha="left")

ax.axvline(pooled, color=ORANGE, linewidth=1.0, linestyle=(0, (4, 2)))
ax.text(
    pooled * 1.06,
    len(order) - 0.42,
    f"pooled floor {pooled:.1f} t h$^{{-1}}$",
    fontsize=7.4,
    color=ORANGE,
)
ax.set_yticks(range(len(order)))
ax.set_yticklabels([s.split(",")[0].replace(" (USA)", "") for s in order])
ax.set_xscale("log")
ax.set_xlim(3, 120)
ax.set_xlabel("apparent emission rate on plume-free pairs (t h$^{-1}$)")
ax.text(122, len(order) - 0.05, "detect\nrate", fontsize=7.2, color=MUTED, ha="left", va="bottom")
despine(ax, keep=("bottom",))
ax.tick_params(axis="y", length=0)
ax.grid(axis="x", color=GRID, linewidth=0.4)
ax.set_axisbelow(True)
ax.legend(
    handles=[
        Line2D(
            [], [], marker="o", ls="none", ms=3.4, mfc="none", mec=BLUE, label="individual pair"
        ),
        Line2D([], [], marker="D", ls="none", ms=5.5, color=BLUE, label="site floor (median)"),
    ],
    loc="upper left",
    bbox_to_anchor=(0.0, 1.17),
    ncol=2,
    handletextpad=0.4,
)
fig.savefig(OUT / "fig_noise_floor.pdf")
plt.close(fig)
print("fig_noise_floor.pdf")

# --------------------------------------------------------- Fig 4: ML dumbbells
with open(REPO / "scripts/data/ml_eval_v2.json") as f:
    ml = json.load(f)
folds = ml["folds"]
rows = [(f"fold {f['fold']}", f["baseline_k2"]["f1"], f["model"]["f1"]) for f in folds]
rows.append(("mean", ml["aggregate"]["baseline_k2_f1"], ml["aggregate"]["model_scene_f1"]))

fig, ax = plt.subplots(figsize=(5.2, 2.35), constrained_layout=True)
ys = np.arange(len(rows))[::-1]
for y, (name, b, m) in zip(ys, rows, strict=True):
    bold = name == "mean"
    ax.plot([b, m], [y, y], color=GRID if not bold else MUTED, linewidth=1.1, zorder=2)
    ax.plot([b], [y], "o", ms=6 if bold else 5, color=ORANGE, zorder=4)
    ax.plot([m], [y], "o", ms=6 if bold else 5, color=BLUE, zorder=4)
    if bold:
        ax.text(b - 0.018, y, f"{b:.3f}", fontsize=7.4, color=ORANGE, ha="right", va="center")
        ax.text(m + 0.018, y, f"{m:.3f}", fontsize=7.4, color=BLUE, ha="left", va="center")
ax.set_yticks(ys)
ax.set_yticklabels([r[0] for r in rows], fontsize=8, fontweight="normal")
ax.set_xlim(0.25, 0.78)
ax.set_xlabel("scene-level F1 on held-out site clusters")
despine(ax, keep=("bottom",))
ax.tick_params(axis="y", length=0)
ax.grid(axis="x", color=GRID, linewidth=0.4)
ax.set_axisbelow(True)
ax.legend(
    handles=[
        Line2D(
            [],
            [],
            marker="o",
            ls="none",
            ms=5,
            color=ORANGE,
            label=r"physics baseline ($-\Delta R_{\mathrm{MBMP}}$, $k=2$)",
        ),
        Line2D(
            [], [], marker="o", ls="none", ms=5, color=BLUE, label="U-Net (inner-val threshold)"
        ),
    ],
    loc="upper left",
    bbox_to_anchor=(0.0, 1.14),
    ncol=2,
    handletextpad=0.4,
)
fig.savefig(OUT / "fig_ml_eval.pdf")
plt.close(fig)
print("fig_ml_eval.pdf")
