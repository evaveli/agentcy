"""E3 result collector — aggregates ablation results and generates comparison tables."""

from __future__ import annotations

import json
import statistics
from typing import Any

from evaluation.e3_ablation.ablation_runner import AblationResult
from evaluation.e3_ablation.config_profiles import CONFIG_DESCRIPTIONS


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _fmt(val: float, decimals: int = 1) -> str:
    return f"{val:.{decimals}f}"


def aggregate_results(
    results: dict[str, list[AblationResult]],
) -> dict[str, dict[str, Any]]:
    """Aggregate per-config results into summary statistics."""
    summary = {}

    for config_name, config_results in results.items():
        valid = [r for r in config_results if r.error is None]
        if not valid:
            summary[config_name] = {"error": "All runs failed"}
            continue

        latencies = [r.end_to_end_latency_ms for r in valid]
        violations = [r.ethics_violations_reaching_output for r in valid]
        corrections = [r.manual_corrections for r in valid]
        recovery_rates = [r.recovery_rate for r in valid if r.injected_failures > 0]

        # Aggregate quality scores across deals, per agent
        agent_quality: dict[str, dict[str, list[float]]] = {}
        for r in valid:
            for agent, scores in r.quality_scores.items():
                if agent not in agent_quality:
                    agent_quality[agent] = {}
                for metric, value in scores.items():
                    if isinstance(value, (int, float)):
                        agent_quality[agent].setdefault(metric, []).append(float(value))
                    elif isinstance(value, bool):
                        agent_quality[agent].setdefault(metric, []).append(1.0 if value else 0.0)

        quality_means = {
            agent: {metric: _mean(values) for metric, values in metrics.items()}
            for agent, metrics in agent_quality.items()
        }

        summary[config_name] = {
            "description": CONFIG_DESCRIPTIONS.get(config_name, ""),
            "n_deals": len(valid),
            "mean_latency_ms": _mean(latencies),
            "total_ethics_violations": sum(violations),
            "mean_violations_per_deal": _mean(violations),
            "mean_corrections": _mean(corrections),
            "mean_recovery_rate": _mean(recovery_rates) if recovery_rates else None,
            "quality_per_agent": quality_means,
        }

    return summary


def generate_comparison_table(
    results: dict[str, list[AblationResult]],
) -> str:
    """Generate markdown comparison table across all configs."""
    summary = aggregate_results(results)

    # Main comparison table
    lines = [
        "## E3 Ablation Study — Configuration Comparison",
        "",
        "| Config | Description | N | Latency (ms) | Violations | Corrections | Recovery Rate |",
        "|--------|-------------|---|-------------|------------|-------------|---------------|",
    ]

    baseline = summary.get("full_system", {})
    baseline_latency = baseline.get("mean_latency_ms", 0)

    for config_name, stats in summary.items():
        if "error" in stats:
            lines.append(f"| {config_name} | {stats.get('error', '')} | — | — | — | — | — |")
            continue

        latency = stats["mean_latency_ms"]
        delta = ""
        if baseline_latency > 0 and config_name != "full_system":
            pct = (latency - baseline_latency) / baseline_latency * 100
            delta = f" ({'+' if pct >= 0 else ''}{pct:.0f}%)"

        recovery = f"{stats['mean_recovery_rate']:.0%}" if stats['mean_recovery_rate'] is not None else "—"

        lines.append(
            f"| {config_name} | {stats['description'][:60]} | "
            f"{stats['n_deals']} | {_fmt(latency)}{delta} | "
            f"{stats['total_ethics_violations']} | "
            f"{_fmt(stats['mean_corrections'])} | {recovery} |"
        )

    lines.append("")

    # Per-agent quality comparison
    all_agents = set()
    for stats in summary.values():
        if isinstance(stats, dict) and "quality_per_agent" in stats:
            all_agents.update(stats["quality_per_agent"].keys())

    if all_agents:
        lines.extend([
            "## Per-Agent Quality Across Configurations",
            "",
        ])

        for agent in sorted(all_agents):
            # Collect all metrics for this agent
            all_metrics = set()
            for stats in summary.values():
                if isinstance(stats, dict) and "quality_per_agent" in stats:
                    agent_q = stats["quality_per_agent"].get(agent, {})
                    all_metrics.update(agent_q.keys())

            if not all_metrics:
                continue

            lines.extend([
                f"### {agent.replace('_', ' ').title()}",
                "",
                "| Metric | " + " | ".join(summary.keys()) + " |",
                "|--------|" + "|".join("-----" for _ in summary) + "|",
            ])

            for metric in sorted(all_metrics):
                row = f"| {metric} |"
                for config_name, stats in summary.items():
                    if isinstance(stats, dict) and "quality_per_agent" in stats:
                        val = stats["quality_per_agent"].get(agent, {}).get(metric)
                        row += f" {_fmt(val) if val is not None else '—'} |"
                    else:
                        row += " — |"
                lines.append(row)

            lines.append("")

    return "\n".join(lines)


def generate_full_report(
    results: dict[str, list[AblationResult]],
) -> str:
    """Generate the complete E3 ablation report."""
    sections = [
        "# E3 Ablation Study Report",
        "",
        f"Configurations tested: {len(results)}",
        f"Deals per config: {max(len(v) for v in results.values()) if results else 0}",
        "",
        "---",
        "",
        generate_comparison_table(results),
        "",
        "---",
        "",
        "## Key Findings",
        "",
        "*(Fill in after running the experiments)*",
        "",
        "- **Ethics module removal**: Expected to show violations reaching output when violation inputs are injected",
        "- **Strategist removal**: Expected to show degraded plan quality but potentially lower latency",
        "- **Failure handling removal**: Expected to show lower recovery rate when failures are injected",
        "- **Minimal baseline**: Expected to show worst overall quality but fastest latency",
    ]

    return "\n".join(sections)


def export_json(
    results: dict[str, list[AblationResult]],
) -> str:
    """Export results as JSON for programmatic analysis."""
    data = {}
    for config_name, config_results in results.items():
        data[config_name] = [
            {
                "deal_id": r.deal_id,
                "latency_ms": r.end_to_end_latency_ms,
                "quality_scores": r.quality_scores,
                "violations": r.ethics_violations_reaching_output,
                "recovery_rate": r.recovery_rate,
                "corrections": r.manual_corrections,
                "injected_failures": r.injected_failures,
                "recovered_failures": r.recovered_failures,
                "error": r.error,
            }
            for r in config_results
        ]
    return json.dumps(data, indent=2, default=str)
