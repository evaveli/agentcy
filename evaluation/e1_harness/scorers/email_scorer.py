"""Email Agent scorer — entity accuracy, hallucination count, template adherence,
edit distance, and response appropriateness."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from evaluation.e1_harness.ground_truth import EmailGroundTruth


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _fuzzy_match(value: str, expected: str, threshold: float = 0.75) -> bool:
    """Check if value is a fuzzy match for expected (handles formatting differences)."""
    nv, ne = _normalize(value), _normalize(expected)
    if ne in nv or nv in ne:
        return True
    return SequenceMatcher(None, nv, ne).ratio() >= threshold


def _extract_structured(output: dict) -> dict | None:
    """Extract structured JSON from agent output."""
    if "structured" in output and isinstance(output["structured"], dict):
        return output["structured"]
    # Try parsing raw_output as JSON
    raw = output.get("raw_output", "")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def entity_accuracy(output: dict, gt: EmailGroundTruth) -> float:
    """Fraction of expected entities correctly present in the output.

    Returns value between 0.0 and 1.0.
    """
    structured = _extract_structured(output)
    if not structured:
        # Fall back to checking raw text
        raw = output.get("raw_output", "")
        total = 0
        matched = 0
        for key, expected in gt.expected_entities.items():
            if isinstance(expected, list):
                for item in expected:
                    total += 1
                    if _normalize(item) in _normalize(raw):
                        matched += 1
            else:
                total += 1
                if _normalize(str(expected)) in _normalize(raw):
                    matched += 1
        return matched / total if total > 0 else 0.0

    entities = structured.get("deal_entities", {})
    total = 0
    matched = 0

    for key, expected in gt.expected_entities.items():
        ai_value = entities.get(key, "")
        if isinstance(expected, list):
            for item in expected:
                total += 1
                if isinstance(ai_value, list):
                    if any(_fuzzy_match(str(av), item) for av in ai_value):
                        matched += 1
                elif _fuzzy_match(str(ai_value), item):
                    matched += 1
        else:
            total += 1
            if _fuzzy_match(str(ai_value), str(expected)):
                matched += 1

    return matched / total if total > 0 else 0.0


def hallucination_count(output: dict, gt: EmailGroundTruth) -> int:
    """Count facts in the email body that must NOT be present (cross-deal contamination)."""
    structured = _extract_structured(output)
    body = ""
    if structured:
        body = structured.get("body", "")
    if not body:
        body = output.get("raw_output", "")

    body_lower = _normalize(body)
    count = 0
    for forbidden in gt.body_must_not_contain:
        if _normalize(forbidden) in body_lower:
            count += 1
    return count


def template_adherence(output: dict, gt: EmailGroundTruth) -> float:
    """Check presence of required email structural elements.

    Expected structure: greeting, context/reference, deal specifics, call-to-action, sign-off.
    Returns fraction of elements present (0.0 to 1.0).
    """
    structured = _extract_structured(output)
    body = ""
    if structured:
        body = structured.get("body", "")
    if not body:
        body = output.get("raw_output", "")

    body_lower = body.lower()
    checks = {
        "greeting": any(
            g in body_lower
            for g in ["dear ", "hello ", "hi ", "good morning", "good afternoon"]
        ),
        "context_reference": any(
            r in body_lower
            for r in ["following", "regarding", "as discussed", "further to",
                       "with reference", "as per", "update on", "I wanted to"]
        ),
        "deal_specifics": any(
            _normalize(item) in _normalize(body)
            for item in gt.body_must_contain
        ),
        "call_to_action": any(
            cta in body_lower
            for cta in ["next step", "please", "could we", "shall we", "would you",
                         "let me know", "looking forward", "schedule", "confirm",
                         "available", "your thoughts"]
        ),
        "sign_off": any(
            s in body_lower
            for s in ["best regards", "kind regards", "sincerely", "regards",
                       "best,", "thank you"]
        ),
    }

    passed = sum(1 for v in checks.values() if v)
    return passed / len(checks)


def edit_distance(output: dict, gt: EmailGroundTruth) -> float:
    """Sentence-level edit distance between AI email and human reference.

    Returns normalized distance (0.0 = identical, 1.0 = completely different).
    """
    structured = _extract_structured(output)
    body = ""
    if structured:
        body = structured.get("body", "")
    if not body:
        body = output.get("raw_output", "")

    # Split into sentences for sentence-level comparison
    def _sentences(text: str) -> list[str]:
        return [s.strip() for s in re.split(r"[.!?\n]+", text) if s.strip()]

    ai_sentences = _sentences(body)
    ref_sentences = _sentences(gt.human_reference_email)

    if not ai_sentences and not ref_sentences:
        return 0.0
    if not ai_sentences or not ref_sentences:
        return 1.0

    # Use SequenceMatcher on sentence lists for structural similarity
    matcher = SequenceMatcher(None, ai_sentences, ref_sentences)
    return 1.0 - matcher.ratio()


def response_appropriateness(output: dict, gt: EmailGroundTruth) -> bool:
    """Binary: does the email match the expected type and stage context?

    Checks:
    - Correct email type
    - References prior interactions when expected
    - No cold-intro tone for existing negotiation-stage deals
    """
    structured = _extract_structured(output)
    if not structured:
        return False

    # Check email type
    ai_type = structured.get("email_type", "").lower().replace("-", "_").replace(" ", "_")
    if gt.email_type not in ai_type and ai_type not in gt.email_type:
        return False

    # Check references_prior matches expectation
    ai_refs = structured.get("references_prior", None)
    if gt.expected_references_prior and ai_refs is False:
        return False

    # Check stage appropriateness: intro tone in negotiation stage is wrong
    body = structured.get("body", "").lower()
    if gt.deal_stage in ("negotiation", "proposal"):
        intro_markers = ["introduce myself", "introduce our company", "i am reaching out for the first time"]
        if any(m in body for m in intro_markers):
            return False

    return True


def score_email(output: dict, gt: EmailGroundTruth) -> dict[str, Any]:
    """Run all email metrics. Returns dict of metric_name -> value."""
    return {
        "entity_accuracy_pct": round(entity_accuracy(output, gt) * 100, 1),
        "hallucination_count": hallucination_count(output, gt),
        "template_adherence_pct": round(template_adherence(output, gt) * 100, 1),
        "edit_distance": round(edit_distance(output, gt), 3),
        "response_appropriateness": response_appropriateness(output, gt),
    }
