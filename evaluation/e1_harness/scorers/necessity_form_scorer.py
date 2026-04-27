"""Client Necessity Form Agent scorer — field-level accuracy metrics."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from evaluation.e1_harness.ground_truth import NecessityFormGroundTruth


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s.]", "", text.lower()).strip()


def _extract_field_value(output: str, field_name: str) -> str | None:
    """Try to extract a field value from markdown-formatted form output.

    Handles patterns like:
    - **Company Name:** FreshCo Logistics
    - Company Name: FreshCo Logistics
    - | Company Name | FreshCo Logistics |
    """
    # Normalize field name for matching
    label_variants = [
        field_name.replace("_", " "),
        field_name.replace("_", " ").title(),
        field_name,
    ]

    for label in label_variants:
        # Pattern: **Label:** Value or Label: Value
        pattern = rf"(?:\*\*)?{re.escape(label)}(?:\*\*)?[:\s|]+\s*(.+?)(?:\n|\||$)"
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("|").strip()

    return None


def _values_match_exact(extracted: str, expected: Any) -> bool:
    """Exact match (with normalization)."""
    if extracted is None:
        return False
    return _normalize(str(extracted)) == _normalize(str(expected))


def _values_match_acceptable(
    extracted: str,
    expected: Any,
    alternatives: list[str] | None = None,
) -> bool:
    """Accept exact match OR semantically equivalent value."""
    if extracted is None:
        return False
    norm_extracted = _normalize(str(extracted))
    norm_expected = _normalize(str(expected))

    # Exact match
    if norm_extracted == norm_expected:
        return True

    # Substring containment (either direction)
    if norm_expected in norm_extracted or norm_extracted in norm_expected:
        return True

    # Check explicit alternatives
    if alternatives:
        for alt in alternatives:
            if _normalize(alt) in norm_extracted or norm_extracted in _normalize(alt):
                return True

    # Fuzzy match for strings
    if isinstance(expected, str) and len(str(expected)) > 3:
        if SequenceMatcher(None, norm_extracted, norm_expected).ratio() >= 0.80:
            return True

    # Numeric tolerance (within 5%)
    try:
        num_ext = float(re.sub(r"[^\d.]", "", str(extracted)))
        num_exp = float(re.sub(r"[^\d.]", "", str(expected)))
        if num_exp > 0 and abs(num_ext - num_exp) / num_exp <= 0.05:
            return True
    except (ValueError, ZeroDivisionError):
        pass

    return False


def score_necessity_form(output: str, gt: NecessityFormGroundTruth) -> dict[str, Any]:
    """Run all necessity form metrics.

    Returns:
        field_exact_match_pct: % of fields matching exactly
        acceptability_pct: % of fields matching exactly OR acceptably
        error_rate_pct: % of fields wrong or missing
        critical_error_count: errors in critical fields
    """
    raw = output if isinstance(output, str) else output.get("raw_output", "")

    total_fields = len(gt.expected_fields)
    exact_matches = 0
    acceptable_matches = 0
    critical_errors = 0

    for field_name, expected_value in gt.expected_fields.items():
        extracted = _extract_field_value(raw, field_name)

        # Get alternatives for this field
        alternatives = gt.acceptable_alternatives.get(field_name, [])

        is_exact = _values_match_exact(extracted, expected_value)
        is_acceptable = _values_match_acceptable(extracted, expected_value, alternatives)

        if is_exact:
            exact_matches += 1
            acceptable_matches += 1
        elif is_acceptable:
            acceptable_matches += 1
        else:
            # This is an error — check if critical
            if field_name in gt.critical_fields:
                critical_errors += 1

    errors = total_fields - acceptable_matches

    return {
        "field_exact_match_pct": round(exact_matches / total_fields * 100, 1) if total_fields else 0.0,
        "acceptability_pct": round(acceptable_matches / total_fields * 100, 1) if total_fields else 0.0,
        "error_rate_pct": round(errors / total_fields * 100, 1) if total_fields else 0.0,
        "critical_error_count": critical_errors,
    }
