import uuid

import pytest


@pytest.mark.asyncio
async def test_graph_builder_end_to_end(http_client):
    username = f"graph_builder_e2e_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    root_task = f"task-{uuid.uuid4()}"
    child_task = f"task-{uuid.uuid4()}"

    spec_root = {
        "task_id": root_task,
        "username": username,
        "description": "root task",
        "required_capabilities": ["plan"],
        "tags": ["core"],
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_root)
    assert resp.status_code == 201

    spec_child = {
        "task_id": child_task,
        "username": username,
        "description": "child task",
        "required_capabilities": ["execute"],
        "tags": ["core"],
        "metadata": {"depends_on": [root_task]},
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_child)
    assert resp.status_code == 201

    bid_low = {"task_id": root_task, "bidder_id": "agent-low", "bid_score": 0.3}
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid_low)
    assert resp.status_code == 201

    bid_high = {"task_id": root_task, "bidder_id": "agent-high", "bid_score": 0.95}
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid_high)
    assert resp.status_code == 201

    bid_child = {"task_id": child_task, "bidder_id": "agent-child", "bid_score": 0.7}
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid_child)
    assert resp.status_code == 201

    resp = await http_client.post(
        f"/graph-store/{username}/plan-drafts/build",
        json={"pipeline_id": pipeline_id},
    )
    assert resp.status_code == 201
    draft = resp.json()
    assert draft["pipeline_id"] == pipeline_id

    tasks = {task["task_id"]: task for task in draft["graph_spec"]["tasks"]}
    assert tasks[root_task]["assigned_agent"] == "agent-high"
    assert {"from": root_task, "to": child_task} in draft["graph_spec"]["edges"]
    assert tasks[root_task].get("award_id")

    plan_id = draft["plan_id"]
    resp = await http_client.get(f"/graph-store/{username}/plan-drafts/{plan_id}")
    assert resp.status_code == 200

    resp = await http_client.get(f"/graph-store/{username}/awards?task_id={root_task}")
    assert resp.status_code == 200
    assert resp.json()
