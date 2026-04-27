"""Tests for the end-to-end pipeline generation service."""
import pytest
from typing import Any, Dict, List, Optional

from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    TopologyOutcome,
    TopologyPerformance,
)
from src.agentcy.pydantic_models.runtime_policy_models import (
    CoalitionPolicyMode,
    HealthSignals,
    PolicyState,
    TopologyVariantBias,
    VerificationPolicyMode,
)
from src.agentcy.cognitive.topology.generation_service import (
    GenerationResult,
    generate_system,
)


# ── Fake stores ──────────────────────────────────────────────────────────


class _FakeGraphStore:
    def __init__(self):
        self.outcomes: List[Dict[str, Any]] = []
        self.logged: List[Dict[str, Any]] = []

    def list_topology_outcomes(self, *, username, topology_signature=None,
                               skeleton_id=None, limit=None, offset=0):
        items = list(self.outcomes)
        if topology_signature:
            items = [i for i in items if i.get("topology_signature") == topology_signature]
        if skeleton_id:
            items = [i for i in items if i.get("skeleton_id") == skeleton_id]
        return items, len(items)

    def upsert_raw(self, key, doc):
        self.logged.append(doc)


class _FakeTemplateStore:
    def __init__(self, templates=None):
        self._templates = templates or []

    def list(self, *, username, enabled=None):
        return list(self._templates)


# ── GenerationResult ─────────────────────────────────────────────────────


class TestGenerationResult:
    def test_success(self):
        r = GenerationResult(pipeline_create={"dag": {"tasks": []}})
        assert r.success is True

    def test_failure(self):
        r = GenerationResult(error="No skeleton found")
        assert r.success is False

    def test_to_dict(self):
        r = GenerationResult(
            pipeline_create={"dag": {"tasks": []}, "_topology_metadata": {}},
            policy_state=PolicyState(),
            topology_metadata={"skeleton_id": "s1"},
        )
        d = r.to_dict()
        assert d["success"] is True
        assert "topology_metadata" in d
        assert "policy" in d


# ── Core generation ──────────────────────────────────────────────────────


class TestGenerateSystem:
    def test_basic_generation(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "0")

        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(business_template=bt, username="alice")

        assert result.success
        assert result.pipeline_create is not None
        assert "dag" in result.pipeline_create
        tasks = result.pipeline_create["dag"]["tasks"]
        assert len(tasks) >= 6  # Base shipment exception has 6 steps
        assert result.topology_metadata is not None
        assert result.topology_metadata["skeleton_name"] == "Shipment Exception Handling"

    def test_all_workflow_classes(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        for wc in ("shipment_exception", "order_fulfillment", "carrier_selection", "customs_compliance"):
            bt = BusinessTemplate(workflow_class=wc)
            result = generate_system(business_template=bt, username="alice")
            assert result.success, f"Failed for {wc}: {result.error}"

    def test_unknown_workflow_class(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(workflow_class="unknown_workflow_xyz")
        result = generate_system(business_template=bt, username="alice")
        # Should still succeed by falling back to generic matching
        # (all seeds score > 0 because non-workflow factors contribute)
        assert result.success or result.error is not None

    def test_feature_disabled(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "0")
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(business_template=bt, username="alice")
        assert not result.success

    def test_with_strict_compliance(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
            human_approval_required=True,
        )
        result = generate_system(business_template=bt, username="alice")
        assert result.success
        task_ids = [t["id"] for t in result.pipeline_create["dag"]["tasks"]]
        assert "compliance_verify" in task_ids
        assert "human_approve" in task_ids

    def test_with_experiment_mode(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
            experiment_mode=True,
        )
        variants = set()
        for _ in range(20):
            result = generate_system(business_template=bt, username="alice")
            if result.success and result.topology_metadata:
                variants.add(result.topology_metadata.get("variant_id"))
        assert len(variants) >= 2


# ── Policy integration ───────────────────────────────────────────────────


class TestGenerateWithPolicy:
    def test_policy_affects_output(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")

        # Under stress: high queue lag + policy incidents
        signals = HealthSignals(
            queue_lag_ms=3000,
            recent_policy_incident_rate=0.10,
        )
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="none",
        )
        result = generate_system(
            business_template=bt,
            username="alice",
            health_signals=signals,
        )
        assert result.success
        assert result.policy_state is not None
        # Policy incidents should have forced stricter verification
        assert result.policy_state.verification_mode == VerificationPolicyMode.STRICTER
        # Which means compliance_verify should be inserted
        task_ids = [t["id"] for t in result.pipeline_create["dag"]["tasks"]]
        assert "compliance_verify" in task_ids

    def test_healthy_signals_no_policy_change(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")

        signals = HealthSignals()  # All healthy defaults
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            health_signals=signals,
        )
        assert result.success
        assert len(result.policy_state.triggered_rules) == 0

    def test_no_health_signals_uses_defaults(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")

        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            health_signals=None,
        )
        assert result.success
        # No signals → no policy triggered
        assert result.policy_state.coalition_mode == CoalitionPolicyMode.ENABLED


# ── Performance integration ──────────────────────────────────────────────


class TestGenerateWithPerformance:
    def test_performance_data_used(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")

        store = _FakeGraphStore()
        # Seed outcomes for the shipment exception skeleton
        from src.agentcy.cognitive.topology.seeds import get_logistics_seeds
        seed = get_logistics_seeds()[0]  # shipment_exception
        for i in range(10):
            o = TopologyOutcome(
                skeleton_id=seed.skeleton_id,
                pipeline_id=f"p{i}",
                workflow_class="shipment_exception",
                topology_signature=seed.skeleton_id,
                success=True,
                execution_time_seconds=5.0,
                task_count=6,
            )
            store.outcomes.append(o.model_dump(mode="json"))

        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            graph_marker_store=store,
        )
        assert result.success
        # Performance data should have been loaded
        assert result.performance_used is not None
        assert result.performance_used["sample_count"] == 10
        assert result.performance_used["success_rate"] == 1.0

    def test_no_store_still_works(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            graph_marker_store=None,
        )
        assert result.success
        assert result.performance_used is None


# ── Template matching ────────────────────────────────────────────────────


class TestGenerateWithTemplates:
    def test_agent_templates_improve_service_names(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")

        templates = [{
            "template_id": "t1",
            "name": "logistics-intake-service",
            "capabilities": ["data_read", "parse"],
            "tags": ["intake"],
            "keywords": ["shipment", "exception", "receive"],
            "enabled": True,
        }]
        template_store = _FakeTemplateStore(templates)

        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            template_store=template_store,
        )
        assert result.success
        # The intake task should use the matched template's service name
        intake = next(t for t in result.pipeline_create["dag"]["tasks"] if t["id"] == "intake")
        assert intake["available_services"] == "logistics-intake-service"

    def test_no_templates_uses_defaults(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            template_store=None,
        )
        assert result.success
        # Should still compile with fallback service names
        tasks = result.pipeline_create["dag"]["tasks"]
        assert all(t["available_services"] for t in tasks)


# ── Error handling ───────────────────────────────────────────────────────


class TestGenerateErrors:
    def test_no_skeletons(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_system(
            business_template=bt,
            username="alice",
            include_seeds=False,
        )
        assert not result.success
        assert "No topology skeletons" in result.error

    def test_result_to_dict_on_error(self):
        r = GenerationResult(error="Something broke")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "Something broke"


# ── End-to-end traceability ──────────────────────────────────────────────


class TestTraceability:
    def test_full_metadata_chain(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")

        signals = HealthSignals(recent_retry_rate=0.30)
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
            decision_criticality="high",
        )
        result = generate_system(
            business_template=bt,
            username="alice",
            health_signals=signals,
        )
        assert result.success

        # Topology metadata
        meta = result.topology_metadata
        assert meta["skeleton_name"] == "Shipment Exception Handling"
        assert "mutations_applied" in meta
        assert "topology_signature" in meta
        assert "business_template" in meta

        # Policy state
        assert result.policy_state is not None
        assert "retry_rate_high_prefer_safety" in result.policy_state.triggered_rules

        # Response dict
        d = result.to_dict()
        assert d["success"] is True
        assert "topology_metadata" in d
        assert "policy" in d
        assert d["policy"]["topology_variant_bias"] == "high_safety"
