"""Tests for the topology compiler: skeleton → PipelineCreate."""
import pytest
from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    SkeletonStep,
    TopologySkeleton,
)
from src.agentcy.cognitive.topology.compiler import compile_skeleton_to_pipeline
from src.agentcy.cognitive.topology.mutation import apply_mutations
from src.agentcy.cognitive.topology.seeds import get_logistics_seeds


def _simple_skeleton() -> TopologySkeleton:
    return TopologySkeleton(
        name="test", workflow_class="test",
        steps=[
            SkeletonStep(step_id="s1", role="intake", name="Intake", is_entry=True,
                         required_capabilities=["data_read"]),
            SkeletonStep(step_id="s2", role="execute", name="Execute", dependencies=["s1"],
                         required_capabilities=["http_request"]),
            SkeletonStep(step_id="s3", role="notify", name="Notify", dependencies=["s2"],
                         is_final=True, required_capabilities=["notification"]),
        ],
    )


class TestCompileSkeleton:
    def test_basic_compilation(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, agent_templates=[])

        assert "dag" in result
        tasks = result["dag"]["tasks"]
        assert len(tasks) == 3

    def test_entry_and_final_flags(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, [])
        tasks = result["dag"]["tasks"]

        entries = [t for t in tasks if t["is_entry"]]
        finals = [t for t in tasks if t["is_final_task"]]
        assert len(entries) == 1
        assert len(finals) == 1
        assert entries[0]["id"] == "s1"
        assert finals[0]["id"] == "s3"

    def test_dependencies_wired(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, [])
        tasks = result["dag"]["tasks"]

        s2 = next(t for t in tasks if t["id"] == "s2")
        assert s2["inputs"]["dependencies"] == ["s1"]

        s3 = next(t for t in tasks if t["id"] == "s3")
        assert s3["inputs"]["dependencies"] == ["s2"]

    def test_entry_task_has_no_inputs(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, [])
        tasks = result["dag"]["tasks"]
        s1 = next(t for t in tasks if t["id"] == "s1")
        assert s1["inputs"] is None

    def test_error_handling_high_criticality(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test", decision_criticality="high")
        result = compile_skeleton_to_pipeline(sk, bt, [])
        eh = result["error_handling"]
        assert eh["retry_policy"]["max_retries"] == 5
        assert eh["retry_policy"]["backoff_strategy"] == "exponential"
        assert eh["on_failure"] == "escalate"

    def test_error_handling_medium_criticality(self):
        bt = BusinessTemplate(workflow_class="test", decision_criticality="medium")
        result = compile_skeleton_to_pipeline(_simple_skeleton(), bt, [])
        assert result["error_handling"]["retry_policy"]["max_retries"] == 3

    def test_error_handling_low_criticality(self):
        bt = BusinessTemplate(workflow_class="test", decision_criticality="low")
        result = compile_skeleton_to_pipeline(_simple_skeleton(), bt, [])
        assert result["error_handling"]["retry_policy"]["max_retries"] == 1
        assert result["error_handling"]["on_failure"] == "skip"

    def test_default_service_names_without_templates(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, agent_templates=[])
        tasks = result["dag"]["tasks"]
        # Without matching templates, falls back to role-based names
        for t in tasks:
            assert t["available_services"]  # Not empty

    def test_template_matching_assigns_service(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        # Provide a template that matches the intake step
        templates = [{
            "template_id": "tmpl1",
            "name": "data-reader-service",
            "capabilities": ["data_read", "parse"],
            "tags": [],
            "keywords": ["intake", "read"],
            "enabled": True,
        }]
        result = compile_skeleton_to_pipeline(sk, bt, agent_templates=templates)
        tasks = result["dag"]["tasks"]
        s1 = next(t for t in tasks if t["id"] == "s1")
        assert s1["available_services"] == "data-reader-service"

    def test_pipeline_name_format(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = compile_skeleton_to_pipeline(sk, bt, [])
        assert result["pipeline_name"] == "topology_shipment_exception"

    def test_vhost_propagated(self):
        sk = _simple_skeleton()
        bt = BusinessTemplate(workflow_class="test")
        result = compile_skeleton_to_pipeline(sk, bt, [], vhost="/custom")
        assert result["vhost"] == "/custom"


class TestCompileSeeds:
    """All 4 logistics seeds must compile to valid PipelineCreate dicts."""

    @pytest.fixture
    def seeds(self):
        return get_logistics_seeds()

    def test_all_seeds_compile(self, seeds):
        for sk in seeds:
            bt = BusinessTemplate(workflow_class=sk.workflow_class)
            mutated, _ = apply_mutations(sk, bt)
            result = compile_skeleton_to_pipeline(mutated, bt, [])
            tasks = result["dag"]["tasks"]
            assert len(tasks) >= len(sk.steps)
            assert any(t["is_entry"] for t in tasks), f"{sk.name}: no entry"
            assert any(t["is_final_task"] for t in tasks), f"{sk.name}: no final"

    def test_seeds_with_strict_compliance(self, seeds):
        for sk in seeds:
            bt = BusinessTemplate(
                workflow_class=sk.workflow_class,
                compliance_strictness="strict",
                human_approval_required=True,
                integration_types=["email"],
            )
            mutated, applied = apply_mutations(sk, bt)
            result = compile_skeleton_to_pipeline(mutated, bt, [])
            tasks = result["dag"]["tasks"]
            # Should have more tasks than base skeleton
            assert len(tasks) >= len(sk.steps)

    def test_shipment_exception_full_mutation(self):
        seeds = get_logistics_seeds()
        sk = next(s for s in seeds if s.workflow_class == "shipment_exception")
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            decision_criticality="high",
            compliance_strictness="strict",
            human_approval_required=True,
            integration_types=["email"],
        )
        mutated, applied = apply_mutations(sk, bt)
        result = compile_skeleton_to_pipeline(mutated, bt, [])
        tasks = result["dag"]["tasks"]

        ids = [t["id"] for t in tasks]
        assert "compliance_verify" in ids
        assert "human_approve" in ids
        assert "email_notify" in ids
        assert len(tasks) == 9  # 6 base + 3 inserted
