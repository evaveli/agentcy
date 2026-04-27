"""Tests for topology Pydantic models: validation, defaults, serialization, edge cases."""
import pytest
from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    MutationAction,
    MutationCondition,
    MutationRule,
    SkeletonCandidate,
    SkeletonStep,
    TopologyOutcome,
    TopologySkeleton,
)


class TestBusinessTemplate:
    def test_required_fields(self):
        bt = BusinessTemplate(workflow_class="shipment_exception")
        assert bt.workflow_class == "shipment_exception"
        assert bt.template_id  # auto UUID

    def test_defaults(self):
        bt = BusinessTemplate(workflow_class="generic")
        assert bt.decision_criticality == "medium"
        assert bt.compliance_strictness == "none"
        assert bt.human_approval_required is False
        assert bt.throughput_priority == "balanced"
        assert bt.integration_types == []
        assert bt.volume_per_day == 0
        assert bt.industry is None

    def test_all_criticality_values(self):
        for val in ("low", "medium", "high"):
            bt = BusinessTemplate(workflow_class="x", decision_criticality=val)
            assert bt.decision_criticality == val

    def test_all_compliance_values(self):
        for val in ("none", "moderate", "strict"):
            bt = BusinessTemplate(workflow_class="x", compliance_strictness=val)
            assert bt.compliance_strictness == val

    def test_all_throughput_values(self):
        for val in ("latency_optimized", "cost_optimized", "balanced"):
            bt = BusinessTemplate(workflow_class="x", throughput_priority=val)
            assert bt.throughput_priority == val

    def test_json_roundtrip(self):
        bt = BusinessTemplate(
            workflow_class="customs_compliance",
            decision_criticality="high",
            compliance_strictness="strict",
            human_approval_required=True,
            integration_types=["tms", "email"],
            volume_per_day=500,
            industry="logistics",
            description="Test template",
        )
        d = bt.model_dump(mode="json")
        restored = BusinessTemplate.model_validate(d)
        assert restored.workflow_class == "customs_compliance"
        assert restored.integration_types == ["tms", "email"]
        assert restored.volume_per_day == 500

    def test_negative_volume_rejected(self):
        with pytest.raises(Exception):
            BusinessTemplate(workflow_class="x", volume_per_day=-1)

    def test_empty_workflow_class(self):
        # Empty string is technically allowed by the model
        bt = BusinessTemplate(workflow_class="")
        assert bt.workflow_class == ""


class TestSkeletonStep:
    def test_minimal(self):
        step = SkeletonStep(step_id="s1", role="intake", name="Intake")
        assert step.is_entry is False
        assert step.is_final is False
        assert step.dependencies == []
        assert step.required_capabilities == []

    def test_full(self):
        step = SkeletonStep(
            step_id="s1", role="verify", name="Check",
            description="Verify output",
            required_capabilities=["validate", "data_read"],
            required_tags=["compliance"],
            is_entry=False, is_final=True,
            dependencies=["s0"],
            coordination_mode="coalition_allowed",
        )
        assert step.coordination_mode == "coalition_allowed"
        assert "validate" in step.required_capabilities

    def test_json_roundtrip(self):
        step = SkeletonStep(step_id="s1", role="execute", name="Run", dependencies=["s0"])
        d = step.model_dump(mode="json")
        restored = SkeletonStep.model_validate(d)
        assert restored.step_id == "s1"
        assert restored.dependencies == ["s0"]


class TestMutationModels:
    def test_condition_defaults(self):
        c = MutationCondition(field="compliance_strictness", value="strict")
        assert c.operator == "eq"

    def test_action_insert_after(self):
        a = MutationAction(
            action_type="insert_after",
            target_step_id="decide",
            step=SkeletonStep(step_id="verify", role="verify", name="Verify"),
        )
        assert a.step is not None
        assert a.field_path is None

    def test_action_modify_field(self):
        a = MutationAction(
            action_type="modify_field",
            target_step_id="s1",
            field_path="required_capabilities",
            field_value=["validate", "http_request"],
        )
        assert a.step is None
        assert a.field_value == ["validate", "http_request"]

    def test_rule_priority_default(self):
        r = MutationRule(name="test", conditions=[], actions=[])
        assert r.priority == 0
        assert r.rule_id  # auto UUID

    def test_rule_json_roundtrip(self):
        r = MutationRule(
            name="test",
            priority=50,
            conditions=[MutationCondition(field="x", operator="eq", value="y")],
            actions=[MutationAction(action_type="remove", target_step_id="s1")],
        )
        d = r.model_dump(mode="json")
        restored = MutationRule.model_validate(d)
        assert restored.priority == 50
        assert len(restored.conditions) == 1
        assert restored.actions[0].action_type == "remove"


class TestTopologySkeleton:
    def test_defaults(self):
        sk = TopologySkeleton(name="test", workflow_class="generic")
        assert sk.skeleton_id  # auto UUID
        assert sk.steps == []
        assert sk.mutation_rules == []
        assert sk.enabled is True
        assert sk.version == "1.0.0"

    def test_json_roundtrip(self):
        sk = TopologySkeleton(
            name="test",
            workflow_class="shipment_exception",
            steps=[
                SkeletonStep(step_id="s1", role="intake", name="In", is_entry=True),
                SkeletonStep(step_id="s2", role="execute", name="Run", dependencies=["s1"], is_final=True),
            ],
            control_patterns=["verification_gate", "retry_wrapper"],
        )
        d = sk.model_dump(mode="json")
        restored = TopologySkeleton.model_validate(d)
        assert len(restored.steps) == 2
        assert restored.control_patterns == ["verification_gate", "retry_wrapper"]


class TestTopologyOutcome:
    def test_defaults(self):
        o = TopologyOutcome(skeleton_id="sk1", pipeline_id="p1", workflow_class="generic")
        assert o.success is False
        assert o.task_count == 0

    def test_json_roundtrip(self):
        o = TopologyOutcome(
            skeleton_id="sk1", pipeline_id="p1", workflow_class="test",
            success=True, execution_time_seconds=12.5, task_count=6,
            mutations_applied=["r1", "r2"],
            business_template={"workflow_class": "test"},
        )
        d = o.model_dump(mode="json")
        restored = TopologyOutcome.model_validate(d)
        assert restored.success is True
        assert restored.mutations_applied == ["r1", "r2"]


class TestSkeletonCandidate:
    def test_score_bounds(self):
        sk = TopologySkeleton(name="t", workflow_class="x")
        c = SkeletonCandidate(skeleton=sk, score=0.85, match_details={"workflow_class": 1.0})
        assert 0.0 <= c.score <= 1.0
