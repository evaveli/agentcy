import json

from agentcy.agents import multi_agent_stubs as stubs
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, TaskStatus


def _task(task_id="t1", service="svc"):
    return TaskState(
        status=TaskStatus.PENDING,
        attempts=0,
        error=None,
        result=None,
        output_ref="",
        is_final_task=False,
        pipeline_run_id="run-123",
        task_id=task_id,
        username="alice",
        pipeline_config_id="cfg-1",
        pipeline_id="pipe-1",
        service_name=service,
        data={"triggered_by": "unit"},
    )


def _assert_stage(raw: dict, stage: str):
    assert raw["raw_output"]
    parsed = json.loads(raw["raw_output"])
    assert parsed["stage"] == stage
    assert parsed["pipeline_run_id"] == "run-123"


async def test_stub_functions_return_contract():
    # spot-check a few representative agents
    _assert_stage(await stubs.agent_registration(_task("register_agents", "agent_registration")), "agent_registration")
    _assert_stage(await stubs.blueprint_bidder(_task("blueprint_bid", "blueprint_bidder")), "blueprint_bidder")
    _assert_stage(await stubs.audit_logger(_task("audit_and_trace", "audit_logger")), "audit_logger")
