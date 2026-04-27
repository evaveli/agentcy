import json

import pytest

from agentcy.agents import foundational_agents as fa
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, TaskStatus


def _task(task_id="register_agents", service="agent_registration", data=None):
    return TaskState(
        status=TaskStatus.PENDING,
        attempts=0,
        error=None,
        result=None,
        output_ref="",
        is_final_task=False,
        pipeline_run_id="run-1",
        task_id=task_id,
        username="alice",
        pipeline_config_id="cfg-1",
        pipeline_id="pipe-1",
        service_name=service,
        data=data or {},
    )


@pytest.mark.asyncio
async def test_agent_registration_contract():
    out = await fa.agent_registration(_task(data={"capabilities": ["plan"]}))
    assert "raw_output" in out and out["raw_output"]
    parsed = json.loads(out["raw_output"])
    assert parsed["stage"] == "agent_registration"
    assert out["result"]["registered"] is True
    assert out["result"]["capabilities"] == ["plan"]


@pytest.mark.asyncio
async def test_plan_cache_sets_and_hits():
    task = _task(task_id="plan_cache", service="plan_cache", data={"plan_id": "p1", "plan_hash": "hash1"})
    first = await fa.plan_cache(task)
    assert first["result"]["cache_hit"] is False
    second = await fa.plan_cache(task)
    assert second["result"]["cache_hit"] is True
    assert second["result"]["plan_id"] == "p1"
