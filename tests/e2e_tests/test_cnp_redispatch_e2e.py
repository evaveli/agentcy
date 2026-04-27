"""E2E smoke test for CNP task re-dispatch.

Validates that when a pipeline task fails, the tracker advances the
evaluation sequence to a fallback candidate, the forwarder retries
inline, and the pipeline reaches COMPLETED.
"""
import asyncio
import logging
import os
import uuid
from copy import deepcopy

import pytest

from tests.data.multi_agent_pipeline import MULTI_AGENT_PIPELINE_TEMPLATE
from tests.e2e_tests.helpers_e2e import (
    poll_run_until,
    publish_start_pipeline,
    wait_until_registered,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_cnp_redispatch_completes_after_failure(http_client, resource_manager_fixture):
    """
    Full E2E: pipeline task fails once -> tracker advances eval sequence
    -> forwarder retries -> task succeeds -> pipeline COMPLETED.
    """
    username = f"cnp_e2e_{uuid.uuid4().hex[:6]}"
    rm = resource_manager_fixture

    # 1. Register multi-agent pipeline
    payload = deepcopy(MULTI_AGENT_PIPELINE_TEMPLATE)
    suffix = uuid.uuid4().hex[:8]
    payload["authors"] = [username]
    payload["name"] = f"cnp_redispatch_{suffix}"
    payload["pipeline_name"] = f"cnp_redispatch_{suffix}"
    for task in payload["dag"]["tasks"]:
        task.setdefault("action", "noop")

    resp = await http_client.post(f"/pipelines/{username}", json=payload)
    resp.raise_for_status()
    pipeline_id = resp.json()["pipeline_id"]
    await wait_until_registered(http_client, username, pipeline_id)

    # 2. Pre-seed task specs and 2 bids for a target task so graph_builder
    #    creates an evaluation sequence with a fallback candidate.
    target_task = f"plan-task-{uuid.uuid4().hex[:6]}"
    spec = {
        "task_id": target_task,
        "username": username,
        "description": "CNP re-dispatch target task",
        "required_capabilities": ["execute"],
        "tags": ["core"],
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec)
    resp.raise_for_status()

    bid_primary = {
        "task_id": target_task,
        "bidder_id": "agent-primary",
        "bid_score": 0.9,
    }
    bid_fallback = {
        "task_id": target_task,
        "bidder_id": "agent-fallback",
        "bid_score": 0.7,
    }
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid_primary)
    resp.raise_for_status()
    resp = await http_client.post(f"/graph-store/{username}/bids", json=bid_fallback)
    resp.raise_for_status()

    # 3. Set up failure injection for system_executor
    #    (will fail once, then succeed on the forwarder's retry)
    from tests.conftest import _FAILURE_INJECTIONS
    _FAILURE_INJECTIONS[(username, "system_executor")] = 1

    try:
        # 4. Start pipeline
        await publish_start_pipeline(username, pipeline_id, "e2e_run_cfg")

        # 5. Wait for run_id to appear
        run_id = None
        runs_url = f"/pipelines/{username}/{pipeline_id}/runs?latest_run=true"
        for _ in range(60):
            resp = await http_client.get(runs_url)
            if resp.status_code == 200:
                run_id = resp.json().get("run_id")
                if run_id:
                    break
            await asyncio.sleep(0.5)
        assert run_id, "Run document never appeared within 30s"

        # 6. Poll until terminal state
        timeout = int(os.getenv("E2E_RUN_TIMEOUT", "180"))
        final = await poll_run_until(
            http_client, username, pipeline_id, run_id,
            target_status="COMPLETED", timeout=timeout,
        )
        assert final["status"] == "COMPLETED"

        # 7. Verify system_execution task actually ran (not skipped)
        tasks = final.get("tasks", {})
        sys_exec = tasks.get("system_execution", {})
        assert sys_exec.get("status") == "COMPLETED", (
            f"system_execution should be COMPLETED, got {sys_exec.get('status')}"
        )

        # 8. Verify CNP artifacts were created
        resp = await http_client.get(f"/graph-store/{username}/awards")
        resp.raise_for_status()
        awards = resp.json()
        assert awards, "Expected at least one contract award"

        logger.info(
            "CNP re-dispatch E2E passed: pipeline=%s run=%s awards=%d",
            pipeline_id, run_id, len(awards),
        )

    finally:
        _FAILURE_INJECTIONS.pop((username, "system_executor"), None)
