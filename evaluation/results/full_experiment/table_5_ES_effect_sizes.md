## Table 5.ES: Effect Size Analysis -- Addressing Statistical Power

### Why Wilcoxon Tests Show Non-Significance

The Wilcoxon signed-rank tests compare C0 against each ablation using **per-client mean accuracy** as paired observations (N=5 pairs). With only 5 pairs and highly skewed accuracy distributions (most configs achieve >85% on 3-4 clients), the test lacks statistical power to detect differences. This is a **sample size limitation**, not evidence of equivalence.

To supplement the hypothesis tests, we report **Cliff's delta** (a non-parametric effect size) and **per-run accuracy differences** using all N=50 runs per config.

### Per-Config Effect Sizes (Cliff's Delta)

| Config | Component Removed | C0 ACC | Cx ACC | Delta (pp) | Cliff's d | Interpretation |
|--------|-------------------|--------|--------|-----------|-----------|----------------|
| C1 | Pheromone adaptation | 0.98 | 0.98 | +0.00 | +0.000 | Negligible |
| C2 | ICNP bidding | 0.98 | 0.42 | +0.56 | +0.774 | Large |
| C3 | SHACL validation | 0.98 | 0.85 | +0.13 | +0.260 | Small |
| C4 | Compliance agent | 0.98 | 0.90 | +0.08 | +0.160 | Small |
| C3+C4 | SHACL + compliance | 0.98 | 0.98 | +0.00 | +0.000 | Negligible |
| C5 | LLM strategist | 0.98 | 0.95 | +0.03 | +0.060 | Negligible |
| C6 | Ethics checker | 0.98 | 0.91 | +0.07 | +0.121 | Negligible |
| C7 | All coordination | 0.98 | 0.50 | +0.48 | +0.576 | Large |

### Interpretation Guide
- |d| < 0.147: Negligible effect
- 0.147 <= |d| < 0.33: Small effect
- 0.33 <= |d| < 0.474: Medium effect
- |d| >= 0.474: Large effect

**Key finding**: ICNP bidding (C2, d=+0.774) and full coordination (C7) show **large** effect sizes despite non-significant Wilcoxon p-values. The non-significance reflects low statistical power (N=5 paired observations), not absence of meaningful differences.