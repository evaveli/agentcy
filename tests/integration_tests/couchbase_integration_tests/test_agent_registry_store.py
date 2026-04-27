import time
import uuid

import pytest

from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.orchestrator_core.stores.agent_registry_store import AgentRegistryStore
from src.agentcy.pydantic_models.agent_registry_model import (
    AgentRegistryEntry,
    AgentStatus,
)


@pytest.fixture(scope="module")
def pool():
    pool = DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )
    yield pool
    pool.close_all()


def test_upsert_get_list_and_delete(pool):
    store = AgentRegistryStore(pool, default_ttl_seconds=0)
    username = "registry_integration"
    agent_id = f"agent-{uuid.uuid4()}"
    entry = AgentRegistryEntry(
        agent_id=agent_id,
        service_name="planner",
        owner=username,
        capabilities=["planning"],
        status=AgentStatus.IDLE,
    )

    try:
        store.upsert(username=username, entry=entry, ttl_seconds=0)
        doc = store.get(username=username, agent_id=agent_id)
        assert doc is not None
        assert doc["agent_id"] == agent_id
        assert doc["owner"] == username

        listed = store.list(username=username, service_name="planner")
        assert any(item["agent_id"] == agent_id for item in listed)
    finally:
        store.delete(username=username, agent_id=agent_id)
        assert store.get(username=username, agent_id=agent_id) is None


def test_heartbeat_updates_last_seen(pool):
    store = AgentRegistryStore(pool, default_ttl_seconds=0)
    username = "registry_integration"
    agent_id = f"agent-{uuid.uuid4()}"
    entry = AgentRegistryEntry(
        agent_id=agent_id,
        service_name="executor",
        owner=username,
        status=AgentStatus.ONLINE,
    )

    try:
        store.upsert(username=username, entry=entry, ttl_seconds=0)
        before = store.get(username=username, agent_id=agent_id)
        assert before is not None
        time.sleep(0.01)

        store.heartbeat(
            username=username,
            agent_id=agent_id,
            status=AgentStatus.BUSY,
            metadata={"load": 1},
            ttl_seconds=0,
        )
        after = store.get(username=username, agent_id=agent_id)
        assert after is not None
        assert after["status"] == AgentStatus.BUSY.value
        assert after["last_heartbeat"] != before["last_heartbeat"]
    finally:
        store.delete(username=username, agent_id=agent_id)
