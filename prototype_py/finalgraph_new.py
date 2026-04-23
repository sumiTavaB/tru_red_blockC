import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# =========================================================
# 1) DATA
# =========================================================
alpha = np.array([0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0])
beta  = 1.0 - alpha

tps      = np.array([8.4215,9.1733,9.1027,9.1145,8.2586,8.9896,9.0040,8.5891,8.8674,8.6340])
latency  = np.array([2.0375,1.9963,2.2126,2.1094,2.5515,2.4185,2.4766,2.7031,2.6297,2.2546])
jain     = np.array([0.9992,0.9604,0.9502,0.9495,0.9645,0.9666,0.9603,0.9677,0.9617,0.9642])
std_dev  = np.array([20.15,148.21,167.16,168.43,140.04,135.75,148.55,133.37,145.66,140.65])

# =========================================================
# 2) NORMALIZATION
# =========================================================
def normalize(x):
    x = np.asarray(x, dtype=float)
    den = x.max() - x.min()
    return np.zeros_like(x) if den == 0 else (x - x.min()) / den

z_tps       = normalize(tps)
z_latency   = normalize(latency)
z_jain      = normalize(jain)
z_stability = 1 - normalize(std_dev)

# =========================================================
# 3) STYLE (bold labels/titles, slim lines, 1200 dpi export)
# =========================================================
mpl.rcParams.update({
    "figure.dpi": 160,           # display dpi (interactive)
    "savefig.dpi": 1200,         # <<< REQUIRED export dpi
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",  # plot titles bold
    "axes.labelsize": 11,
    "axes.labelweight": "bold",  # axis labels bold
    "axes.linewidth": 0.8,
    "legend.fontsize": 9,
    "legend.title_fontsize": 9
})

COL = {
    "fair": "#1f77b4",
    "stab": "#9467bd",
    "tps":  "#2ca02c",
    "lat":  "#d62728",
    "guide": "#444444"
}

# =========================================================
# 4) FIGURE LAYOUT (constrained_layout avoids overlaps)
# =========================================================
fig = plt.figure(figsize=(15, 8), constrained_layout=True)
gs = GridSpec(2, 3, figure=fig, width_ratios=[1.22, 1, 1], wspace=0.25, hspace=0.25)

# =========================================================
# 5) 3D OVERVIEW (smaller, shifted left; slim lines)
# =========================================================
ax3d = fig.add_subplot(gs[:, 0], projection="3d")

# Scale down ~2 "points" (approx) by shrinking the axes box a bit
pos = ax3d.get_position()
ax3d.set_position([
    pos.x0 - 0.085,   # ← moved further left (clear visual gap)
    pos.y0 + 0.015,
    pos.width * 0.88, # slightly smaller
    pos.height * 0.90
])

ax3d.set_title(
    "Consensus performance\nacross $(\\alpha, \\beta)$",
    pad=18,
    loc='center'
)


def plot3d(x, y, z, color, label):
    ax3d.plot(
        x, y, z,
        color=color,
        linewidth=0.95,         # <<< slim
        marker="o",
        markersize=3.2,
        markerfacecolor="white",
        markeredgewidth=0.9,
        label=label
    )

plot3d(alpha, beta, z_jain,      COL["fair"], "Fairness")
plot3d(alpha, beta, z_stability, COL["stab"], "Stability")
plot3d(alpha, beta, z_tps,       COL["tps"],  "Throughput")
plot3d(alpha, beta, z_latency,   COL["lat"],  "Latency")

ax3d.set_xlabel("$\\alpha$", labelpad=10, fontweight="bold")
ax3d.set_ylabel("$\\beta$", labelpad=10, fontweight="bold")
ax3d.set_zlabel("Normalized score", labelpad=8, fontweight="bold")

ax3d.set_xlim(0, 1.05)
ax3d.set_ylim(1.05, 0)
ax3d.set_zlim(0, 1.05)

# Keep ticks readable (and avoid crowding)
ax3d.set_xticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
ax3d.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
ax3d.set_zticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])

ax3d.grid(True, linestyle="--", alpha=0.22)

# Low-ish view + "a little more towards the right"
# (increase azim from -170 to -155)
ax3d.view_init(elev=18, azim=-170)

# Legend: bold labels, moved a bit so it doesn't overlap title/axes
leg3d = ax3d.legend(
    loc="upper center",
    bbox_to_anchor=(0.5, -0.12),  # below the 3D plot
    ncol=2,
    frameon=False
)
for t in leg3d.get_texts():
    t.set_fontweight("bold")

# =========================================================
# 6) 2D PANELS (normalized only, bold titles/labels, slim lines)
# =========================================================
def style2d(ax, title, better=None):
    ax.set_title(title, pad=8, fontweight="bold")
    ax.set_xlim(0.08, 1.02)

    # fewer x ticks (prevents label crowding)
    ax.set_xticks([0.1, 0.3, 0.5, 0.7, 0.9, 1.0])

    ax.grid(True, linestyle="--", alpha=0.22)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.axvline(0.1, linestyle=":", linewidth=0.9, color=COL["guide"], alpha=0.7)

    ax.set_xlabel("$\\alpha$", fontweight="bold")
    ax.set_ylabel("Normalized", fontweight="bold")

    if better:
        ax.text(
            0.985, 0.06, better,
            transform=ax.transAxes,
            ha="right", va="bottom",
            fontsize=8,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor=(0,0,0,0.12))
        )

def plot2d(ax, y, color):
    ax.plot(
        alpha, y,
        color=color,
        linewidth=1.05,     # <<< slim
        marker="o",
        markersize=3.4
    )

# Fairness
ax1 = fig.add_subplot(gs[0, 1])
style2d(ax1, "Fairness vs $\\alpha$", "Higher is better")
plot2d(ax1, z_jain, COL["fair"])

# Stability
ax2 = fig.add_subplot(gs[0, 2])
style2d(ax2, "Stability vs $\\alpha$", "Higher is better")
plot2d(ax2, z_stability, COL["stab"])

# Throughput
ax3 = fig.add_subplot(gs[1, 1])
style2d(ax3, "Throughput vs $\\alpha$", "Higher is better")
plot2d(ax3, z_tps, COL["tps"])

# Latency
ax4 = fig.add_subplot(gs[1, 2])
style2d(ax4, "Latency vs $\\alpha$", "Lower is better")
plot2d(ax4, z_latency, COL["lat"])

# A little extra padding safeguard for any backend weirdness
fig.set_constrained_layout_pads(w_pad=0.03, h_pad=0.03, wspace=0.05, hspace=0.05)

# =========================================================
# 7) SAVE (1200 DPI)
# =========================================================
fig.savefig("Fig9_Clean_Publication_1200dpi.png", dpi=1200, bbox_inches="tight", pad_inches=0.10)
fig.savefig("Fig9_Clean_Publication_1200dpi.pdf", bbox_inches="tight", pad_inches=0.10)

print("✅ Saved at 1200 DPI: Fig9_Clean_Publication_1200dpi.png and .pdf")

plt.show()
