"""Tests for the runtime policy engine: health signals → policy decisions."""
import pytest
from src.agentcy.pydantic_models.runtime_policy_models import (
    CoalitionPolicyMode,
    FallbackPolicyMode,
    HealthSignals,
    HumanGateBias,
    PolicyDecisionLog,
    PolicyState,
    TopologyVariantBias,
    VerificationPolicyMode,
)
from src.agentcy.agent_runtime.services.runtime_policy import (
    RuntimePolicyEngine,
    runtime_policy_enabled,
)


# ── Model defaults ───────────────────────────────────────────────────────


class TestHealthSignalsDefaults:
    def test_all_healthy(self):
        s = HealthSignals()
        assert s.queue_lag_ms == 0.0
        assert s.agent_pool_saturation == 0.0
        assert s.recent_timeout_rate == 0.0
        assert s.human_approval_backlog == 0

    def test_json_roundtrip(self):
        s = HealthSignals(queue_lag_ms=500, verifier_pool_saturation=0.8)
        d = s.model_dump(mode="json")
        restored = HealthSignals.model_validate(d)
        assert restored.queue_lag_ms == 500
        assert restored.verifier_pool_saturation == 0.8


class TestPolicyStateDefaults:
    def test_neutral(self):
        p = PolicyState()
        assert p.coalition_mode == CoalitionPolicyMode.ENABLED
        assert p.verification_mode == VerificationPolicyMode.NORMAL
        assert p.fallback_policy == FallbackPolicyMode.NORMAL
        assert p.topology_variant_bias == TopologyVariantBias.BASELINE
        assert p.human_gate_bias == HumanGateBias.NORMAL
        assert p.coalition_margin_override is None
        assert p.retry_budget_multiplier == 1.0
        assert p.triggered_rules == []


# ── Feature gate ─────────────────────────────────────────────────────────


class TestFeatureGate:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("RUNTIME_POLICY_ENABLE", raising=False)
        assert runtime_policy_enabled() is False

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        assert runtime_policy_enabled() is True

    def test_disabled_returns_neutral(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "0")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(queue_lag_ms=99999))
        assert policy.coalition_mode == CoalitionPolicyMode.ENABLED  # Neutral
        assert policy.triggered_rules == []


# ── Individual rules ─────────────────────────────────────────────────────


class TestQueueLagRule:
    def test_high_lag_discourages_coalitions(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(queue_lag_ms=800))
        assert policy.coalition_mode == CoalitionPolicyMode.DISCOURAGED
        assert policy.coalition_margin_override == 0.15
        assert "queue_lag_high_discourages_coalitions" in policy.triggered_rules

    def test_critical_lag_disables_coalitions(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(queue_lag_ms=3000))
        assert policy.coalition_mode == CoalitionPolicyMode.DISABLED
        assert "queue_lag_critical_disables_coalitions" in policy.triggered_rules

    def test_normal_lag_no_effect(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(queue_lag_ms=100))
        assert policy.coalition_mode == CoalitionPolicyMode.ENABLED


class TestVerifierSaturationRule:
    def test_saturated_verifier_prefers_solo(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(verifier_pool_saturation=0.85))
        assert policy.coalition_mode == CoalitionPolicyMode.DISCOURAGED
        assert policy.fallback_policy == FallbackPolicyMode.AGGRESSIVE
        assert "verifier_saturated_prefer_solo" in policy.triggered_rules

    def test_low_saturation_no_effect(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(verifier_pool_saturation=0.3))
        assert "verifier_saturated_prefer_solo" not in policy.triggered_rules


class TestTimeoutSpikeRule:
    def test_high_timeout_tightens_retries(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(recent_timeout_rate=0.25))
        assert policy.retry_budget_multiplier < 1.0
        assert policy.fallback_policy == FallbackPolicyMode.AGGRESSIVE
        assert "timeout_spike_tighten_retries" in policy.triggered_rules


class TestPolicyIncidentRule:
    def test_incidents_force_stricter(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(recent_policy_incident_rate=0.08))
        assert policy.verification_mode == VerificationPolicyMode.STRICTER
        assert "policy_incidents_force_stricter_verification" in policy.triggered_rules


class TestHandoffFrictionRule:
    def test_high_friction_discourages_coalitions(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(recent_coalition_handoff_failure_rate=0.30))
        assert policy.coalition_mode == CoalitionPolicyMode.DISCOURAGED
        assert "handoff_friction_discourages_coalitions" in policy.triggered_rules


class TestCostBurnRule:
    def test_high_cost_prefers_cheap(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(cost_burn_rate_per_min=8.0))
        assert policy.topology_variant_bias == TopologyVariantBias.LOW_COST
        assert policy.verification_mode == VerificationPolicyMode.MINIMAL
        assert "cost_burn_high_prefer_cheap" in policy.triggered_rules


class TestHumanBacklogRule:
    def test_backlog_defers_gates(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(human_approval_backlog=15))
        assert policy.human_gate_bias == HumanGateBias.LATER
        assert "human_backlog_defer_gates" in policy.triggered_rules


class TestAgentPoolPressureRule:
    def test_saturated_pool_disables_coalitions(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(agent_pool_saturation=0.9))
        assert policy.coalition_mode == CoalitionPolicyMode.DISABLED
        assert policy.retry_budget_multiplier <= 0.5
        assert "agent_pool_pressure_disable_coalitions" in policy.triggered_rules


class TestRetryRateRule:
    def test_high_retries_prefer_safety(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals(recent_retry_rate=0.30))
        assert policy.topology_variant_bias == TopologyVariantBias.HIGH_SAFETY
        assert "retry_rate_high_prefer_safety" in policy.triggered_rules


# ── Multiple rules firing ────────────────────────────────────────────────


class TestMultipleRules:
    def test_combined_stress(self, monkeypatch):
        """Under combined stress, multiple rules fire."""
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        signals = HealthSignals(
            queue_lag_ms=3000,
            verifier_pool_saturation=0.9,
            recent_timeout_rate=0.20,
            recent_policy_incident_rate=0.10,
            cost_burn_rate_per_min=10.0,
        )
        policy = engine.evaluate(signals)
        assert len(policy.triggered_rules) >= 4
        assert policy.coalition_mode == CoalitionPolicyMode.DISABLED
        assert policy.fallback_policy == FallbackPolicyMode.AGGRESSIVE

    def test_all_healthy_no_rules(self, monkeypatch):
        """Completely healthy signals → no rules fire."""
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine()
        policy = engine.evaluate(HealthSignals())
        assert len(policy.triggered_rules) == 0
        assert policy.coalition_mode == CoalitionPolicyMode.ENABLED


# ── Decision logging ─────────────────────────────────────────────────────


class TestDecisionLogging:
    def test_log_includes_signals_and_policy(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")

        logged = []

        class FakeStore:
            def upsert_raw(self, key, doc):
                logged.append(doc)

        engine = RuntimePolicyEngine(store=FakeStore())
        engine.evaluate(HealthSignals(queue_lag_ms=800), username="alice")
        assert len(logged) == 1
        assert logged[0]["username"] == "alice"
        assert "queue_lag_ms" in str(logged[0]["signals_snapshot"])
        assert len(logged[0]["triggered_rules"]) > 0

    def test_no_store_no_crash(self, monkeypatch):
        monkeypatch.setenv("RUNTIME_POLICY_ENABLE", "1")
        engine = RuntimePolicyEngine(store=None)
        policy = engine.evaluate(HealthSignals(queue_lag_ms=800))
        assert len(policy.triggered_rules) > 0


# ── Coalition assembler integration ──────────────────────────────────────


class TestCoalitionPolicyIntegration:
    def test_disabled_blocks_coalition(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        from src.agentcy.agent_runtime.services.coalition_assembler import assemble_coalition
        from src.agentcy.pydantic_models.multi_agent_pipeline import CoordinationMode, TaskSpec

        spec = TaskSpec(
            task_id="t1", username="alice", description="test",
            coordination_mode=CoordinationMode.COALITION_ALLOWED,
            required_capabilities=["processing"],
        )
        primary = {"agent_id": "a1", "capabilities": ["processing"], "tags": [], "metadata": {"cnp": {"trust": 0.9, "load": 0, "max_load": 3}}}
        partner = {"agent_id": "a2", "capabilities": ["validate", "verification"], "tags": ["verifier"], "metadata": {"cnp": {"trust": 0.8, "load": 0, "max_load": 3}}}

        # Without policy override → assembles normally
        result = assemble_coalition(
            task_spec=spec, primary_agent=primary, primary_bid_score=0.8,
            all_agents=[primary, partner], username="alice",
        )
        assert result is not None

        # With policy disabled → blocks
        result_disabled = assemble_coalition(
            task_spec=spec, primary_agent=primary, primary_bid_score=0.8,
            all_agents=[primary, partner], username="alice",
            coalition_policy_mode="disabled",
        )
        assert result_disabled is None

    def test_discouraged_blocks_allowed_but_not_required(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        from src.agentcy.agent_runtime.services.coalition_assembler import assemble_coalition
        from src.agentcy.pydantic_models.multi_agent_pipeline import CoordinationMode, TaskSpec

        primary = {"agent_id": "a1", "capabilities": ["processing"], "tags": [], "metadata": {"cnp": {"trust": 0.9, "load": 0, "max_load": 3}}}
        partner = {"agent_id": "a2", "capabilities": ["validate", "verification"], "tags": ["verifier"], "metadata": {"cnp": {"trust": 0.8, "load": 0, "max_load": 3}}}

        # COALITION_ALLOWED + discouraged → blocked
        spec_allowed = TaskSpec(
            task_id="t1", username="alice", description="test",
            coordination_mode=CoordinationMode.COALITION_ALLOWED,
            required_capabilities=["processing"],
        )
        result = assemble_coalition(
            task_spec=spec_allowed, primary_agent=primary, primary_bid_score=0.8,
            all_agents=[primary, partner], username="alice",
            coalition_policy_mode="discouraged",
        )
        assert result is None

        # COALITION_REQUIRED + discouraged → still allowed
        spec_required = TaskSpec(
            task_id="t2", username="alice", description="test",
            coordination_mode=CoordinationMode.COALITION_REQUIRED,
            required_capabilities=["processing"],
        )
        result_req = assemble_coalition(
            task_spec=spec_required, primary_agent=primary, primary_bid_score=0.8,
            all_agents=[primary, partner], username="alice",
            coalition_policy_mode="discouraged",
        )
        assert result_req is not None


# ── Topology orchestrator integration ────────────────────────────────────


class TestTopologyPolicyIntegration:
    def test_high_safety_escalates_compliance(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        from src.agentcy.cognitive.topology.orchestrator import generate_pipeline_from_template
        from src.agentcy.cognitive.topology.seeds import get_logistics_seeds

        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="none",
            decision_criticality="low",
        )
        policy = PolicyState(topology_variant_bias=TopologyVariantBias.HIGH_SAFETY)

        result = generate_pipeline_from_template(bt, seeds, [], policy=policy)
        assert result is not None
        # HIGH_SAFETY should have escalated compliance from "none" to "moderate"
        # and criticality from "low" to "medium", affecting error handling
        assert result["error_handling"]["retry_policy"]["max_retries"] >= 3

    def test_stricter_verification_inserts_gate(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        from src.agentcy.cognitive.topology.orchestrator import generate_pipeline_from_template
        from src.agentcy.cognitive.topology.seeds import get_logistics_seeds

        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="none",
        )
        policy = PolicyState(verification_mode=VerificationPolicyMode.STRICTER)

        result = generate_pipeline_from_template(bt, seeds, [], policy=policy)
        assert result is not None
        task_ids = [t["id"] for t in result["dag"]["tasks"]]
        # STRICTER should have escalated compliance to "strict" → compliance_verify inserted
        assert "compliance_verify" in task_ids

    def test_mutation_suppression(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        from src.agentcy.cognitive.topology.orchestrator import generate_pipeline_from_template
        from src.agentcy.cognitive.topology.seeds import get_logistics_seeds

        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
        )

        # Without suppression → compliance_verify inserted
        result_normal = generate_pipeline_from_template(bt, seeds, [])
        ids_normal = [t["id"] for t in result_normal["dag"]["tasks"]]
        assert "compliance_verify" in ids_normal

        # With suppression → compliance_verify NOT inserted
        policy = PolicyState(mutation_suppression=["rule_compliance_gate"])
        result_suppressed = generate_pipeline_from_template(bt, seeds, [], policy=policy)
        ids_suppressed = [t["id"] for t in result_suppressed["dag"]["tasks"]]
        assert "compliance_verify" not in ids_suppressed


# Need to import BusinessTemplate here for the topology tests
from src.agentcy.pydantic_models.topology_models import BusinessTemplate
