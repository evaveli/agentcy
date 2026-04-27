"""Unified report generator for E1 evaluation results.

Outputs markdown tables (thesis-ready), JSON sidecar, and optional LaTeX.
"""

from __future__ import annotations

import json
import statistics
from typing import Any, Literal


def _mean(values: list[float | int]) -> float:
    return statistics.mean(values) if values else 0.0


def _stdev(values: list[float | int]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _fmt(val: float, decimals: int = 1) -> str:
    return f"{val:.{decimals}f}"


def _aggregate_scores(
    scores_list: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Compute mean and stdev per metric across test cases."""
    if not scores_list:
        return {}

    metrics = scores_list[0].keys()
    result = {}
    for metric in metrics:
        values = []
        for scores in scores_list:
            val = scores.get(metric)
            if isinstance(val, bool):
                values.append(1.0 if val else 0.0)
            elif isinstance(val, (int, float)):
                values.append(float(val))
            # Skip string metrics for aggregation
        if values:
            result[metric] = {"mean": _mean(values), "stdev": _stdev(values), "n": len(values)}

    return result


def generate_per_agent_table(
    agent_name: str,
    scores_list: list[dict[str, Any]],
) -> str:
    """Generate a markdown table for one agent's metrics across test cases."""
    agg = _aggregate_scores(scores_list)
    if not agg:
        return f"### {agent_name}\n\nNo results.\n"

    lines = [
        f"### {agent_name.replace('_', ' ').title()}",
        "",
        "| Metric | Mean | Std Dev | N |",
        "|--------|------|---------|---|",
    ]
    for metric, stats in agg.items():
        lines.append(
            f"| {metric} | {_fmt(stats['mean'])} | {_fmt(stats['stdev'])} | {int(stats['n'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_global_table(global_scores: dict[str, Any]) -> str:
    """Generate markdown table for global cross-agent metrics."""
    lines = [
        "### Global Cross-Agent Metrics",
        "",
    ]

    # Correction effort per agent
    corrections = global_scores.get("correction_effort_per_agent", {})
    if corrections:
        lines.extend([
            "**Correction Effort**",
            "",
            "| Agent | Corrections Needed |",
            "|-------|--------------------|",
        ])
        for agent, count in corrections.items():
            lines.append(f"| {agent} | {count} |")
        lines.append(f"| **Total** | **{global_scores.get('correction_effort_total', 0)}** |")
        lines.append("")

    # Critical errors per agent
    errors = global_scores.get("critical_error_count_per_agent", {})
    if errors:
        lines.extend([
            "**Critical Errors**",
            "",
            "| Agent | Critical Errors |",
            "|-------|-----------------|",
        ])
        for agent, count in errors.items():
            lines.append(f"| {agent} | {count} |")
        lines.append(f"| **Total** | **{global_scores.get('critical_error_count_total', 0)}** |")
        lines.append("")

    # Task success per agent
    successes = global_scores.get("task_success_per_agent", {})
    if successes:
        lines.extend([
            "**Task Success Rate**",
            "",
            "| Agent | Usable Without Major Rewrite |",
            "|-------|------------------------------|",
        ])
        for agent, ok in successes.items():
            lines.append(f"| {agent} | {'Yes' if ok else 'No'} |")
        lines.append(
            f"| **Overall** | **{global_scores.get('task_success_rate_pct', 0)}%** |"
        )
        lines.append("")

    return "\n".join(lines)


def generate_comparison_table(
    ai_scores: dict[str, list[dict]],
    human_scores: dict[str, list[dict]],
) -> str:
    """Side-by-side AI vs Human comparison table for paired evaluation."""
    lines = [
        "### AI vs Human Output Comparison",
        "",
        "| Agent | Metric | AI (Mean) | Human (Mean) | Delta | p-value* |",
        "|-------|--------|-----------|--------------|-------|----------|",
    ]

    for agent_name in ai_scores:
        ai_agg = _aggregate_scores(ai_scores.get(agent_name, []))
        human_agg = _aggregate_scores(human_scores.get(agent_name, []))

        for metric in ai_agg:
            ai_mean = ai_agg[metric]["mean"]
            hu_mean = human_agg.get(metric, {}).get("mean", 0.0)
            delta = ai_mean - hu_mean
            lines.append(
                f"| {agent_name} | {metric} | {_fmt(ai_mean)} | {_fmt(hu_mean)} | "
                f"{'+' if delta >= 0 else ''}{_fmt(delta)} | — |"
            )

    lines.append("")
    lines.append("*p-values require scipy; compute with `scipy.stats.wilcoxon` on paired samples.*")
    lines.append("")
    return "\n".join(lines)


def generate_report(
    per_agent_scores: dict[str, list[dict[str, Any]]],
    global_scores: dict[str, Any],
    human_scores: dict[str, list[dict]] | None = None,
    output_format: Literal["markdown", "json"] = "markdown",
) -> str:
    """Generate a full evaluation report.

    Args:
        per_agent_scores: {agent_name: [scores_dict_per_test_case, ...]}
        global_scores: output from global_scorer.score_global()
        human_scores: optional paired human scores for comparison
        output_format: 'markdown' or 'json'
    """
    if output_format == "json":
        return json.dumps(
            {
                "per_agent": {
                    name: _aggregate_scores(scores)
                    for name, scores in per_agent_scores.items()
                },
                "global": global_scores,
            },
            indent=2,
            default=str,
        )

    # Markdown report
    sections = [
        "# E1 Evaluation Report — Agent Output Quality",
        "",
        f"Test cases per agent: {max(len(v) for v in per_agent_scores.values()) if per_agent_scores else 0}",
        "",
        "---",
        "",
        "## Per-Agent Metrics",
        "",
    ]

    for agent_name, scores_list in per_agent_scores.items():
        sections.append(generate_per_agent_table(agent_name, scores_list))

    sections.extend([
        "---",
        "",
        "## Global Metrics",
        "",
        generate_global_table(global_scores),
    ])

    if human_scores:
        sections.extend([
            "---",
            "",
            "## Paired Comparison (AI vs Human)",
            "",
            generate_comparison_table(per_agent_scores, human_scores),
        ])

    return "\n".join(sections)
