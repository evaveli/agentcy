import re
from contextlib import contextmanager

from couchbase.exceptions import DocumentNotFoundException

from src.agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    EthicsPolicy,
    EthicsPolicySeverity,
    EthicsRule,
)


class _FakeResult:
    def __init__(self, value):
        self.content_as = {dict: value}


class _FakeCollection:
    def __init__(self):
        self._data = {}

    def upsert(self, key, value, **_kw):
        self._data[key] = value
        return _FakeResult(value)

    def get(self, key, **_kw):
        if key not in self._data:
            raise DocumentNotFoundException()
        return _FakeResult(self._data[key])


class _FakeCluster:
    def __init__(self, data):
        self._data = data

    def query(self, statement):
        match = re.search(r"LIKE '([^']+)%'", statement)
        prefix = match.group(1) if match else ""
        if "COUNT" in statement:
            count = sum(1 for k in self._data if k.startswith(prefix))
            return [{"total": count}]
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
        pass


def _make_store():
    pool = _FakePool()
    store = object.__new__(GraphMarkerStore)
    store._pool = pool
    return store


def _sample_policy(policy_id="pol-1", username="alice"):
    return EthicsPolicy(
        policy_id=policy_id,
        username=username,
        name="ACME Corp Policy",
        description="Company-specific ethical rules",
        rules=[
            EthicsRule(
                rule_id="no_pii",
                name="No PII Leakage",
                category="pii",
                severity=EthicsPolicySeverity.BLOCK,
                keywords=["ssn", "credit card"],
                llm_instruction="Never expose personally identifiable information.",
            ),
            EthicsRule(
                rule_id="no_bias",
                name="No Unfair Bias",
                category="bias",
                severity=EthicsPolicySeverity.WARN,
                keywords=["discriminate"],
                llm_instruction="Ensure no discriminatory outcomes.",
            ),
        ],
    )


def test_save_and_get_ethics_policy():
    store = _make_store()
    policy = _sample_policy()
    key = store.save_ethics_policy(username="alice", policy=policy)
    assert "ethics_policy::alice::pol-1" in key

    doc = store.get_ethics_policy(username="alice", policy_id="pol-1")
    assert doc is not None
    assert doc["policy_id"] == "pol-1"
    assert doc["name"] == "ACME Corp Policy"
    assert len(doc["rules"]) == 2
    assert doc["_meta"]["type"] == "ethics_policy"


def test_get_active_ethics_policy():
    store = _make_store()
    policy = _sample_policy()
    store.save_ethics_policy(username="alice", policy=policy)

    active = store.get_active_ethics_policy(username="alice")
    assert active is not None
    assert active["policy_id"] == "pol-1"
    assert active["_meta"]["type"] == "ethics_policy_active"


def test_active_policy_overwritten_on_save():
    store = _make_store()
    policy1 = _sample_policy(policy_id="pol-1")
    policy2 = _sample_policy(policy_id="pol-2")
    policy2 = policy2.model_copy(update={"name": "Updated Policy"})

    store.save_ethics_policy(username="alice", policy=policy1)
    store.save_ethics_policy(username="alice", policy=policy2)

    active = store.get_active_ethics_policy(username="alice")
    assert active["policy_id"] == "pol-2"
    assert active["name"] == "Updated Policy"

    # Both policies still accessible by id
    p1 = store.get_ethics_policy(username="alice", policy_id="pol-1")
    p2 = store.get_ethics_policy(username="alice", policy_id="pol-2")
    assert p1 is not None
    assert p2 is not None


def test_get_nonexistent_policy():
    store = _make_store()
    doc = store.get_ethics_policy(username="alice", policy_id="nonexistent")
    assert doc is None


def test_get_active_when_none_set():
    store = _make_store()
    doc = store.get_active_ethics_policy(username="alice")
    assert doc is None


def test_list_ethics_policies():
    store = _make_store()
    policy1 = _sample_policy(policy_id="pol-1")
    policy2 = _sample_policy(policy_id="pol-2")
    store.save_ethics_policy(username="alice", policy=policy1)
    store.save_ethics_policy(username="alice", policy=policy2)

    items, total = store.list_ethics_policies(username="alice")
    assert total == 2
    assert len(items) == 2
    policy_ids = {item.get("policy_id") for item in items}
    assert "pol-1" in policy_ids
    assert "pol-2" in policy_ids


def test_list_ethics_policies_tenant_isolation():
    store = _make_store()
    policy_alice = _sample_policy(policy_id="pol-a", username="alice")
    policy_bob = _sample_policy(policy_id="pol-b", username="bob")
    store.save_ethics_policy(username="alice", policy=policy_alice)
    store.save_ethics_policy(username="bob", policy=policy_bob)

    alice_items, alice_total = store.list_ethics_policies(username="alice")
    bob_items, bob_total = store.list_ethics_policies(username="bob")

    assert alice_total == 1
    assert bob_total == 1
    assert alice_items[0]["policy_id"] == "pol-a"
    assert bob_items[0]["policy_id"] == "pol-b"
