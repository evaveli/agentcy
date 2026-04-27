"""Global scorer — 3 cross-agent metrics for Jordi's comparability requirement.

These metrics are computed uniformly across ALL agents:
1. Correction effort — number of issues requiring manual correction
2. Critical error count — errors that would cause real business problems
3. Task success rate — binary: output usable without major rewrite
"""

from __future__ import annotations

from typing import Any


def correction_effort(per_agent_scores: dict[str, dict]) -> dict[str, int]:
    """Estimate correction effort per agent as an integer count of issues.

    Maps agent-specific metrics to a unified correction count.
    """
    efforts: dict[str, int] = {}

    for agent_name, scores in per_agent_scores.items():
        count = 0

        if agent_name == "email":
            count += scores.get("hallucination_count", 0)
            if not scores.get("response_appropriateness", True):
                count += 1
            # Template issues
            adherence = scores.get("template_adherence_pct", 100)
            if adherence < 60:
                count += 2
            elif adherence < 80:
                count += 1

        elif agent_name == "deal_summary":
            coverage = scores.get("checklist_coverage_pct", 100)
            count += max(0, int((100 - coverage) / 15))  # ~1 correction per 15% missing
            consistency = scores.get("structural_consistency_pct", 100)
            if consistency < 60:
                count += 2
            elif consistency < 80:
                count += 1

        elif agent_name == "necessity_form":
            count += scores.get("critical_error_count", 0)
            error_rate = scores.get("error_rate_pct", 0)
            count += max(0, int(error_rate / 10))  # ~1 correction per 10% error rate

        elif agent_name == "proposal":
            count += scores.get("critical_error_count", 0)
            effort = scores.get("editing_effort", "none")
            effort_map = {"none": 0, "minor": 1, "moderate": 3, "major": 5}
            count += effort_map.get(effort, 0)

        elif agent_name == "warehouse":
            if scores.get("top1_match", 0) == 0:
                count += 1
            constraint_sat = scores.get("hard_constraint_satisfaction_pct", 100)
            if constraint_sat < 100:
                count += max(1, int((100 - constraint_sat) / 25))

        efforts[agent_name] = count

    return efforts


def critical_error_count(per_agent_scores: dict[str, dict]) -> dict[str, int]:
    """Count critical errors per agent — errors that would cause real business problems."""
    errors: dict[str, int] = {}

    for agent_name, scores in per_agent_scores.items():
        count = 0

        if agent_name == "email":
            count += scores.get("hallucination_count", 0)
            entity_acc = scores.get("entity_accuracy_pct", 100)
            if entity_acc < 50:
                count += 1  # Major entity failure

        elif agent_name == "deal_summary":
            coverage = scores.get("checklist_coverage_pct", 100)
            if coverage < 50:
                count += 1  # Missing more than half of key facts

        elif agent_name == "necessity_form":
            count += scores.get("critical_error_count", 0)

        elif agent_name == "proposal":
            count += scores.get("critical_error_count", 0)

        elif agent_name == "warehouse":
            constraint_sat = scores.get("hard_constraint_satisfaction_pct", 100)
            if constraint_sat < 50:
                count += 1  # Recommending warehouses that fail hard constraints

        errors[agent_name] = count

    return errors


def task_success_rate(per_agent_scores: dict[str, dict]) -> dict[str, bool]:
    """Binary per agent: output usable without major rewrite.

    An agent passes if it meets minimum quality thresholds.
    """
    results: dict[str, bool] = {}

    for agent_name, scores in per_agent_scores.items():
        if agent_name == "email":
            results[agent_name] = (
                scores.get("entity_accuracy_pct", 0) >= 60
                and scores.get("hallucination_count", 99) <= 1
                and scores.get("template_adherence_pct", 0) >= 60
            )

        elif agent_name == "deal_summary":
            results[agent_name] = (
                scores.get("checklist_coverage_pct", 0) >= 60
                and scores.get("structural_consistency_pct", 0) >= 50
            )

        elif agent_name == "necessity_form":
            results[agent_name] = (
                scores.get("acceptability_pct", 0) >= 70
                and scores.get("critical_error_count", 99) <= 1
            )

        elif agent_name == "proposal":
            results[agent_name] = (
                scores.get("critical_error_count", 99) <= 2
                and scores.get("editing_effort", "major") in ("none", "minor", "moderate")
                and scores.get("section_completeness_pct", 0) >= 60
            )

        elif agent_name == "warehouse":
            results[agent_name] = (
                scores.get("hard_constraint_satisfaction_pct", 0) >= 50
                and scores.get("top3_match", 0) > 0
            )

        else:
            results[agent_name] = True

    return results


def score_global(per_agent_scores: dict[str, dict]) -> dict[str, Any]:
    """Compute all 3 global metrics.

    Args:
        per_agent_scores: {agent_name: {metric_name: value}} from per-agent scorers

    Returns:
        Dict with correction_effort, critical_error_count, task_success_rate
        per agent and aggregated.
    """
    corrections = correction_effort(per_agent_scores)
    errors = critical_error_count(per_agent_scores)
    successes = task_success_rate(per_agent_scores)

    return {
        "correction_effort_per_agent": corrections,
        "correction_effort_total": sum(corrections.values()),
        "critical_error_count_per_agent": errors,
        "critical_error_count_total": sum(errors.values()),
        "task_success_per_agent": successes,
        "task_success_rate_pct": round(
            sum(1 for v in successes.values() if v) / len(successes) * 100, 1
        ) if successes else 0.0,
    }
