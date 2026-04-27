"""Tests for topology skeleton retrieval and scoring."""
import pytest
from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    SkeletonStep,
    TopologySkeleton,
)
from src.agentcy.cognitive.topology.retrieval import (
    retrieve_skeletons,
    score_skeleton,
)
from src.agentcy.cognitive.topology.seeds import get_logistics_seeds


def _minimal_skeleton(workflow_class: str, control_patterns=None, caps=None) -> TopologySkeleton:
    steps = [
        SkeletonStep(step_id="s1", role="intake", name="In", is_entry=True,
                     required_capabilities=caps or []),
        SkeletonStep(step_id="s2", role="execute", name="Run",
                     dependencies=["s1"], is_final=True,
                     required_capabilities=caps or []),
    ]
    return TopologySkeleton(
        name=f"test_{workflow_class}",
        workflow_class=workflow_class,
        steps=steps,
        control_patterns=control_patterns or [],
    )


class TestScoreSkeleton:
    def test_exact_workflow_match_scores_highest(self):
        bt = BusinessTemplate(workflow_class="shipment_exception")
        sk_exact = _minimal_skeleton("shipment_exception")
        sk_generic = _minimal_skeleton("generic")
        sk_other = _minimal_skeleton("order_fulfillment")

        c_exact = score_skeleton(bt, sk_exact)
        c_generic = score_skeleton(bt, sk_generic)
        c_other = score_skeleton(bt, sk_other)

        assert c_exact.score > c_generic.score
        assert c_generic.score > c_other.score
        # Non-matching workflow scores lower but not zero (other factors contribute)
        assert c_other.score < c_exact.score

    def test_generic_gets_partial_score(self):
        bt = BusinessTemplate(workflow_class="unknown")
        sk = _minimal_skeleton("generic")
        c = score_skeleton(bt, sk)
        assert c.score > 0.0
        assert c.match_details["workflow_class"] == pytest.approx(0.3)

    def test_no_match_scores_zero_workflow(self):
        bt = BusinessTemplate(workflow_class="x")
        sk = _minimal_skeleton("y")
        c = score_skeleton(bt, sk)
        assert c.match_details["workflow_class"] == 0.0

    def test_integration_compatibility(self):
        bt_with = BusinessTemplate(workflow_class="x", integration_types=["carrier_api", "email"])
        sk_with_caps = _minimal_skeleton("x", caps=["http_request", "integration"])
        sk_no_caps = _minimal_skeleton("x", caps=[])

        c1 = score_skeleton(bt_with, sk_with_caps)
        c2 = score_skeleton(bt_with, sk_no_caps)
        assert c1.match_details["integration"] > c2.match_details["integration"]

    def test_no_integration_requirements_full_score(self):
        bt = BusinessTemplate(workflow_class="x", integration_types=[])
        sk = _minimal_skeleton("x")
        c = score_skeleton(bt, sk)
        assert c.match_details["integration"] == 1.0

    def test_compliance_alignment(self):
        bt_strict = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        sk_with_gate = _minimal_skeleton("x", control_patterns=["verification_gate"])
        sk_without = _minimal_skeleton("x")

        c1 = score_skeleton(bt_strict, sk_with_gate)
        c2 = score_skeleton(bt_strict, sk_without)
        assert c1.match_details["constraint_alignment"] > c2.match_details["constraint_alignment"]

    def test_human_approval_alignment(self):
        bt = BusinessTemplate(workflow_class="x", human_approval_required=True)
        sk_with = _minimal_skeleton("x", control_patterns=["human_approval"])
        sk_without = _minimal_skeleton("x")

        c1 = score_skeleton(bt, sk_with)
        c2 = score_skeleton(bt, sk_without)
        assert c1.score > c2.score

    def test_control_pattern_coverage(self):
        bt = BusinessTemplate(
            workflow_class="x",
            compliance_strictness="strict",
            human_approval_required=True,
            decision_criticality="high",
        )
        sk_full = _minimal_skeleton("x", control_patterns=["verification_gate", "human_approval", "retry_wrapper"])
        sk_partial = _minimal_skeleton("x", control_patterns=["verification_gate"])
        sk_none = _minimal_skeleton("x")

        c_full = score_skeleton(bt, sk_full)
        c_partial = score_skeleton(bt, sk_partial)
        c_none = score_skeleton(bt, sk_none)

        assert c_full.match_details["control_pattern_coverage"] > c_partial.match_details["control_pattern_coverage"]
        assert c_partial.match_details["control_pattern_coverage"] > c_none.match_details["control_pattern_coverage"]

    def test_score_clamped_0_1(self):
        bt = BusinessTemplate(workflow_class="x")
        sk = _minimal_skeleton("x")
        c = score_skeleton(bt, sk)
        assert 0.0 <= c.score <= 1.0


class TestRetrieveSkeletons:
    def test_returns_sorted_descending(self):
        bt = BusinessTemplate(workflow_class="shipment_exception")
        seeds = get_logistics_seeds()
        candidates = retrieve_skeletons(bt, seeds)
        assert len(candidates) > 0
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_exact_match_is_first(self):
        bt = BusinessTemplate(workflow_class="order_fulfillment")
        seeds = get_logistics_seeds()
        candidates = retrieve_skeletons(bt, seeds)
        assert candidates[0].skeleton.workflow_class == "order_fulfillment"

    def test_min_score_filter(self):
        bt = BusinessTemplate(workflow_class="unknown_class")
        seeds = get_logistics_seeds()
        # With high min_score, no non-matching skeleton should pass
        candidates = retrieve_skeletons(bt, seeds, min_score=0.9)
        assert len(candidates) == 0

    def test_disabled_skeletons_excluded(self):
        sk = _minimal_skeleton("test")
        sk_disabled = _minimal_skeleton("test")
        sk_disabled.enabled = False
        bt = BusinessTemplate(workflow_class="test")
        candidates = retrieve_skeletons(bt, [sk, sk_disabled])
        assert len(candidates) == 1

    def test_empty_skeletons_list(self):
        bt = BusinessTemplate(workflow_class="x")
        assert retrieve_skeletons(bt, []) == []

    def test_all_four_seeds_score_for_matching_class(self):
        seeds = get_logistics_seeds()
        for sk in seeds:
            bt = BusinessTemplate(workflow_class=sk.workflow_class)
            candidates = retrieve_skeletons(bt, seeds)
            assert candidates[0].skeleton.workflow_class == sk.workflow_class
