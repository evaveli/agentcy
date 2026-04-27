import asyncio
import json
import os
import subprocess
import time
from uuid import uuid4
from urllib.parse import urlparse, urlunparse

import aio_pika
import pytest

from agentcy.orchestrator_core.consumers.launcher_consumer import QUEUE as START_TASK_QUEUE
from agentcy.orchestrator_core.executors.docker_exec import _sanitize_name

AMQP_URI = os.getenv("AMQP_URI", "amqp://guest:guest@localhost:5672/")


def _docker_cmd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
    )

def _docker_rabbitmq_url() -> str:
    parsed = urlparse(os.getenv("RABBITMQ_URL", AMQP_URI))
    host = parsed.hostname or "localhost"
    override = os.getenv("RABBITMQ_DOCKER_HOST")
    if override:
        host = override
    elif host in {"localhost", "127.0.0.1", "rabbitmq"}:
        host = "host.docker.internal"
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{netloc}"
    return urlunparse((parsed.scheme or "amqp", netloc, parsed.path or "/", "", "", ""))


async def _publish_start_task(payload: dict) -> None:
    connection = await aio_pika.connect_robust(AMQP_URI)
    try:
        async with connection.channel() as channel:
            await channel.default_exchange.publish(
                aio_pika.Message(body=json.dumps(payload).encode("utf-8")),
                routing_key=START_TASK_QUEUE,
            )
    finally:
        await connection.close()


async def _wait_for_container(name: str, timeout: float = 30.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _docker_cmd(
            "ps",
            "--filter",
            f"name={name}",
            "--format",
            "{{.ID}}",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
        await asyncio.sleep(0.5)
    raise AssertionError(f"Container matching {name} never appeared")


async def _wait_for_log_line(container_id: str, substring: str, timeout: float = 30.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _docker_cmd("logs", container_id)
        if proc.returncode == 0 and substring in proc.stdout:
            return proc.stdout
        await asyncio.sleep(0.5)
    raise AssertionError(f"Expected log fragment {substring!r} not found")


async def _wait_for_container_gone(name: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _docker_cmd(
            "ps",
            "--filter",
            f"name={name}",
            "--format",
            "{{.ID}}",
        )
        if proc.returncode == 0 and not proc.stdout.strip():
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"Container {name} did not terminate")


@pytest.mark.asyncio
async def test_container_agent_launched_and_reports_output(
    e2e_dummy_agent,
    start_task_consumer_fixture,
):
    repo, tag = e2e_dummy_agent.rsplit(":", 1)
    service_name = f"docker-e2e-{uuid4().hex[:6]}"
    expected_container = _sanitize_name(f"agent-{service_name}-{tag}")

    unique_payload = f"payload-{uuid4().hex}"

    message = {
        "runtime": "container",
        "service_name": service_name,
        "artifact": {"repo": repo, "tag": tag},
        "task_environ": {
            "AGENT_INPUT": unique_payload,
            "AGENT_HOLD_SECONDS": "8",
        },
    }

    await _publish_start_task(message)

    container_id = await _wait_for_container(expected_container)
    logs = await _wait_for_log_line(container_id, unique_payload)
    assert unique_payload in logs

    await _wait_for_container_gone(expected_container)


@pytest.mark.asyncio
async def test_entry_services_launch_via_start_task(
    http_client,
    start_task_consumer_fixture,
    e2e_runtime_image,
):
    username = f"entry_services_{uuid4().hex[:6]}"
    services = [
        ("input_validator", "agentcy.agent_runtime.services.input_validator:run"),
        ("supervisor_agent", "agentcy.agent_runtime.services.supervisor_agent:run"),
        ("path_seeder", "agentcy.agent_runtime.services.path_seeder:run"),
        ("blueprint_bidder", "agentcy.agent_runtime.services.blueprint_bidder:run"),
        ("graph_builder", "agentcy.agent_runtime.services.graph_builder:run"),
        ("plan_validator", "agentcy.agent_runtime.services.plan_validator:run"),
        ("plan_cache", "agentcy.agent_runtime.services.plan_cache:run"),
        ("pheromone_engine", "agentcy.agent_runtime.services.pheromone_engine:run"),
        ("human_validator", "agentcy.agent_runtime.services.human_validator:run"),
        ("llm_strategist", "agentcy.agent_runtime.services.llm_strategist:run"),
        ("ethics_checker", "agentcy.agent_runtime.services.ethics_checker:run"),
        ("system_executor", "agentcy.agent_runtime.services.system_executor:run"),
        ("failure_escalation", "agentcy.agent_runtime.services.failure_escalation:run"),
        ("audit_logger", "agentcy.agent_runtime.services.audit_logger:run"),
    ]
    for service_name, entry in services:
        payload = {
            "service_id": str(uuid4()),
            "service_name": service_name,
            "version": "0.1.0",
            "runtime": "python_plugin",
            "artifact": {"kind": "entry", "entry": entry},
            "description": f"e2e entry service {service_name}",
            "healthcheck_endpoint": {
                "name": "health",
                "path": "/health",
                "methods": ["GET"],
                "description": "Health check endpoint.",
                "parameters": [],
            },
        }
        resp = await http_client.post(f"/services/{username}", json=payload)
        resp.raise_for_status()

    rabbitmq_url = _docker_rabbitmq_url()
    parsed = urlparse(rabbitmq_url)
    rabbit_host = parsed.hostname or "host.docker.internal"
    rabbit_port = str(parsed.port or 5672)
    for service_name, entry in services:
        expected_container = _sanitize_name(f"agent-{service_name}-entry")
        _docker_cmd("rm", "-f", expected_container)
        message = {
            "runtime": "python_plugin",
            "service_name": service_name,
            "artifact": {"kind": "entry", "entry": entry},
            "task_environ": {
                "RABBITMQ_URL": rabbitmq_url,
                "AMQP_URI": rabbitmq_url,
                "RABBITMQ_HOST": rabbit_host,
                "RABBITMQ_PORT": rabbit_port,
                "AGENT_RUNTIME_EPHEMERAL_CB": "0",
            },
        }
        await _publish_start_task(message)

        container_id = await _wait_for_container(expected_container)
        try:
            logs = await _wait_for_log_line(container_id, "[runner] starting service", timeout=60.0)
            assert "[runner] starting service" in logs
        finally:
            _docker_cmd("rm", "-f", container_id)
            await _wait_for_container_gone(expected_container)
