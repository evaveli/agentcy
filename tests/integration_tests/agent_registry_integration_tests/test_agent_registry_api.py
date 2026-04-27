import uuid

import pytest


@pytest.mark.asyncio
async def test_register_heartbeat_and_delete(http_client):
    username = "agent_registry_api"
    agent_id = f"agent-{uuid.uuid4()}"
    payload = {
        "agent_id": agent_id,
        "service_name": "planner",
        "status": "idle",
        "capabilities": ["planning"],
        "tags": ["core"],
    }

    resp = await http_client.post(
        f"/agent-registry/{username}?ttl_seconds=0", json=payload
    )
    assert resp.status_code == 201
    doc = resp.json()
    assert doc["agent_id"] == agent_id
    assert doc["service_name"] == "planner"

    hb = {"status": "busy", "metadata": {"load": 2}, "ttl_seconds": 0}
    resp = await http_client.post(
        f"/agent-registry/{username}/{agent_id}/heartbeat", json=hb
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["status"] == "busy"
    assert updated["metadata"]["load"] == 2

    resp = await http_client.get(f"/agent-registry/{username}/{agent_id}")
    assert resp.status_code == 200

    resp = await http_client.get(
        f"/agent-registry/{username}?service_name=planner&status=busy&tags=core"
    )
    assert resp.status_code == 200
    listed = resp.json()
    assert any(item["agent_id"] == agent_id for item in listed)

    resp = await http_client.delete(f"/agent-registry/{username}/{agent_id}")
    assert resp.status_code == 204
