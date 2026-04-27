"""Deal Summary Agent scorer — checklist coverage and structural consistency."""

from __future__ import annotations

import re
from typing import Any

from evaluation.e1_harness.ground_truth import DealSummaryGroundTruth


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def checklist_coverage(output: str, gt: DealSummaryGroundTruth) -> float:
    """Fraction of required facts present in the summary output.

    Uses case-insensitive substring matching with normalization.
    """
    output_norm = _normalize(output)
    total = len(gt.required_facts)
    if total == 0:
        return 1.0

    matched = 0
    for field_name, expected_value in gt.required_facts.items():
        # Try the value directly, and also common formatting variants
        variants = [expected_value]
        # Handle numbers with/without comma formatting
        if "," in expected_value:
            variants.append(expected_value.replace(",", ""))
        clean = expected_value.replace(",", "")
        if clean.isdigit() and len(clean) > 3:
            variants.append(f"{int(clean):,}")

        if any(_normalize(v) in output_norm for v in variants):
            matched += 1

    return matched / total


def structural_consistency(output: str, gt: DealSummaryGroundTruth) -> float:
    """Fraction of expected structural elements present in the output.

    Checks for:
    - Required sections as markdown headings or labeled paragraphs
    - Structural elements (tables, risk sections, etc.)
    """
    output_lower = output.lower()
    total_checks = len(gt.required_sections) + len(gt.structural_elements)
    if total_checks == 0:
        return 1.0

    passed = 0

    # Check required sections (as headings or bold labels)
    section_patterns = {
        "deal_stage": ["deal stage", "stage", "current stage", "status"],
        "key_contacts": ["contact", "key contact", "stakeholder", "point of contact"],
        "financial_terms": ["financial", "value", "deal value", "pricing", "budget", "terms"],
        "timeline": ["timeline", "schedule", "deadline", "expected close", "target date"],
        "risks": ["risk", "concern", "issue", "challenge", "flag"],
        "next_steps": ["next step", "action item", "recommendation", "follow-up"],
    }
    for section in gt.required_sections:
        patterns = section_patterns.get(section, [section.replace("_", " ")])
        if any(p in output_lower for p in patterns):
            passed += 1

    # Check structural elements
    element_checks = {
        "summary_table": bool(re.search(r"\|.*\|.*\|", output)),  # markdown table
        "risks_section": any(
            r in output_lower for r in ["risk", "concern", "warning", "flag"]
        ),
        "next_steps": any(
            n in output_lower for n in ["next step", "action", "recommend", "follow-up"]
        ),
    }
    for element in gt.structural_elements:
        if element_checks.get(element, False):
            passed += 1

    return passed / total_checks


def score_deal_summary(output: str, gt: DealSummaryGroundTruth) -> dict[str, Any]:
    """Run all deal summary metrics."""
    raw = output if isinstance(output, str) else output.get("raw_output", "")
    return {
        "checklist_coverage_pct": round(checklist_coverage(raw, gt) * 100, 1),
        "structural_consistency_pct": round(structural_consistency(raw, gt) * 100, 1),
    }
