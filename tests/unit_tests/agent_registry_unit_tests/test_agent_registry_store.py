import copy
import re
import time
from contextlib import contextmanager

import pytest
from couchbase.exceptions import DocumentNotFoundException

from src.agentcy.orchestrator_core.stores.agent_registry_store import AgentRegistryStore
from src.agentcy.pydantic_models.agent_registry_model import (
    AgentRegistryEntry,
    AgentStatus,
)


class _FakeResult:
    def __init__(self, value):
        self.content_as = {dict: value}


class _FakeCollection:
    def __init__(self):
        self._data = {}

    def upsert(self, key, value, **_kw):
        self._data[key] = copy.deepcopy(value)
        return _FakeResult(copy.deepcopy(value))

    def get(self, key, **_kw):
        if key not in self._data:
            raise DocumentNotFoundException()
        return _FakeResult(copy.deepcopy(self._data[key]))

    def remove(self, key, **_kw):
        if key in self._data:
            del self._data[key]


class _FakeCluster:
    def __init__(self, data):
        self._data = data

    def query(self, statement):
        match = re.search(r"LIKE '([^']+)%'", statement)
        prefix = match.group(1) if match else ""
        rows = []
        for key, doc in self._data.items():
            if key.startswith(prefix):
                row = {"id": key}
                row.update(doc)
                rows.append(row)
        return rows


class _FakeBundle:
    def __init__(self, collection):
        self.cluster = _FakeCluster(collection._data)
        self._collection = collection

    def collection(self, _logical):
        return self._collection


class _FakePool:
    def __init__(self):
        self._collection = _FakeCollection()

    @contextmanager
    def collections(self, *_keys, **_kw):
        yield self._collection

    def acquire(self, *_a, **_kw):
        return _FakeBundle(self._collection)

    def release(self, _bundle):
        return None


def _make_store():
    return AgentRegistryStore(_FakePool(), default_ttl_seconds=120)


def test_upsert_and_get_returns_entry():
    store = _make_store()
    entry = AgentRegistryEntry(
        agent_id="agent-1",
        service_name="graph_builder",
        owner="alice",
        capabilities=["plan", "dag"],
        tags=["foundational"],
        status=AgentStatus.IDLE,
    )

    store.upsert(username="alice", entry=entry, ttl_seconds=60)
    doc = store.get(username="alice", agent_id="agent-1")

    assert doc is not None
    assert doc["agent_id"] == "agent-1"
    assert doc["service_name"] == "graph_builder"
    assert doc["owner"] == "alice"
    assert doc["status"] == AgentStatus.IDLE.value
    assert doc["expires_at"] is not None


def test_heartbeat_updates_status_and_metadata():
    store = _make_store()
    entry = AgentRegistryEntry(
        agent_id="agent-2",
        service_name="planner",
        owner="alice",
        status=AgentStatus.ONLINE,
    )

    store.upsert(username="alice", entry=entry, ttl_seconds=0)
    before = store.get(username="alice", agent_id="agent-2")
    assert before is not None

    time.sleep(0.01)
    updated = store.heartbeat(
        username="alice",
        agent_id="agent-2",
        status=AgentStatus.BUSY,
        metadata={"load": 2},
        ttl_seconds=0,
    )

    assert updated["status"] == AgentStatus.BUSY.value
    assert updated["metadata"]["load"] == 2
    assert updated["last_heartbeat"] != before["last_heartbeat"]


def test_list_filters_by_capability_and_status():
    store = _make_store()
    store.upsert(
        username="alice",
        entry=AgentRegistryEntry(
            agent_id="agent-3",
            service_name="planner",
            owner="alice",
            capabilities=["planning"],
            status=AgentStatus.IDLE,
            tags=["a"],
        ),
        ttl_seconds=0,
    )
    store.upsert(
        username="alice",
        entry=AgentRegistryEntry(
            agent_id="agent-4",
            service_name="executor",
            owner="alice",
            capabilities=["execution"],
            status=AgentStatus.BUSY,
            tags=["b"],
        ),
        ttl_seconds=0,
    )

    planners = store.list(username="alice", capability="planning")
    assert {doc["agent_id"] for doc in planners} == {"agent-3"}

    busy = store.list(username="alice", status=AgentStatus.BUSY)
    assert {doc["agent_id"] for doc in busy} == {"agent-4"}

    tagged = store.list(username="alice", tags=["a"])
    assert {doc["agent_id"] for doc in tagged} == {"agent-3"}


def test_delete_removes_entry():
    store = _make_store()
    store.upsert(
        username="alice",
        entry=AgentRegistryEntry(
            agent_id="agent-5",
            service_name="health",
            owner="alice",
        ),
        ttl_seconds=0,
    )
    store.delete(username="alice", agent_id="agent-5")
    assert store.get(username="alice", agent_id="agent-5") is None
