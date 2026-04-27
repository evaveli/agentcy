"""Generate Q15 thematic-codes chart for the survey appendix (Fig. themes-missing).

Multi-label coding of 19 Q15 responses against 5 themes (a response may be
assigned to more than one theme). Total mentions = 26.

Per-theme breakdown (Domain N=9, Technical N=10):
  Evidence links / source verification: P1[T], P5[T], P9[D], P13[T], P14[T],
                                        P16[T], P17[T], P19[T]         (1D, 7T, =8)
  Market comparables / local context:   P3[D], P4[D], P6[D], P8[D],
                                        P10[D], P12[D]                 (6D, 0T, =6)
  Confidence levels / uncertainty:      P1[T], P15[T], P16[T], P18[T],
                                        P19[T]                         (0D, 5T, =5)
  Practical details (photos, access):   P4[D], P11[D], P12[D], P13[T]  (3D, 1T, =4)
  Sensitivity / assumptions:            P2[T], P14[T], P18[T]          (0D, 3T, =3)
"""

import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 12,
})

# (label, domain_count, technical_count)
themes = [
    ("Evidence links /\nsource verification", 1, 7),
    ("Market comparables /\nlocal context",   6, 0),
    ("Confidence levels /\nuncertainty",      0, 5),
    ("Practical details\n(photos, access)",   3, 1),
    ("Sensitivity analysis /\nassumptions",   0, 3),
]
themes.sort(key=lambda t: t[1] + t[2])  # ascending so largest ends up on top

labels = [t[0] for t in themes]
dom    = np.array([t[1] for t in themes])
tech   = np.array([t[2] for t in themes])
totals = dom + tech

fig, ax = plt.subplots(figsize=(9.0, 4.8))
y = np.arange(len(labels))

c_dom  = "#6FA8DC"  # soft blue, close to user's original
c_tech = "#E8A87C"  # soft orange, complementary

ax.barh(y, dom,  color=c_dom,  edgecolor="black", linewidth=0.6,
        label="Domain experts (N = 9)")
ax.barh(y, tech, left=dom, color=c_tech, edgecolor="black", linewidth=0.6,
        label="Technical evaluators (N = 10)")

for i, (d, t, tot) in enumerate(zip(dom, tech, totals)):
    if d >= 1:
        ax.text(d / 2, i, str(d), va="center", ha="center",
                fontsize=10, fontweight="bold", color="black")
    if t >= 1:
        ax.text(d + t / 2, i, str(t), va="center", ha="center",
                fontsize=10, fontweight="bold", color="black")
    ax.text(tot + 0.15, i, f"{tot}/19", va="center", ha="left", fontsize=10)

ax.set_yticks(y, labels)
ax.set_xlabel("Number of Mentions (n = 19, multi-label)")
ax.set_title("Thematic Codes: What Is Missing from Outputs (Q15)",
             fontsize=13, pad=10)
ax.set_xlim(0, max(totals) + 1.6)
ax.xaxis.set_major_locator(plt.MultipleLocator(2))
ax.tick_params(axis="y", length=0)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
ax.legend(loc="lower right", frameon=False, fontsize=10)

plt.tight_layout()
out = Path(__file__).parent / "results" / "full_experiment" / "fig6_themes_q15_missing.png"
plt.savefig(out, dpi=200, bbox_inches="tight")
print(f"wrote {out}")
