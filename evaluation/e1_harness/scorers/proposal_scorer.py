"""Proposal Template Agent scorer — critical errors, editing effort, section completeness."""

from __future__ import annotations

import re
from typing import Any

from evaluation.e1_harness.ground_truth import ProposalGroundTruth


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s.]", "", text.lower()).strip()


def _section_present(output_lower: str, section_name: str) -> bool:
    """Check if a proposal section is present (as heading, bold label, or content block)."""
    section_patterns = {
        "executive_summary": ["executive summary", "summary", "overview"],
        "client_requirements": ["client requirements", "requirements", "client needs", "your requirements"],
        "warehouse_option": ["warehouse", "property", "facility", "recommended option", "suggested"],
        "financial_terms": ["financial terms", "pricing", "commercial terms", "cost", "rent"],
        "location_advantages": ["location", "advantage", "why this", "strategic"],
        "comparison_table": ["comparison", "side-by-side", "option a", "option b"],
        "timeline": ["timeline", "schedule", "milestone", "target date", "next steps"],
        "next_steps": ["next step", "action item", "how to proceed", "moving forward"],
        "compliance_section": ["compliance", "certification", "regulatory", "gdp", "standard"],
        "infrastructure_section": ["infrastructure", "crane", "rail", "equipment", "facility spec"],
    }
    patterns = section_patterns.get(section_name, [section_name.replace("_", " ")])
    return any(p in output_lower for p in patterns)


def critical_error_count(output: str, gt: ProposalGroundTruth) -> int:
    """Count critical factual errors: missing critical facts + present forbidden claims."""
    output_norm = _normalize(output)
    errors = 0

    # Missing critical facts
    for fact_name, expected_value in gt.critical_facts.items():
        value_norm = _normalize(expected_value)
        # Try exact and common variants
        variants = [value_norm]
        if "," in expected_value:
            variants.append(_normalize(expected_value.replace(",", "")))
        if not any(v in output_norm for v in variants):
            errors += 1

    # Forbidden claims present
    for claim in gt.forbidden_claims:
        if _normalize(claim) in output_norm:
            errors += 1

    return errors


def editing_effort(output: str, gt: ProposalGroundTruth) -> str:
    """Estimate editing effort category based on errors and missing sections.

    Returns: 'none' (0 issues), 'minor' (1-2), 'moderate' (3-5), 'major' (6+)
    """
    output_lower = output.lower()
    issues = 0

    # Count critical errors
    issues += critical_error_count(output, gt)

    # Count missing sections
    for section in gt.required_sections:
        if not _section_present(output_lower, section):
            issues += 1

    if issues == 0:
        return "none"
    elif issues <= 2:
        return "minor"
    elif issues <= 5:
        return "moderate"
    else:
        return "major"


def section_completeness(output: str, gt: ProposalGroundTruth) -> float:
    """Fraction of required proposal sections present."""
    output_lower = output.lower()
    total = len(gt.required_sections)
    if total == 0:
        return 1.0

    present = sum(1 for s in gt.required_sections if _section_present(output_lower, s))
    return present / total


def score_proposal(output: str, gt: ProposalGroundTruth) -> dict[str, Any]:
    """Run all proposal metrics."""
    raw = output if isinstance(output, str) else output.get("raw_output", "")
    return {
        "critical_error_count": critical_error_count(raw, gt),
        "editing_effort": editing_effort(raw, gt),
        "section_completeness_pct": round(section_completeness(raw, gt) * 100, 1),
    }
