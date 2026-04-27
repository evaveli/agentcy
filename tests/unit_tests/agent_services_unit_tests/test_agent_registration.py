import uuid

import pytest

from src.agentcy.agent_runtime.services.agent_registration import run
from src.agentcy.pydantic_models.service_registration_model import ServiceRegistration, RuntimeEnum


class _FakeServiceStore:
    def __init__(self) -> None:
        self._services = {}

    def list_all(self, username):
        return [
            {"service_id": sid, "service_name": doc["service_name"]}
            for sid, doc in self._services.items()
        ]

    def get(self, username, service_id):
        return self._services.get(service_id)

    def add(self, service: ServiceRegistration) -> None:
        self._services[str(service.service_id)] = service.model_dump(mode="json")


class _FakeRegistryStore:
    def __init__(self) -> None:
        self.entries = {}

    def upsert(self, username, entry):
        self.entries[(username, entry.agent_id)] = entry


class _FakeRM:
    def __init__(self, service_store, registry_store) -> None:
        self.service_store = service_store
        self.agent_registry_store = registry_store


def _service(name: str) -> ServiceRegistration:
    return ServiceRegistration(
        service_id=uuid.uuid4(),
        service_name=name,
        runtime=RuntimeEnum.PYTHON_PLUGIN,
        artifact={"kind": "entry", "entry": f"agentcy.agent_runtime.services.{name}:run"},
        base_url=None,
        healthcheck_endpoint={
            "name": "health",
            "path": "/health",
            "methods": ["GET"],
            "description": "health",
            "parameters": [],
        },
    )


@pytest.mark.asyncio
async def test_agent_registration_seeds_registry():
    service_store = _FakeServiceStore()
    registry_store = _FakeRegistryStore()
    service_store.add(_service("graph_builder"))
    service_store.add(_service("plan_validator"))
    rm = _FakeRM(service_store, registry_store)

    result = await run(rm, "run-1", "agent_registration", None, {"username": "alice"})
    assert result["registered"] == 2
    assert ("alice", "graph_builder") in registry_store.entries
    assert ("alice", "plan_validator") in registry_store.entries
    entry = registry_store.entries[("alice", "graph_builder")]
    assert "graph_builder" in entry.capabilities
