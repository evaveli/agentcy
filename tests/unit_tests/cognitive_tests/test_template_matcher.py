"""Tests for the template matching engine."""
from src.agentcy.cognitive.template_matcher import (
    WorkflowStep,
    score_template_for_step,
    match_step_to_templates,
    match_steps_to_templates,
    best_matches,
    match_quality_score,
)


def _step(**overrides) -> WorkflowStep:
    base: WorkflowStep = {
        "step_id": "step-1",
        "description": "check inventory levels in the warehouse",
        "inferred_capabilities": ["inventory_check", "data_read"],
        "inferred_tags": ["warehouse", "stock"],
        "dependencies": [],
        "is_entry": True,
        "is_final": False,
    }
    base.update(overrides)
    return base


def _template(**overrides):
    base = {
        "template_id": "tmpl-inv",
        "name": "inventory_checker",
        "capabilities": ["inventory_check", "data_read", "reporting"],
        "tags": ["warehouse", "stock", "logistics"],
        "keywords": ["inventory", "stock", "warehouse", "levels"],
        "enabled": True,
    }
    base.update(overrides)
    return base


# ── score_template_for_step ──────────────────────────────────────────────


def test_perfect_match_scores_high():
    score = score_template_for_step(_step(), _template())
    assert score > 0.8


def test_no_overlap_scores_zero():
    step = _step(
        inferred_capabilities=["payment"],
        inferred_tags=["billing"],
        description="process credit card payment",
    )
    tmpl = _template(
        capabilities=["inventory_check"],
        tags=["warehouse"],
        keywords=["stock"],
    )
    score = score_template_for_step(step, tmpl)
    assert score == 0.0


def test_partial_capability_overlap():
    step = _step(inferred_capabilities=["inventory_check", "data_write", "alerting"])
    tmpl = _template(capabilities=["inventory_check"])
    score = score_template_for_step(step, tmpl)
    # Only 1 of 3 capabilities match -> capability component is ~0.33
    assert 0.1 < score < 0.8


def test_keyword_only_match():
    step = _step(
        inferred_capabilities=[],
        inferred_tags=[],
        description="check inventory stock levels in the warehouse",
    )
    tmpl = _template(capabilities=[], tags=[])
    score = score_template_for_step(step, tmpl)
    # All 4 keywords hit -> keyword component = 0.30 * 1.0 = 0.30
    assert 0.25 <= score <= 0.35


def test_case_insensitive_matching():
    step = _step(
        inferred_capabilities=["Inventory_Check"],
        inferred_tags=["WAREHOUSE"],
    )
    tmpl = _template(
        capabilities=["inventory_check"],
        tags=["warehouse"],
    )
    score = score_template_for_step(step, tmpl)
    assert score > 0.5


def test_empty_step_returns_zero():
    step = _step(inferred_capabilities=[], inferred_tags=[], description="")
    tmpl = _template()
    score = score_template_for_step(step, tmpl)
    assert score == 0.0


def test_score_capped_at_one():
    step = _step()
    tmpl = _template()
    score = score_template_for_step(step, tmpl)
    assert score <= 1.0


# ── match_step_to_templates ──────────────────────────────────────────────


def test_match_step_to_templates_ranks_descending():
    step = _step()
    good = _template(template_id="good", name="good")
    weak = _template(
        template_id="weak",
        name="weak",
        capabilities=["unrelated"],
        tags=["other"],
        keywords=["nope"],
    )
    results = match_step_to_templates(step, [weak, good])
    assert len(results) == 2
    assert results[0]["template_id"] == "good"
    assert results[0]["confidence"] >= results[1]["confidence"]


def test_min_score_filters():
    step = _step()
    good = _template(template_id="good", name="good")
    weak = _template(
        template_id="weak",
        name="weak",
        capabilities=["unrelated"],
        tags=[],
        keywords=[],
    )
    results = match_step_to_templates(step, [good, weak], min_score=0.5)
    assert all(m["confidence"] >= 0.5 for m in results)


def test_disabled_templates_excluded():
    step = _step()
    disabled = _template(template_id="off", name="off", enabled=False)
    enabled = _template(template_id="on", name="on")
    results = match_step_to_templates(step, [disabled, enabled])
    ids = [m["template_id"] for m in results]
    assert "off" not in ids
    assert "on" in ids


def test_match_result_has_all_fields():
    step = _step()
    tmpl = _template()
    results = match_step_to_templates(step, [tmpl])
    assert len(results) == 1
    m = results[0]
    assert "step_id" in m
    assert "template_id" in m
    assert "template_name" in m
    assert "confidence" in m
    assert "capability_overlap" in m
    assert "tag_overlap" in m
    assert "keyword_score" in m


# ── match_steps_to_templates ─────────────────────────────────────────────


def test_match_steps_returns_per_step_dict():
    steps = [
        _step(step_id="s1", description="check inventory"),
        _step(step_id="s2", description="send notification"),
    ]
    tmpl = _template()
    result = match_steps_to_templates(steps, [tmpl])
    assert "s1" in result
    assert "s2" in result
    assert isinstance(result["s1"], list)


def test_missing_step_id_uses_index():
    step: WorkflowStep = {
        "description": "something",
        "inferred_capabilities": [],
        "inferred_tags": [],
        "dependencies": [],
        "is_entry": True,
        "is_final": False,
    }
    result = match_steps_to_templates([step], [_template()])
    assert "step_0" in result


# ── best_matches & match_quality_score ───────────────────────────────────


def test_best_matches_returns_top_match_per_step():
    step = _step()
    good = _template(template_id="best", name="best")
    weak = _template(
        template_id="weak",
        name="weak",
        capabilities=["unrelated"],
        tags=[],
        keywords=[],
    )
    result = best_matches([step], [good, weak])
    assert result["step-1"] is not None
    assert result["step-1"]["template_id"] == "best"


def test_best_matches_returns_none_when_no_match():
    step = _step()
    result = best_matches([step], [], min_score=0.5)
    assert result["step-1"] is None


def test_match_quality_score_perfect():
    bm = {"s1": {"confidence": 1.0}, "s2": {"confidence": 1.0}}
    assert match_quality_score(bm) == 1.0


def test_match_quality_score_with_unmatched():
    bm = {"s1": {"confidence": 0.8}, "s2": None}
    score = match_quality_score(bm)
    assert score == 0.4  # (0.8 + 0.0) / 2


def test_match_quality_score_empty():
    assert match_quality_score({}) == 0.0
