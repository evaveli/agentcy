import pytest

from src.agentcy.agent_runtime.services.agent_utils import rank_agents_for_task, score_agent_for_task
from src.agentcy.pydantic_models.multi_agent_pipeline import TaskSpec


def test_score_agent_for_task_rewards_capability_and_status():
    spec = TaskSpec(
        task_id="task-1",
        username="alice",
        description="plan",
        required_capabilities=["plan"],
        tags=["core"],
    )
    agent = {
        "agent_id": "agent-1",
        "capabilities": ["plan"],
        "tags": ["core"],
        "status": "idle",
    }

    score = score_agent_for_task(agent, spec)
    assert score >= 0.8


def test_rank_agents_for_task_orders_by_score():
    spec = TaskSpec(
        task_id="task-2",
        username="bob",
        description="execute",
        required_capabilities=["execute"],
    )
    agents = [
        {"agent_id": "agent-low", "capabilities": ["plan"], "status": "busy"},
        {"agent_id": "agent-high", "capabilities": ["execute"], "status": "idle"},
    ]

    ranked = rank_agents_for_task(agents, spec)
    assert ranked[0]["agent"]["agent_id"] == "agent-high"
