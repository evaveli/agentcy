import pytest

from agentcy.agents import foundational_agents as fa
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, TaskStatus


def _task(run_id: str):
    return TaskState(
        status=TaskStatus.PENDING,
        attempts=0,
        error=None,
        result=None,
        output_ref="",
        is_final_task=False,
        pipeline_run_id=run_id,
        task_id="register_agents",
        username="alice",
        pipeline_config_id="cfg-e2e",
        pipeline_id="pipe-e2e",
        service_name="agent_registration",
        data={},
    )


async def _run_once(run_id: str):
    t = _task(run_id)
    for fn in (
        fa.agent_registration,
        fa.input_validator,
        fa.path_seeder,
        fa.blueprint_bidder,
        fa.graph_builder,
        fa.plan_validator,
        fa.plan_cache,
        fa.human_validator,
        fa.llm_strategist,
        fa.ethics_checker,
        fa.system_executor,
        fa.pheromone_engine,
        fa.failure_escalation,
        fa.audit_logger,
    ):
        out = await fn(t)
        t.data.update(out["result"])
    return t.data


@pytest.mark.asyncio
async def test_plan_cache_reuses_between_runs_and_audit_accumulates():
    first = await _run_once("run-e2e-1")
    assert first["cache_hit"] is False
    assert len(fa.AUDIT_LOGS) == 1

    second = await _run_once("run-e2e-2")
    # plan_hash deterministic by plan_graph/seed -> should hit cache second time
    assert second["cache_hit"] is True
    assert len(fa.AUDIT_LOGS) == 2
