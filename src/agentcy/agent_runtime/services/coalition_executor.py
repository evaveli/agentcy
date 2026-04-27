"""Structured mini-workflow execution for awarded coalitions.

A coalition execution is more constrained than solo — explicit step order,
max handoffs, structured intermediate outputs, and clean failure states.

Feature-gated behind ``CNP_COALITION_ENABLE=1``.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from agentcy.pydantic_models.multi_agent_pipeline import (
    CoalitionContract,
    CoalitionFailureState,
    CoalitionOutcome,
)

logger = logging.getLogger(__name__)


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _coalition_signature(contract: CoalitionContract) -> str:
    """Build canonical coalition signature from member roles, e.g. 'planner+verifier'."""
    roles = sorted(m.role.value for m in contract.members)
    return "+".join(roles)


def _member_ids(contract: CoalitionContract) -> List[str]:
    return [m.agent_id for m in contract.members]


async def execute_coalition(
    *,
    contract: CoalitionContract,
    run_step_fn: Any,
    message: Any,
    task_signature: str = "",
) -> CoalitionOutcome:
    """Execute a coalition contract as a structured mini-workflow.

    Parameters
    ----------
    contract:
        The materialised coalition contract (source of truth).
    run_step_fn:
        Async callable ``(agent_id, step_name, input_data) → output_data``
        that dispatches work to an agent and returns the result.
    message:
        The original task message / payload.
    task_signature:
        Task type identifier for outcome recording.

    Returns
    -------
    CoalitionOutcome with full metrics.
    """
    steps = contract.execution_plan.get("steps", [])
    max_handoffs = int(contract.execution_plan.get("max_handoffs", 2))
    overall_timeout_ms = int(contract.timeouts.get("overall_ms", 15000))
    step_timeout_ms = int(contract.timeouts.get("member_step_ms", 7000))

    if not steps:
        steps = [f"step_{i}" for i in range(len(contract.members))]
    if not contract.members:
        return CoalitionOutcome(
            coalition_id=contract.coalition_id,
            coalition_signature="empty",
            task_id=contract.task_id,
            task_signature=task_signature,
            success=False,
            failure_state=CoalitionFailureState.COALITION_ABORTED,
        )

    # Map step index to member (round-robin if more steps than members)
    member_count = len(contract.members)
    signature = _coalition_signature(contract)

    start_time = time.monotonic()
    handoff_count = 0
    handoff_failures = 0
    retries = 0
    current_output: Any = message
    step_results: List[Dict[str, Any]] = []

    for i, step_name in enumerate(steps):
        # Check overall timeout
        elapsed_ms = (time.monotonic() - start_time) * 1000
        if elapsed_ms > overall_timeout_ms:
            logger.warning("Coalition %s timed out at step %d/%d", contract.coalition_id, i, len(steps))
            return CoalitionOutcome(
                coalition_id=contract.coalition_id,
                coalition_signature=signature,
                members=_member_ids(contract),
                task_id=contract.task_id,
                task_signature=task_signature,
                success=False,
                retries=retries,
                handoff_failures=handoff_failures,
                latency_ms=int(elapsed_ms),
                failure_state=CoalitionFailureState.PARTNER_TIMEOUT,
            )

        member = contract.members[i % member_count]

        # Track handoffs (transitions between different agents)
        if i > 0 and contract.members[(i - 1) % member_count].agent_id != member.agent_id:
            handoff_count += 1
            if handoff_count > max_handoffs:
                logger.warning("Coalition %s exceeded max handoffs (%d)", contract.coalition_id, max_handoffs)
                return CoalitionOutcome(
                    coalition_id=contract.coalition_id,
                    coalition_signature=signature,
                    members=_member_ids(contract),
                    task_id=contract.task_id,
                    task_signature=task_signature,
                    success=False,
                    retries=retries,
                    handoff_failures=handoff_failures + 1,
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                    failure_state=CoalitionFailureState.HANDOFF_VALIDATION_FAILED,
                )

        # Execute step
        try:
            result = await run_step_fn(member.agent_id, step_name, current_output)
            step_results.append({
                "step": step_name,
                "agent_id": member.agent_id,
                "role": member.role.value,
                "success": True,
            })
            current_output = result
        except Exception as exc:
            logger.error(
                "Coalition %s step '%s' by %s failed: %s",
                contract.coalition_id, step_name, member.agent_id, exc,
            )
            handoff_failures += 1

            # Check if we should attempt fallback
            fallback_mode = contract.fallback.get("mode", "fail_fast")
            if fallback_mode == "degrade_to_solo":
                preferred = contract.fallback.get("preferred_agent_id")
                if preferred:
                    logger.info(
                        "Coalition %s degrading to solo agent %s",
                        contract.coalition_id, preferred,
                    )
                    try:
                        result = await run_step_fn(preferred, "solo_fallback", message)
                        elapsed_ms = (time.monotonic() - start_time) * 1000
                        return CoalitionOutcome(
                            coalition_id=contract.coalition_id,
                            coalition_signature=signature,
                            members=_member_ids(contract),
                            task_id=contract.task_id,
                            task_signature=task_signature,
                            success=True,
                            retries=retries + 1,
                            handoff_failures=handoff_failures,
                            latency_ms=int(elapsed_ms),
                            failure_state=CoalitionFailureState.FALLBACK_TO_SOLO,
                        )
                    except Exception:
                        pass

            elapsed_ms = (time.monotonic() - start_time) * 1000
            return CoalitionOutcome(
                coalition_id=contract.coalition_id,
                coalition_signature=signature,
                members=_member_ids(contract),
                task_id=contract.task_id,
                task_signature=task_signature,
                success=False,
                retries=retries,
                handoff_failures=handoff_failures,
                latency_ms=int(elapsed_ms),
                failure_state=CoalitionFailureState.COALITION_ABORTED,
            )

    # All steps completed successfully
    elapsed_ms = (time.monotonic() - start_time) * 1000
    return CoalitionOutcome(
        coalition_id=contract.coalition_id,
        coalition_signature=signature,
        members=_member_ids(contract),
        task_id=contract.task_id,
        task_signature=task_signature,
        success=True,
        retries=retries,
        handoff_failures=handoff_failures,
        latency_ms=int(elapsed_ms),
        quality_score=1.0 if handoff_failures == 0 else 0.8,
    )
