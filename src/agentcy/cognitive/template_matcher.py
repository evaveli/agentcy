"""
Template matching engine.

Scores how well an agent template matches a workflow step extracted from
a natural-language business description.  Uses the same weighted-scoring
approach as ``cnp_utils.score_bid`` and ``agent_utils.score_agent_for_task``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TypedDict

from agentcy.semantic.capability_taxonomy import expand_capabilities


class WorkflowStep(TypedDict, total=False):
    """A single workflow step extracted from a NL description."""
    step_id: str
    description: str
    inferred_capabilities: List[str]
    inferred_tags: List[str]
    dependencies: List[str]
    is_entry: bool
    is_final: bool


class TemplateMatch(TypedDict):
    """Result of matching a step to a template."""
    step_id: str
    template_id: str
    template_name: str
    confidence: float
    capability_overlap: float
    tag_overlap: float
    keyword_score: float


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Tunable weights
_W_CAPABILITY = _env_float("TEMPLATE_MATCH_W_CAPABILITY", 0.55)
_W_TAG = _env_float("TEMPLATE_MATCH_W_TAG", 0.15)
_W_KEYWORD = _env_float("TEMPLATE_MATCH_W_KEYWORD", 0.30)


def _capability_overlap(
    step_capabilities: List[str],
    template_capabilities: List[str],
) -> float:
    """Jaccard-style overlap: |intersection| / |step_capabilities|.

    Template capabilities are expanded via the capability hierarchy so
    that a template with ``file_read`` also matches a step requiring
    ``data_read`` (its parent).
    """
    if not step_capabilities:
        return 0.0
    step_set = {c.lower().strip() for c in step_capabilities}
    tmpl_set = expand_capabilities({c.lower().strip() for c in template_capabilities})
    overlap = len(step_set & tmpl_set)
    return overlap / len(step_set) if step_set else 0.0


def _tag_overlap(
    step_tags: List[str],
    template_tags: List[str],
) -> float:
    """Tag intersection ratio."""
    if not step_tags:
        return 0.0
    step_set = {t.lower().strip() for t in step_tags}
    tmpl_set = {t.lower().strip() for t in template_tags}
    overlap = len(step_set & tmpl_set)
    return overlap / len(step_set) if step_set else 0.0


def _keyword_score(
    step_description: str,
    template_keywords: List[str],
) -> float:
    """Fraction of template keywords found in the step description."""
    if not template_keywords:
        return 0.0
    desc_lower = step_description.lower()
    hits = sum(1 for kw in template_keywords if kw.lower() in desc_lower)
    return hits / len(template_keywords)


def score_template_for_step(
    step: WorkflowStep,
    template: Dict[str, Any],
) -> float:
    """Score a single template against a workflow step.  Returns 0.0 – 1.0."""
    cap = _capability_overlap(
        step.get("inferred_capabilities", []),
        template.get("capabilities", []),
    )
    tag = _tag_overlap(
        step.get("inferred_tags", []),
        template.get("tags", []),
    )
    kw = _keyword_score(
        step.get("description", ""),
        template.get("keywords", []),
    )
    return min(1.0, (_W_CAPABILITY * cap) + (_W_TAG * tag) + (_W_KEYWORD * kw))


def match_step_to_templates(
    step: WorkflowStep,
    templates: List[Dict[str, Any]],
    min_score: float = 0.0,
) -> List[TemplateMatch]:
    """Rank all templates against a single step, descending by score.

    Only templates with ``enabled == True`` and ``score >= min_score`` are returned.
    """
    scored: List[TemplateMatch] = []
    for tmpl in templates:
        if not tmpl.get("enabled", True):
            continue
        score = score_template_for_step(step, tmpl)
        if score < min_score:
            continue
        scored.append(
            TemplateMatch(
                step_id=step.get("step_id", ""),
                template_id=tmpl.get("template_id", ""),
                template_name=tmpl.get("name", ""),
                confidence=round(score, 4),
                capability_overlap=round(
                    _capability_overlap(
                        step.get("inferred_capabilities", []),
                        tmpl.get("capabilities", []),
                    ),
                    4,
                ),
                tag_overlap=round(
                    _tag_overlap(
                        step.get("inferred_tags", []),
                        tmpl.get("tags", []),
                    ),
                    4,
                ),
                keyword_score=round(
                    _keyword_score(
                        step.get("description", ""),
                        tmpl.get("keywords", []),
                    ),
                    4,
                ),
            )
        )
    scored.sort(key=lambda m: m["confidence"], reverse=True)
    return scored


def match_steps_to_templates(
    steps: List[WorkflowStep],
    templates: List[Dict[str, Any]],
    min_score: float = 0.0,
) -> Dict[str, List[TemplateMatch]]:
    """Match every step to all templates.

    Returns ``{step_id: [TemplateMatch, ...]}`` with matches sorted
    descending by confidence.
    """
    return {
        step.get("step_id", f"step_{i}"): match_step_to_templates(
            step, templates, min_score=min_score
        )
        for i, step in enumerate(steps)
    }


def best_matches(
    steps: List[WorkflowStep],
    templates: List[Dict[str, Any]],
    min_score: float = 0.0,
) -> Dict[str, Optional[TemplateMatch]]:
    """Return the single best template match per step (or ``None``).

    Also computes ``match_quality_score`` as the mean confidence across
    all steps.
    """
    all_matches = match_steps_to_templates(steps, templates, min_score=min_score)
    result: Dict[str, Optional[TemplateMatch]] = {}
    for step_id, matches in all_matches.items():
        result[step_id] = matches[0] if matches else None
    return result


def match_quality_score(
    best: Dict[str, Optional[TemplateMatch]],
) -> float:
    """Mean confidence across all steps.  Unmatched steps count as 0."""
    if not best:
        return 0.0
    total = sum(m["confidence"] if m else 0.0 for m in best.values())
    return round(total / len(best), 4)
