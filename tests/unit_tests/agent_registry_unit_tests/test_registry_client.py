import asyncio
import json

import httpx
import pytest

from src.agentcy.agent_runtime.registry_client import (
    AgentRegistryClient,
    AgentRegistryConfig,
    AgentStatus,
    heartbeat_loop,
)


def _config() -> AgentRegistryConfig:
    return AgentRegistryConfig(
        base_url="http://registry.test",
        username="alice",
        agent_id="agent-1",
        service_name="planner",
        description=None,
        capabilities=["planning"],
        tags=["core"],
        heartbeat_interval=0.05,
        ttl_seconds=10,
        timeout_seconds=2,
        failure_threshold=3,
    )


@pytest.mark.asyncio
async def test_register_posts_agent_entry():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry.test") as client:
        registry = AgentRegistryClient(_config(), client=client)
        ok = await registry.register()

    assert ok is True
    assert requests
    req = requests[0]
    assert req.method == "POST"
    assert req.url.path == "/agent-registry/alice"
    body = json.loads(req.content.decode())
    assert body["agent_id"] == "agent-1"
    assert body["service_name"] == "planner"


@pytest.mark.asyncio
async def test_heartbeat_updates_status_and_metadata():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry.test") as client:
        registry = AgentRegistryClient(_config(), client=client)
        ok = await registry.heartbeat(status=AgentStatus.BUSY, metadata={"load": 2}, ttl_seconds=5)

    assert ok is True
    req = requests[0]
    assert req.method == "POST"
    assert req.url.path == "/agent-registry/alice/agent-1/heartbeat"
    body = json.loads(req.content.decode())
    assert body["status"] == "busy"
    assert body["metadata"]["load"] == 2
    assert body["ttl_seconds"] == 5


@pytest.mark.asyncio
async def test_deregister_deletes_agent_entry():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry.test") as client:
        registry = AgentRegistryClient(_config(), client=client)
        ok = await registry.deregister()

    assert ok is True
    assert requests
    req = requests[0]
    assert req.method == "DELETE"
    assert req.url.path == "/agent-registry/alice/agent-1"


@pytest.mark.asyncio
async def test_task_status_transitions():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry.test") as client:
        registry = AgentRegistryClient(_config(), client=client)
        await registry.mark_task_started(task_id="task-1", pipeline_run_id="run-1", service_name="planner")
        await registry.mark_task_finished(task_id="task-1", pipeline_run_id="run-1", service_name="planner", success=True)

    statuses = [json.loads(req.content.decode())["status"] for req in requests]
    assert statuses[0] == "busy"
    assert statuses[-1] == "idle"


@pytest.mark.asyncio
async def test_unhealthy_after_repeated_failures():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    config = _config()
    config = AgentRegistryConfig(**{**config.__dict__, "failure_threshold": 2})
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry.test") as client:
        registry = AgentRegistryClient(config, client=client)
        await registry.mark_task_finished(task_id="task-1", pipeline_run_id="run-1", service_name="planner", success=False)
        await registry.mark_task_finished(task_id="task-2", pipeline_run_id="run-1", service_name="planner", success=False)

    statuses = [json.loads(req.content.decode())["status"] for req in requests]
    assert statuses[-1] == "unhealthy"


@pytest.mark.asyncio
async def test_heartbeat_loop_runs_until_stopped():
    calls = []

    class DummyClient:
        async def heartbeat(self, **_kw):
            calls.append(1)

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        heartbeat_loop(DummyClient(), interval_seconds=0.01, stop_event=stop_event)
    )
    await asyncio.sleep(0.05)
    stop_event.set()
    await task
    assert len(calls) >= 2
