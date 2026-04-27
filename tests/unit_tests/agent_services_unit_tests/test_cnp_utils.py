from src.agentcy.agent_runtime.services.cnp_utils import (
    agent_cnp_state,
    score_bid,
    task_params,
    update_cnp_metadata,
)
from src.agentcy.pydantic_models.multi_agent_pipeline import RiskLevel, TaskSpec


def test_task_params_defaults_from_risk_level():
    spec = TaskSpec(
        task_id="task-1",
        username="alice",
        description="demo",
        risk_level=RiskLevel.HIGH,
    )
    params = task_params(spec)
    assert params["priority"] == 5
    assert 0.0 <= params["stimulus"] <= 1.0
    assert 0.1 <= params["reward"] <= 6.0
    assert params["task_type"] == "general"


def test_update_cnp_metadata_tracks_thresholds_and_counts():
    agent_doc = {"agent_id": "agent-1", "metadata": {"cnp": {"tasks_received": 2}}}
    metadata = update_cnp_metadata(
        agent_doc=agent_doc,
        task_type="plan",
        stimulus=0.6,
        priority=3,
        reward=2.5,
        success=True,
    )
    state = agent_cnp_state({"metadata": metadata})
    assert state["tasks_acquired"] == 1
    assert state["tasks_completed"] == 1
    assert state["thresholds"]["plan"] >= 0.0


def test_score_bid_penalizes_cost_and_load():
    trust = 0.8
    score_low_cost = score_bid(trust=trust, cost=1.0, load=0, tmin=1.0, tmax=3.0, lmin=0, lmax=3)
    score_high_cost = score_bid(trust=trust, cost=3.0, load=0, tmin=1.0, tmax=3.0, lmin=0, lmax=3)
    score_high_load = score_bid(trust=trust, cost=1.0, load=3, tmin=1.0, tmax=3.0, lmin=0, lmax=3)
    assert score_low_cost > score_high_cost
    assert score_low_cost > score_high_load
