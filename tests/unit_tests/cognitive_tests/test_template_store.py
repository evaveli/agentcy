"""Tests for the TemplateStore Couchbase CRUD layer."""
import re
from contextlib import contextmanager

import pytest
from couchbase.exceptions import DocumentNotFoundException

from src.agentcy.orchestrator_core.stores.template_store import TemplateStore
from src.agentcy.pydantic_models.agent_template_model import (
    AgentTemplate,
    TemplateCategory,
)


# ── Fake Couchbase infrastructure (same pattern as test_graph_marker_store) ──


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

    def remove(self, key, **_kw):
        if key not in self._data:
            raise DocumentNotFoundException()
        del self._data[key]


class _FakeCluster:
    def __init__(self, data):
        self._data = data

    def query(self, statement, *, named_parameters=None, **_kw):
        """Minimal N1QL emulation with parameterised query support."""
        params = named_parameters or {}

        # Resolve $prefix param or fall back to string literal
        prefix_val = params.get("prefix", "")
        if not prefix_val:
            m = re.search(r"LIKE '([^']+)%'", statement)
            prefix_val = (m.group(1) + "%") if m else ""
        prefix = prefix_val.rstrip("%")

        is_count = "COUNT(*)" in statement.upper()
        rows = []

        for key, doc in self._data.items():
            if not key.startswith(prefix):
                continue

            # Category filter (parameterised)
            if "category" in params:
                if doc.get("category") != params["category"]:
                    continue

            # Enabled filter (parameterised)
            if "enabled" in params:
                if doc.get("enabled", True) != params["enabled"]:
                    continue

            # Capability filter (parameterised)
            if "capability" in params:
                if params["capability"] not in doc.get("capabilities", []):
                    continue

            rows.append(dict(doc))

        if is_count:
            return [{"cnt": len(rows)}]
        return rows


class _FakePool:
    def __init__(self):
        self._collection = _FakeCollection()

    def collection(self, _logical):
        return self._collection

    def resolve_collection_name(self, _logical):
        return "agent_templates"

    def cluster(self):
        return _FakeCluster(self._collection._data)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_template(**overrides) -> dict:
    tmpl = AgentTemplate(
        name="test_agent",
        display_name="Test Agent",
        description="A test agent template",
        service_name_pattern="test-agent-{run_id}",
        category=TemplateCategory.DATA_PROCESSING,
        capabilities=["data_read", "transform"],
        tags=["etl", "batch"],
        keywords=["extract", "load"],
    )
    data = tmpl.model_dump(mode="json")
    data.update(overrides)
    return data


# ── CRUD Tests ───────────────────────────────────────────────────────────────


def test_upsert_and_get_roundtrip():
    store = TemplateStore(_FakePool())
    data = _make_template()
    tid = store.upsert(username="alice", template=data)
    assert tid == data["template_id"]

    doc = store.get(username="alice", template_id=tid)
    assert doc is not None
    assert doc["name"] == "test_agent"
    assert doc["username"] == "alice"


def test_get_missing_returns_none():
    store = TemplateStore(_FakePool())
    assert store.get(username="alice", template_id="nonexistent") is None


def test_upsert_requires_template_id():
    store = TemplateStore(_FakePool())
    with pytest.raises(ValueError, match="template_id"):
        store.upsert(username="alice", template={"name": "bad"})


def test_delete_existing():
    store = TemplateStore(_FakePool())
    data = _make_template()
    tid = store.upsert(username="alice", template=data)
    assert store.delete(username="alice", template_id=tid) is True
    assert store.get(username="alice", template_id=tid) is None


def test_delete_missing_returns_false():
    store = TemplateStore(_FakePool())
    assert store.delete(username="alice", template_id="ghost") is False


def test_list_returns_all_for_user():
    store = TemplateStore(_FakePool())
    t1 = _make_template(template_id="t1", name="agent_a")
    t2 = _make_template(template_id="t2", name="agent_b")
    store.upsert(username="alice", template=t1)
    store.upsert(username="alice", template=t2)
    store.upsert(username="bob", template=_make_template(template_id="t3"))

    alice_templates = store.list(username="alice")
    assert len(alice_templates) == 2


def test_list_filters_by_category():
    store = TemplateStore(_FakePool())
    store.upsert(
        username="alice",
        template=_make_template(
            template_id="t1", category=TemplateCategory.DATA_PROCESSING.value
        ),
    )
    store.upsert(
        username="alice",
        template=_make_template(
            template_id="t2", category=TemplateCategory.NOTIFICATION.value
        ),
    )

    results = store.list(username="alice", category=TemplateCategory.DATA_PROCESSING.value)
    assert all(r["category"] == "data_processing" for r in results)


def test_list_filters_by_capability():
    store = TemplateStore(_FakePool())
    store.upsert(
        username="alice",
        template=_make_template(template_id="t1", capabilities=["data_read", "transform"]),
    )
    store.upsert(
        username="alice",
        template=_make_template(template_id="t2", capabilities=["notification"]),
    )

    results = store.list(username="alice", capability="data_read")
    assert len(results) == 1


def test_list_filters_by_enabled():
    store = TemplateStore(_FakePool())
    store.upsert(username="alice", template=_make_template(template_id="t1", enabled=True))
    store.upsert(username="alice", template=_make_template(template_id="t2", enabled=False))

    enabled = store.list(username="alice", enabled=True)
    disabled = store.list(username="alice", enabled=False)
    assert all(r.get("enabled", True) for r in enabled)
    assert all(not r.get("enabled", True) for r in disabled)


def test_count():
    store = TemplateStore(_FakePool())
    store.upsert(username="alice", template=_make_template(template_id="t1"))
    store.upsert(username="alice", template=_make_template(template_id="t2"))
    store.upsert(username="bob", template=_make_template(template_id="t3"))

    assert store.count(username="alice") == 2
    assert store.count(username="bob") == 1


def test_count_with_enabled_filter():
    store = TemplateStore(_FakePool())
    store.upsert(username="alice", template=_make_template(template_id="t1", enabled=True))
    store.upsert(username="alice", template=_make_template(template_id="t2", enabled=False))

    assert store.count(username="alice", enabled=True) == 1
    assert store.count(username="alice", enabled=False) == 1


def test_upsert_overwrites():
    store = TemplateStore(_FakePool())
    data = _make_template(template_id="t1", name="original")
    store.upsert(username="alice", template=data)

    updated = _make_template(template_id="t1", name="updated")
    store.upsert(username="alice", template=updated)

    doc = store.get(username="alice", template_id="t1")
    assert doc["name"] == "updated"


# ── Hardening Tests ──────────────────────────────────────────────────────────


def test_upsert_preserves_created_at():
    store = TemplateStore(_FakePool())
    data = _make_template(template_id="t1")
    store.upsert(username="alice", template=data)

    doc1 = store.get(username="alice", template_id="t1")
    created_at_original = doc1["_meta"]["created_at"]

    updated = _make_template(template_id="t1", name="v2")
    store.upsert(username="alice", template=updated)

    doc2 = store.get(username="alice", template_id="t1")
    assert doc2["_meta"]["created_at"] == created_at_original
    assert doc2["name"] == "v2"


def test_upsert_sets_created_at_on_new_doc():
    store = TemplateStore(_FakePool())
    data = _make_template(template_id="t1")
    store.upsert(username="alice", template=data)

    doc = store.get(username="alice", template_id="t1")
    assert "_meta" in doc
    assert "created_at" in doc["_meta"]
    assert "updated_at" in doc["_meta"]


def test_upsert_rejects_invalid_username():
    store = TemplateStore(_FakePool())
    data = _make_template(template_id="t1")
    with pytest.raises(ValueError, match="username"):
        store.upsert(username="alice'; DROP", template=data)


def test_upsert_rejects_invalid_template_id():
    store = TemplateStore(_FakePool())
    data = _make_template(template_id="bad'; DROP")
    with pytest.raises(ValueError, match="template_id"):
        store.upsert(username="alice", template=data)


def test_list_rejects_invalid_sort_by():
    store = TemplateStore(_FakePool())
    store.upsert(username="alice", template=_make_template(template_id="t1"))
    with pytest.raises(ValueError, match="sort_by"):
        store.list(username="alice", sort_by="'; DROP TABLE")


def test_list_allows_valid_sort_by():
    store = TemplateStore(_FakePool())
    store.upsert(username="alice", template=_make_template(template_id="t1"))
    # Should not raise
    results = store.list(username="alice", sort_by="name")
    assert isinstance(results, list)
