import asyncio
import logging
import uuid
from copy import deepcopy

import pytest
import os

from tests.conftest import kickoff_response
from tests.data.complex_payload import COMPLEX_PIPELINE_PAYLOAD_TEMPLATE
from tests.data.multi_agent_pipeline import MULTI_AGENT_PIPELINE_TEMPLATE
from tests.e2e_tests.helpers_e2e import (
    poll_run_until,
    publish_start_pipeline,
    wait_for_run,
    wait_until_registered,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------
TEST_USER = "e2e_tester"
RUN_CONFIG = "e2e_run_cfg"


@pytest.mark.asyncio
async def test_happy_path(kickoff_response, http_client):
    pipeline_id = kickoff_response["pipeline_id"]
    run_id      = kickoff_response["run_id"]
    timeout = int(os.getenv("E2E_RUN_TIMEOUT", "120"))
    final = await wait_for_run(http_client, pipeline_id, run_id, timeout=timeout)
    assert final["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_multi_agent_pipeline_runs_graph_builder(http_client):
    username = f"multi_agent_{uuid.uuid4().hex[:6]}"
    payload = deepcopy(MULTI_AGENT_PIPELINE_TEMPLATE)
    suffix = uuid.uuid4().hex[:8]
    payload["authors"] = [username]
    payload["name"] = f"multi_agent_{suffix}"
    payload["pipeline_name"] = f"multi_agent_{suffix}"
    for task in payload["dag"]["tasks"]:
        task.setdefault("action", "noop")

    resp = await http_client.post(f"/pipelines/{username}", json=payload)
    resp.raise_for_status()
    pipeline_id = resp.json()["pipeline_id"]

    await wait_until_registered(http_client, username, pipeline_id)

    task_a = f"plan-task-{uuid.uuid4().hex[:6]}"
    task_b = f"plan-task-{uuid.uuid4().hex[:6]}"
    spec_a = {
        "task_id": task_a,
        "username": username,
        "description": "e2e plan task A",
        "required_capabilities": ["plan"],
        "tags": ["core"],
    }
    spec_b = {
        "task_id": task_b,
        "username": username,
        "description": "e2e plan task B",
        "required_capabilities": ["execute"],
        "tags": ["core"],
        "metadata": {"depends_on": [task_a]},
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_a)
    resp.raise_for_status()
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_b)
    resp.raise_for_status()

    await publish_start_pipeline(username, pipeline_id, RUN_CONFIG)

    run_id = None
    runs_url = f"/pipelines/{username}/{pipeline_id}/runs?latest_run=true"
    for _ in range(60):
        resp = await http_client.get(runs_url)
        if resp.status_code == 200:
            run_id = resp.json().get("run_id")
            if run_id:
                break
        await asyncio.sleep(0.5)
    assert run_id

    timeout = int(os.getenv("E2E_RUN_TIMEOUT", "180"))
    final = await poll_run_until(http_client, username, pipeline_id, run_id, timeout=timeout)
    assert final["status"] == "COMPLETED"

    resp = await http_client.get(
        f"/graph-store/{username}/plan-drafts?pipeline_id={pipeline_id}"
    )
    resp.raise_for_status()
    drafts = resp.json()
    assert drafts
    assert any(d.get("is_valid") for d in drafts)
    assert any(d.get("cached") for d in drafts)

    resp = await http_client.get(f"/graph-store/{username}/bids")
    resp.raise_for_status()
    bids = resp.json()
    assert bids
    assert {task_a, task_b}.issubset({bid.get("task_id") for bid in bids})

    resp = await http_client.get(f"/graph-store/{username}/markers/affordance")
    resp.raise_for_status()
    markers = resp.json()
    assert markers

    resp = await http_client.get(f"/graph-store/{username}/cfps")
    resp.raise_for_status()
    cfps = resp.json()
    assert cfps

    resp = await http_client.get(f"/graph-store/{username}/awards")
    resp.raise_for_status()
    awards = resp.json()
    assert awards

    resp = await http_client.get(
        f"/graph-store/{username}/human-approvals?plan_id={drafts[0]['plan_id']}"
    )
    resp.raise_for_status()
    approvals = resp.json()
    assert approvals

    resp = await http_client.get(
        f"/graph-store/{username}/ethics-checks?plan_id={drafts[0]['plan_id']}"
    )
    resp.raise_for_status()
    ethics_checks = resp.json()
    assert ethics_checks

    resp = await http_client.get(
        f"/graph-store/{username}/strategy-plans?plan_id={drafts[0]['plan_id']}"
    )
    resp.raise_for_status()
    strategies = resp.json()
    assert strategies

    resp = await http_client.get(
        f"/graph-store/{username}/execution-reports?plan_id={drafts[0]['plan_id']}"
    )
    resp.raise_for_status()
    exec_reports = resp.json()
    assert exec_reports

    resp = await http_client.get(
        f"/graph-store/{username}/audit-logs?pipeline_run_id={run_id}"
    )
    resp.raise_for_status()
    audits = resp.json()
    assert audits

    resp = await http_client.get(
        f"/graph-store/{username}/escalations?pipeline_run_id={run_id}"
    )
    resp.raise_for_status()
    escalations = resp.json()
    assert escalations
