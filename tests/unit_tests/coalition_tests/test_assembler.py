"""Tests for orchestrator-mediated coalition assembly."""
import pytest
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    CoordinationMode,
    TaskSpec,
)
from src.agentcy.agent_runtime.services.coalition_assembler import (
    assemble_coalition,
    _coalition_compatibility_score,
    _is_verifier_candidate,
)


def _agent(agent_id, capabilities=None, tags=None, trust=0.7, load=0, max_load=3):
    return {
        "agent_id": agent_id,
        "capabilities": capabilities or [],
        "tags": tags or [],
        "metadata": {"cnp": {"trust": trust, "load": load, "max_load": max_load}},
    }


def _spec(coordination_mode=CoordinationMode.COALITION_ALLOWED, capabilities=None):
    return TaskSpec(
        task_id="t1", username="alice", description="test",
        coordination_mode=coordination_mode,
        required_capabilities=capabilities or ["processing"],
    )


class TestIsVerifierCandidate:
    def test_verifier_capability(self):
        assert _is_verifier_candidate(_agent("a", capabilities=["validate"])) is True

    def test_verification_capability(self):
        assert _is_verifier_candidate(_agent("a", capabilities=["verification"])) is True

    def test_verifier_tag(self):
        assert _is_verifier_candidate(_agent("a", tags=["verifier"])) is True

    def test_no_verifier_signal(self):
        assert _is_verifier_candidate(_agent("a", capabilities=["http_request"], tags=["executor"])) is False

    def test_empty_agent(self):
        assert _is_verifier_candidate(_agent("a")) is False


class TestCompatibilityScore:
    def test_complementary_pair(self):
        primary = _agent("a1", capabilities=["processing", "ml_inference"], trust=0.9)
        partner = _agent("a2", capabilities=["validate", "verification"], trust=0.85)
        score = _coalition_compatibility_score(primary, partner)
        assert score > 0.3

    def test_identical_capabilities_low_complementarity(self):
        primary = _agent("a1", capabilities=["validate"])
        partner = _agent("a2", capabilities=["validate"])
        score = _coalition_compatibility_score(primary, partner)
        # Low complementarity since no new capabilities
        assert score < 0.5

    def test_high_load_penalty(self):
        primary = _agent("a1", capabilities=["processing"])
        partner_idle = _agent("a2", capabilities=["validate", "verification"], load=0, max_load=3)
        partner_busy = _agent("a2", capabilities=["validate", "verification"], load=3, max_load=3)
        score_idle = _coalition_compatibility_score(primary, partner_idle)
        score_busy = _coalition_compatibility_score(primary, partner_busy)
        assert score_idle > score_busy

    def test_handoff_friction_penalty(self):
        primary = _agent("a1", capabilities=["processing"])
        partner = _agent("a2", capabilities=["validate", "verification"])
        score_clean = _coalition_compatibility_score(primary, partner, signals={})
        score_friction = _coalition_compatibility_score(primary, partner, signals={"handoff_friction": 0.8})
        assert score_clean > score_friction

    def test_joint_trust_signal(self):
        primary = _agent("a1", capabilities=["processing"])
        partner = _agent("a2", capabilities=["validate", "verification"])
        score_low = _coalition_compatibility_score(primary, partner, signals={"joint_trust": 0.2})
        score_high = _coalition_compatibility_score(primary, partner, signals={"joint_trust": 0.9})
        assert score_high > score_low


class TestAssembleCoalition:
    def test_successful_assembly(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        primary = _agent("planner-1", capabilities=["processing", "validate"], trust=0.9)
        partner = _agent("verifier-1", capabilities=["validate", "verification"], trust=0.85)
        other = _agent("exec-1", capabilities=["http_request"], trust=0.7)

        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=primary,
            primary_bid_score=0.8,
            all_agents=[primary, partner, other],
            username="alice",
        )
        assert result is not None
        assert len(result.members) == 2
        assert result.members[0].agent_id == "planner-1"
        assert result.members[1].agent_id == "verifier-1"
        assert result.fallback_agent_id == "planner-1"

    def test_solo_only_returns_none(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        result = assemble_coalition(
            task_spec=_spec(coordination_mode=CoordinationMode.SOLO_ONLY),
            primary_agent=_agent("a1"),
            primary_bid_score=0.8,
            all_agents=[_agent("a1"), _agent("a2", capabilities=["validate"])],
            username="alice",
        )
        assert result is None

    def test_solo_preferred_returns_none(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        result = assemble_coalition(
            task_spec=_spec(coordination_mode=CoordinationMode.SOLO_PREFERRED),
            primary_agent=_agent("a1"),
            primary_bid_score=0.8,
            all_agents=[_agent("a1"), _agent("a2", capabilities=["validate"])],
            username="alice",
        )
        assert result is None

    def test_no_verifier_returns_none(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        primary = _agent("a1", capabilities=["processing"])
        other = _agent("a2", capabilities=["http_request"])

        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=primary,
            primary_bid_score=0.8,
            all_agents=[primary, other],
            username="alice",
        )
        assert result is None

    def test_disabled_feature_gate(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "0")
        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=_agent("a1", capabilities=["processing"]),
            primary_bid_score=0.8,
            all_agents=[_agent("a1"), _agent("a2", capabilities=["validate"])],
            username="alice",
        )
        assert result is None

    def test_primary_not_in_partner_list(self, monkeypatch):
        """Primary agent should not be paired with itself."""
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        agent = _agent("same-agent", capabilities=["processing", "validate", "verification"])
        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=agent,
            primary_bid_score=0.8,
            all_agents=[agent],
            username="alice",
        )
        assert result is None

    def test_coalition_bid_fields(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        primary = _agent("p1", capabilities=["processing"], trust=0.9)
        partner = _agent("v1", capabilities=["validate", "verification"], trust=0.85)

        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=primary,
            primary_bid_score=0.8,
            all_agents=[primary, partner],
            username="alice",
        )
        assert result is not None
        assert result.coalition_id
        assert result.task_id == "t1"
        assert 0.0 <= result.joint_confidence <= 1.0
        assert 0.0 <= result.joint_trust_score <= 1.0
        assert result.expected_latency_ms > 0
        assert result.expected_cost > 0
        assert len(result.handoff_plan) >= 2

    def test_low_compat_returns_none(self, monkeypatch):
        """If compatibility is below threshold, don't form coalition."""
        monkeypatch.setenv("CNP_COALITION_ENABLE", "1")
        monkeypatch.setenv("CNP_COALITION_MIN_COMPAT", "0.99")
        primary = _agent("a1", capabilities=["processing"])
        partner = _agent("a2", capabilities=["validate"])
        result = assemble_coalition(
            task_spec=_spec(),
            primary_agent=primary,
            primary_bid_score=0.8,
            all_agents=[primary, partner],
            username="alice",
        )
        assert result is None
