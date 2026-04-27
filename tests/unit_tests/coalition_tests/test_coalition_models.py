"""Tests for coalition Pydantic models: validation, defaults, serialization."""
import pytest
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    BlueprintBid,
    CoalitionBid,
    CoalitionContract,
    CoalitionFailureState,
    CoalitionMember,
    CoalitionOutcome,
    CoalitionRole,
    CoalitionSignal,
    CoordinationMode,
    TaskSpec,
)


class TestCoordinationMode:
    def test_all_values(self):
        for mode in ("solo_only", "solo_preferred", "coalition_allowed", "coalition_required"):
            assert CoordinationMode(mode).value == mode

    def test_taskspec_default(self):
        spec = TaskSpec(task_id="t1", username="u", description="d")
        assert spec.coordination_mode == CoordinationMode.SOLO_PREFERRED

    def test_taskspec_coalition_allowed(self):
        spec = TaskSpec(
            task_id="t1", username="u", description="d",
            coordination_mode=CoordinationMode.COALITION_ALLOWED,
        )
        assert spec.coordination_mode == CoordinationMode.COALITION_ALLOWED

    def test_taskspec_backward_compat(self):
        """Old TaskSpec dicts without coordination_mode should still validate."""
        d = {"task_id": "t1", "username": "u", "description": "d"}
        spec = TaskSpec.model_validate(d)
        assert spec.coordination_mode == CoordinationMode.SOLO_PREFERRED


class TestBlueprintBidType:
    def test_default_solo(self):
        bid = BlueprintBid(task_id="t1", bidder_id="a1", bid_score=0.8)
        assert bid.bid_type == "solo"

    def test_coalition_type(self):
        bid = BlueprintBid(task_id="t1", bidder_id="a1", bid_score=0.8, bid_type="coalition")
        assert bid.bid_type == "coalition"

    def test_backward_compat(self):
        d = {"task_id": "t1", "bidder_id": "a1", "bid_score": 0.8}
        bid = BlueprintBid.model_validate(d)
        assert bid.bid_type == "solo"


class TestCoalitionMember:
    def test_all_roles(self):
        for role in CoalitionRole:
            m = CoalitionMember(agent_id="a1", role=role)
            assert m.role == role

    def test_json_roundtrip(self):
        m = CoalitionMember(agent_id="a1", role=CoalitionRole.VERIFIER)
        d = m.model_dump(mode="json")
        restored = CoalitionMember.model_validate(d)
        assert restored.agent_id == "a1"
        assert restored.role == CoalitionRole.VERIFIER


class TestCoalitionBid:
    def test_defaults(self):
        bid = CoalitionBid(
            task_id="t1",
            members=[
                CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER),
                CoalitionMember(agent_id="a2", role=CoalitionRole.VERIFIER),
            ],
        )
        assert bid.coalition_id  # auto UUID
        assert bid.fallback_mode == "degrade_to_solo"
        assert bid.joint_confidence == 0.0

    def test_full(self):
        bid = CoalitionBid(
            task_id="t1",
            members=[
                CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER),
                CoalitionMember(agent_id="a2", role=CoalitionRole.VERIFIER),
            ],
            handoff_plan=["plan", "verify", "finalize"],
            joint_confidence=0.92,
            expected_latency_ms=8000,
            expected_cost=0.30,
            joint_trust_score=0.85,
            fallback_mode="fail_fast",
        )
        assert bid.joint_confidence == 0.92
        assert len(bid.handoff_plan) == 3

    def test_json_roundtrip(self):
        bid = CoalitionBid(
            task_id="t1",
            members=[CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER)],
            joint_confidence=0.88,
        )
        d = bid.model_dump(mode="json")
        restored = CoalitionBid.model_validate(d)
        assert restored.joint_confidence == 0.88
        assert restored.members[0].role == CoalitionRole.PLANNER


class TestCoalitionContract:
    def test_defaults(self):
        c = CoalitionContract(task_id="t1")
        assert c.status == "awarded"
        assert c.timeouts["overall_ms"] == 15000
        assert c.fallback["mode"] == "degrade_to_solo"

    def test_full(self):
        c = CoalitionContract(
            task_id="t1",
            members=[
                CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER),
                CoalitionMember(agent_id="a2", role=CoalitionRole.VERIFIER),
            ],
            execution_plan={"steps": ["plan", "verify"], "max_handoffs": 2},
            policy={"verification_required": True},
        )
        assert c.execution_plan["max_handoffs"] == 2
        assert c.policy["verification_required"] is True

    def test_json_roundtrip(self):
        c = CoalitionContract(task_id="t1", status="executing")
        d = c.model_dump(mode="json")
        restored = CoalitionContract.model_validate(d)
        assert restored.status == "executing"


class TestCoalitionOutcome:
    def test_success(self):
        o = CoalitionOutcome(
            coalition_id="c1",
            coalition_signature="planner+verifier",
            members=["a1", "a2"],
            task_id="t1",
            success=True,
            latency_ms=8000,
            cost_actual=0.34,
            quality_score=0.95,
        )
        assert o.failure_state is None
        assert o.handoff_failures == 0

    def test_failure(self):
        o = CoalitionOutcome(
            coalition_id="c1",
            coalition_signature="planner+verifier",
            success=False,
            failure_state=CoalitionFailureState.PARTNER_TIMEOUT,
            handoff_failures=1,
        )
        assert o.failure_state == CoalitionFailureState.PARTNER_TIMEOUT

    def test_all_failure_states(self):
        for state in CoalitionFailureState:
            o = CoalitionOutcome(
                coalition_id="c1", coalition_signature="x",
                failure_state=state,
            )
            assert o.failure_state == state

    def test_json_roundtrip(self):
        o = CoalitionOutcome(
            coalition_id="c1", coalition_signature="planner+verifier",
            success=True, latency_ms=5000,
        )
        d = o.model_dump(mode="json")
        restored = CoalitionOutcome.model_validate(d)
        assert restored.latency_ms == 5000


class TestCoalitionSignal:
    def test_joint_trust(self):
        s = CoalitionSignal(
            signal_type="joint_trust",
            coalition_signature="planner+verifier",
            task_signature="plan.policy",
            score=0.84,
            sample_size=27,
        )
        assert s.signal_type == "joint_trust"

    def test_handoff_friction(self):
        s = CoalitionSignal(
            signal_type="handoff_friction",
            coalition_signature="planner+verifier",
            score=0.18,
        )
        assert s.score == 0.18

    def test_json_roundtrip(self):
        s = CoalitionSignal(
            signal_type="coalition_overhead",
            coalition_signature="executor+safety",
            score=0.12,
            sample_size=5,
        )
        d = s.model_dump(mode="json")
        restored = CoalitionSignal.model_validate(d)
        assert restored.signal_type == "coalition_overhead"
        assert restored.sample_size == 5
