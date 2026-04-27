import asyncio
import os
import uuid
from copy import deepcopy

import httpx
import pytest

from tests.data.multi_agent_pipeline import MULTI_AGENT_PIPELINE_TEMPLATE
from tests.e2e_tests.helpers_e2e import publish_start_pipeline, poll_run_until, wait_until_registered


def _fuseki_base_url() -> str:
    return os.getenv("FUSEKI_URL", "http://localhost:3030").rstrip("/")


def _fuseki_dataset() -> str:
    return os.getenv("FUSEKI_DATASET", "agentcy")


def _fuseki_available(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/$/ping", timeout=2.0)
    except Exception:
        return False
    return resp.status_code == 200


def _graph_uri(plan_id: str) -> str:
    base = os.getenv("AGENTCY_BASE_URI", "http://agentcy.ai/")
    if not base.endswith("/"):
        base += "/"
    return f"{base}resource/graph/plan/{plan_id}"


def _sparql_count(base_url: str, dataset: str, graph_uri: str) -> int:
    query = f"SELECT (COUNT(*) AS ?count) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
    resp = httpx.post(
        f"{base_url}/{dataset}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=5.0,
    )
    resp.raise_for_status()
    data = resp.json()
    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return 0
    count_val = bindings[0].get("count", {}).get("value", "0")
    try:
        return int(count_val)
    except ValueError:
        return 0


@pytest.mark.asyncio
async def test_fuseki_ingest_e2e(http_client, monkeypatch):
    base_url = _fuseki_base_url()
    if not _fuseki_available(base_url):
        pytest.skip("Fuseki not reachable; skipping KG ingestion test")

    monkeypatch.setenv("FUSEKI_ENABLE", "1")
    monkeypatch.setenv("FUSEKI_URL", base_url)
    monkeypatch.setenv("FUSEKI_DATASET", _fuseki_dataset())
    monkeypatch.setenv("FUSEKI_USER", os.getenv("FUSEKI_USER", "admin"))
    monkeypatch.setenv("FUSEKI_PASSWORD", os.getenv("FUSEKI_PASSWORD", "admin"))

    username = f"kg_{uuid.uuid4().hex[:6]}"
    payload = deepcopy(MULTI_AGENT_PIPELINE_TEMPLATE)
    suffix = uuid.uuid4().hex[:8]
    payload["authors"] = [username]
    payload["name"] = f"kg_pipeline_{suffix}"
    payload["pipeline_name"] = f"kg_pipeline_{suffix}"
    for task in payload["dag"]["tasks"]:
        task.setdefault("action", "noop")

    resp = await http_client.post(f"/pipelines/{username}", json=payload)
    resp.raise_for_status()
    pipeline_id = resp.json()["pipeline_id"]

    await wait_until_registered(http_client, username, pipeline_id)

    task_a = f"kg-task-{uuid.uuid4().hex[:6]}"
    task_b = f"kg-task-{uuid.uuid4().hex[:6]}"
    spec_a = {
        "task_id": task_a,
        "username": username,
        "description": "kg plan task A",
        "required_capabilities": ["plan"],
        "tags": ["core"],
    }
    spec_b = {
        "task_id": task_b,
        "username": username,
        "description": "kg plan task B",
        "required_capabilities": ["execute"],
        "tags": ["core"],
        "metadata": {"depends_on": [task_a]},
    }
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_a)
    resp.raise_for_status()
    resp = await http_client.post(f"/graph-store/{username}/task-specs", json=spec_b)
    resp.raise_for_status()

    await publish_start_pipeline(username, pipeline_id, "e2e_run_cfg")

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
    plan_id = drafts[0]["plan_id"]

    graph_uri = _graph_uri(plan_id)
    count = _sparql_count(base_url, _fuseki_dataset(), graph_uri)
    assert count > 0
