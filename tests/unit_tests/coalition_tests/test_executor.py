"""Tests for coalition execution: handoff plan, failure states, fallback, timeouts."""
import pytest
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    CoalitionContract,
    CoalitionFailureState,
    CoalitionMember,
    CoalitionRole,
)
from src.agentcy.agent_runtime.services.coalition_executor import execute_coalition


def _contract(steps=None, max_handoffs=2, fallback_mode="degrade_to_solo",
              overall_ms=30000, step_ms=10000, members=None) -> CoalitionContract:
    if members is None:
        members = [
            CoalitionMember(agent_id="planner-1", role=CoalitionRole.PLANNER),
            CoalitionMember(agent_id="verifier-1", role=CoalitionRole.VERIFIER),
        ]
    if steps is None:
        steps = ["plan", "verify", "finalize"]
    return CoalitionContract(
        task_id="t1",
        members=members,
        execution_plan={"steps": steps, "max_handoffs": max_handoffs},
        timeouts={"overall_ms": overall_ms, "member_step_ms": step_ms},
        fallback={"mode": fallback_mode, "preferred_agent_id": members[0].agent_id if members else None},
    )


async def _success_fn(agent_id, step_name, input_data):
    return {"raw_output": f"{agent_id} completed {step_name}"}


async def _fail_on_verify(agent_id, step_name, input_data):
    if "verify" in step_name:
        raise RuntimeError("verification failed")
    return {"raw_output": f"{agent_id} completed {step_name}"}


async def _always_fail(agent_id, step_name, input_data):
    raise RuntimeError("everything fails")


# ── Success cases ────────────────────────────────────────────────────────


class TestCoalitionSuccess:
    @pytest.mark.asyncio
    async def test_all_steps_succeed(self):
        outcome = await execute_coalition(
            contract=_contract(),
            run_step_fn=_success_fn,
            message={"data": "test"},
            task_signature="plan.policy",
        )
        assert outcome.success is True
        assert outcome.failure_state is None
        assert outcome.handoff_failures == 0
        assert outcome.coalition_signature == "planner+verifier"
        assert set(outcome.members) == {"planner-1", "verifier-1"}

    @pytest.mark.asyncio
    async def test_single_step(self):
        c = _contract(steps=["only_step"], members=[
            CoalitionMember(agent_id="a1", role=CoalitionRole.EXECUTOR),
        ])
        outcome = await execute_coalition(contract=c, run_step_fn=_success_fn, message={})
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_quality_score_perfect(self):
        outcome = await execute_coalition(
            contract=_contract(), run_step_fn=_success_fn, message={},
        )
        assert outcome.quality_score == 1.0


# ── Failure and fallback ─────────────────────────────────────────────────


class TestCoalitionFailure:
    @pytest.mark.asyncio
    async def test_fallback_to_solo(self):
        outcome = await execute_coalition(
            contract=_contract(fallback_mode="degrade_to_solo"),
            run_step_fn=_fail_on_verify,
            message={"data": "test"},
        )
        assert outcome.failure_state == CoalitionFailureState.FALLBACK_TO_SOLO
        assert outcome.success is True  # Fallback succeeded
        assert outcome.handoff_failures == 1

    @pytest.mark.asyncio
    async def test_fail_fast_no_fallback(self):
        outcome = await execute_coalition(
            contract=_contract(fallback_mode="fail_fast"),
            run_step_fn=_fail_on_verify,
            message={},
        )
        assert outcome.success is False
        assert outcome.failure_state == CoalitionFailureState.COALITION_ABORTED

    @pytest.mark.asyncio
    async def test_all_steps_fail(self):
        outcome = await execute_coalition(
            contract=_contract(fallback_mode="fail_fast"),
            run_step_fn=_always_fail,
            message={},
        )
        assert outcome.success is False
        assert outcome.failure_state == CoalitionFailureState.COALITION_ABORTED

    @pytest.mark.asyncio
    async def test_fallback_also_fails(self):
        async def both_fail(agent_id, step_name, input_data):
            raise RuntimeError("nope")

        outcome = await execute_coalition(
            contract=_contract(fallback_mode="degrade_to_solo"),
            run_step_fn=both_fail,
            message={},
        )
        assert outcome.success is False
        assert outcome.failure_state == CoalitionFailureState.COALITION_ABORTED


# ── Timeout ──────────────────────────────────────────────────────────────


class TestCoalitionTimeout:
    @pytest.mark.asyncio
    async def test_overall_timeout(self):
        import time

        async def slow_fn(agent_id, step_name, input_data):
            time.sleep(0.05)  # 50ms per step
            return {"raw_output": "done"}

        # 10ms overall timeout — should fail on first or second step
        outcome = await execute_coalition(
            contract=_contract(overall_ms=10, steps=["s1", "s2", "s3"]),
            run_step_fn=slow_fn,
            message={},
        )
        # First step takes 50ms > 10ms timeout, so second step should hit timeout
        assert outcome.failure_state == CoalitionFailureState.PARTNER_TIMEOUT
        assert outcome.success is False


# ── Max handoffs ─────────────────────────────────────────────────────────


class TestMaxHandoffs:
    @pytest.mark.asyncio
    async def test_exceeds_max_handoffs(self):
        # 5 steps alternating between 2 agents = 4 handoffs, but max is 2
        c = _contract(
            steps=["s1", "s2", "s3", "s4", "s5"],
            max_handoffs=2,
        )
        outcome = await execute_coalition(
            contract=c, run_step_fn=_success_fn, message={},
        )
        assert outcome.failure_state == CoalitionFailureState.HANDOFF_VALIDATION_FAILED
        assert outcome.success is False

    @pytest.mark.asyncio
    async def test_within_max_handoffs(self):
        # 3 steps, 2 agents → 2 handoffs exactly at limit
        c = _contract(steps=["s1", "s2", "s3"], max_handoffs=2)
        outcome = await execute_coalition(
            contract=c, run_step_fn=_success_fn, message={},
        )
        assert outcome.success is True


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_members(self):
        c = CoalitionContract(
            task_id="t1",
            members=[],
            execution_plan={"steps": ["s1"]},
        )
        outcome = await execute_coalition(
            contract=c, run_step_fn=_success_fn, message={},
        )
        assert outcome.success is False
        assert outcome.failure_state == CoalitionFailureState.COALITION_ABORTED

    @pytest.mark.asyncio
    async def test_empty_execution_plan(self):
        c = _contract(steps=[])
        outcome = await execute_coalition(
            contract=c, run_step_fn=_success_fn, message={},
        )
        # No steps → immediately successful (vacuously true)
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_task_signature_propagated(self):
        outcome = await execute_coalition(
            contract=_contract(),
            run_step_fn=_success_fn,
            message={},
            task_signature="customs.compliance.strict",
        )
        assert outcome.task_signature == "customs.compliance.strict"

    @pytest.mark.asyncio
    async def test_coalition_signature_sorted(self):
        """Signature should be alphabetically sorted roles."""
        c = _contract(members=[
            CoalitionMember(agent_id="v1", role=CoalitionRole.VERIFIER),
            CoalitionMember(agent_id="p1", role=CoalitionRole.PLANNER),
        ])
        outcome = await execute_coalition(contract=c, run_step_fn=_success_fn, message={})
        assert outcome.coalition_signature == "planner+verifier"
