import uuid

import pytest


@pytest.mark.asyncio
async def test_graph_store_end_to_end(http_client):
    username = f"graph_e2e_{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    spec = {
        "task_id": task_id,
        "username": username,
        "description": "e2e task",
        "required_capabilities": ["plan"],
        "tags": ["core"],
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec)
    assert resp.status_code == 201

    affordance = {"task_id": task_id, "agent_id": "agent-a"}
    resp = await http_client.post(
        f"/graph-store/{username}/markers/affordance", json=affordance
    )
    assert resp.status_code == 201

    reservation = {"task_id": task_id, "agent_id": "agent-a"}
    resp = await http_client.post(
        f"/graph-store/{username}/markers/reservation", json=reservation
    )
    assert resp.status_code == 201

    bid = {"task_id": task_id, "bidder_id": "agent-a", "bid_score": 0.9}
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid)
    assert resp.status_code == 201

    draft = {
        "plan_id": plan_id,
        "username": username,
        "pipeline_id": "pipeline-1",
        "graph_spec": {"nodes": [task_id], "edges": []},
    }
    resp = await http_client.post(f"/graph-store/{username}/plan-drafts", json=draft)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/task-specs")
    assert resp.status_code == 200
    assert any(item.get("task_id") == task_id for item in resp.json())

    resp = await http_client.get(
        f"/graph-store/{username}/markers/affordance?task_id={task_id}"
    )
    assert resp.status_code == 200
    assert any(item.get("agent_id") == "agent-a" for item in resp.json())

    resp = await http_client.get(
        f"/graph-store/{username}/markers/reservation?task_id={task_id}"
    )
    assert resp.status_code == 200
    assert any(item.get("agent_id") == "agent-a" for item in resp.json())

    resp = await http_client.get(
        f"/graph-store/{username}/bids?task_id={task_id}"
    )
    assert resp.status_code == 200
    assert any(item.get("bidder_id") == "agent-a" for item in resp.json())

    resp = await http_client.get(
        f"/graph-store/{username}/plan-drafts?pipeline_id=pipeline-1"
    )
    assert resp.status_code == 200
    assert any(item.get("plan_id") == plan_id for item in resp.json())

    cfp = {"task_id": task_id, "required_capabilities": ["plan"]}
    resp = await http_client.post(f"/graph-store/{username}/cfps", json=cfp)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/cfps?task_id={task_id}")
    assert resp.status_code == 200
    cfps = resp.json()
    assert cfps
    cfp_id = cfps[0]["cfp_id"]

    award = {"task_id": task_id, "bidder_id": "agent-a", "cfp_id": cfp_id}
    resp = await http_client.post(f"/graph-store/{username}/awards", json=award)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/awards?task_id={task_id}")
    assert resp.status_code == 200
    assert resp.json()

    approval = {"plan_id": plan_id, "username": username, "approver": "tester", "approved": True}
    resp = await http_client.post(f"/graph-store/{username}/human-approvals", json=approval)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/human-approvals?plan_id={plan_id}")
    assert resp.status_code == 200
    assert resp.json()

    ethics = {"plan_id": plan_id, "approved": True}
    resp = await http_client.post(f"/graph-store/{username}/ethics-checks", json=ethics)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/ethics-checks?plan_id={plan_id}")
    assert resp.status_code == 200
    assert resp.json()

    strategy = {"plan_id": plan_id, "summary": "do it"}
    resp = await http_client.post(f"/graph-store/{username}/strategy-plans", json=strategy)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/strategy-plans?plan_id={plan_id}")
    assert resp.status_code == 200
    assert resp.json()

    report = {"plan_id": plan_id, "success_rate": 1.0}
    resp = await http_client.post(f"/graph-store/{username}/execution-reports", json=report)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/execution-reports?plan_id={plan_id}")
    assert resp.status_code == 200
    assert resp.json()

    run_id = f"run-{uuid.uuid4()}"
    audit = {"event_type": "test", "pipeline_run_id": run_id, "actor": "tester"}
    resp = await http_client.post(f"/graph-store/{username}/audit-logs", json=audit)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/audit-logs?pipeline_run_id={run_id}")
    assert resp.status_code == 200
    assert resp.json()

    escalation = {"pipeline_run_id": run_id, "reason": "fail"}
    resp = await http_client.post(f"/graph-store/{username}/escalations", json=escalation)
    assert resp.status_code == 201

    resp = await http_client.get(f"/graph-store/{username}/escalations?pipeline_run_id={run_id}")
    assert resp.status_code == 200
    assert resp.json()
