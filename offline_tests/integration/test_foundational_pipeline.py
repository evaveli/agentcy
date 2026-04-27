import json

import pytest

from agentcy.agents import foundational_agents as fa
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, TaskStatus


def _base_task():
    return TaskState(
        status=TaskStatus.PENDING,
        attempts=0,
        error=None,
        result=None,
        output_ref="",
        is_final_task=False,
        pipeline_run_id="run-int",
        task_id="register_agents",
        username="alice",
        pipeline_config_id="cfg-int",
        pipeline_id="pipe-int",
        service_name="agent_registration",
        data={"risk_level": "high"},
    )


async def _step(fn, task):
    out = await fn(task)
    # simulate forwarder enrichment by merging result into task.data
    task.data.update(out["result"])
    return out


@pytest.mark.asyncio
async def test_planning_flow_propagates_plan_and_risk():
    task = _base_task()

    await _step(fa.agent_registration, task)
    await _step(fa.input_validator, task)
    assert task.data["requires_human_approval"] is True

    await _step(fa.path_seeder, task)
    bid_out = await _step(fa.blueprint_bidder, task)
    assert "bid_score" in bid_out["result"]

    await _step(fa.graph_builder, task)
    validate_out = await _step(fa.plan_validator, task)
    assert validate_out["result"]["is_valid"] is True
    assert validate_out["result"]["plan_id"]

    cache_out = await _step(fa.plan_cache, task)
    assert cache_out["result"]["cache_hit"] is False

    human_out = await _step(fa.human_validator, task)
    assert human_out["result"]["approved"] is True

    strat_out = await _step(fa.llm_strategist, task)
    assert "strategy" in strat_out["result"]

    ethics_out = await _step(fa.ethics_checker, task)
    assert ethics_out["result"]["ethics_approved"] is True

    exec_out = await _step(fa.system_executor, task)
    assert exec_out["result"]["execution_status"] == "ok"

    pher_out = await _step(fa.pheromone_engine, task)
    assert 0 < pher_out["result"]["pheromone"] <= 1.0

    escalate_out = await _step(fa.failure_escalation, task)
    assert escalate_out["result"]["escalated"] is False

    audit_out = await _step(fa.audit_logger, task)
    parsed = json.loads(audit_out["raw_output"])
    assert parsed["result"]["logged"] is True
    assert fa.AUDIT_LOGS[-1]["pipeline_run_id"] == "run-int"
