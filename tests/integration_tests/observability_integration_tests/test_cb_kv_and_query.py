# tests/integration_tests/observability_integration_tests/test_cb_kv_and_query.py
from __future__ import annotations

import os
import sys
import socket
from contextlib import closing
from types import SimpleNamespace

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _can_reach(host: str, port: int, timeout=0.4) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except Exception:
            return False


def _choose_conn_str() -> str:
    env_val = os.getenv("CB_CONN_STR")
    if env_val:
        return env_val
    if _can_reach("127.0.0.1", 11210) or _can_reach("localhost", 11210):
        return "couchbase://localhost"
    return "couchbase://couchbase"


def _reload(module_name: str):
    if module_name in sys.modules:
        del sys.modules[module_name]
    return __import__(module_name, fromlist=["*"])


def _reload_pool_and_config():
    # try both import styles (with and without "src.")
    mod_paths = [
        "agentcy.orchestrator_core.couch.config",
        "src.agentcy.orchestrator_core.couch.config",
    ]
    for m in mod_paths:
        try:
            _reload(m)
        except Exception:
            pass
    try:
        pool = _reload("agentcy.orchestrator_core.couch.pool")
    except Exception:
        pool = _reload("src.agentcy.orchestrator_core.couch.pool")
    return pool


def _reload_shim_with_env(**env):
    for k, v in env.items():
        os.environ[k] = str(v)
    for m in (
        "agentcy.observability.config",
        "agentcy.observability.couchbase_shim",
        "src.agentcy.observability.config",
        "src.agentcy.observability.couchbase_shim",
    ):
        if m in sys.modules:
            del sys.modules[m]
    try:
        cfg = _reload("agentcy.observability.config")
        shim = _reload("agentcy.observability.couchbase_shim")
    except Exception:
        cfg = _reload("src.agentcy.observability.config")
        shim = _reload("src.agentcy.observability.couchbase_shim")
    return shim


# ──────────────────────────────────────────────────────────────────────────────
# Couchbase pool (MODULE-scoped, no monkeypatch)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def couch_pool():
    prev = os.environ.get("CB_CONN_STR")
    os.environ["CB_CONN_STR"] = _choose_conn_str()

    pool_mod = _reload_pool_and_config()
    # import config values (either path)
    try:
        from src.agentcy.orchestrator_core.couch.config import (
            CB_BUCKET,
            CB_SCOPE,
            CB_COLLECTIONS,
        )
    except Exception:
        from src.agentcy.orchestrator_core.couch.config import (  # type: ignore
            CB_BUCKET,
            CB_SCOPE,
            CB_COLLECTIONS,
        )

    pool = pool_mod.DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=2,
        idle_timeout=5.0,
    )
    try:
        yield SimpleNamespace(pool=pool, bucket=CB_BUCKET, scope=CB_SCOPE, colmap=CB_COLLECTIONS)
    finally:
        try:
            pool.close_all()
        except Exception:
            pass
        if prev is None:
            os.environ.pop("CB_CONN_STR", None)
        else:
            os.environ["CB_CONN_STR"] = prev


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────
def test_pool_kv_spans_and_metrics(couch_pool, otel_pipeline):
    """KV ops via your production pool + TracedCollection wrapper."""
    # load shim with metrics enabled
    shim = _reload_shim_with_env(CB_METRICS_ENABLED="1", CB_TRACE_STATEMENTS="0")

    # pick a logical collection key (prefer 'pipelines' if present)
    colmap = couch_pool.colmap
    logical = "pipelines" if "pipelines" in colmap else next(iter(colmap.keys()))
    physical = colmap[logical]

    # use the pool to get a SafeCollection; wrap it with TracedCollection
    from contextlib import contextmanager

    @contextmanager
    def _one_collection():
        with couch_pool.pool.collections(logical) as c:
            yield c

    with _one_collection() as safe_coll:
        tcoll = shim.TracedCollection(
            safe_coll,  # SafeCollection implements KV ops
            bucket=couch_pool.bucket,
            scope=couch_pool.scope,
            collection=physical,
            conn_str=os.getenv("CB_CONN_STR"),
        )
        tcoll.upsert("it-key-1", {"a": 1})
        doc = tcoll.get("it-key-1")
        assert getattr(doc, "content_as", None) is not None

    # assert spans
    spans = otel_pipeline.spans.get_finished_spans()
    names = [s.name for s in spans]
    assert any("Couchbase.upsert" in n for n in names)
    assert any("Couchbase.get" in n for n in names)

    # attrs
    for s in spans:
        assert s.attributes.get("db.system") == "couchbase"
        assert s.attributes.get("db.name") == couch_pool.bucket
        assert s.attributes.get("db.collection.name") == physical

    # metrics exist
    data = otel_pipeline.metric_reader.get_metrics_data()
    assert data and getattr(data, "resource_metrics", None), "no metrics exported"


def test_pool_query_span_and_statement_capture(otel_pipeline, couch_pool):
    """Query via a direct Cluster + TracedCluster (leaves prod pool untouched)."""
    # enable statement capture & keep messages short for test
    shim = _reload_shim_with_env(CB_TRACE_STATEMENTS="1", CB_STATEMENT_MAXLEN="12", CB_METRICS_ENABLED="1")

    # Use same creds/env as prod config
    try:
        from src.agentcy.orchestrator_core.couch.config import CB_CONN_STR, CB_USER, CB_PASS
    except Exception:
        from src.agentcy.orchestrator_core.couch.config import CB_CONN_STR, CB_USER, CB_PASS  # type: ignore

    # couchbase driver
    from couchbase.cluster import Cluster
    from couchbase.auth import PasswordAuthenticator
    from couchbase.options import ClusterOptions

    cluster = Cluster(CB_CONN_STR, ClusterOptions(PasswordAuthenticator(CB_USER, CB_PASS))) # type: ignore
    try:
        tcluster = shim.TracedCluster(cluster, default_bucket=couch_pool.bucket, conn_str=CB_CONN_STR)

        # Valid N1QL that requires no FROM and always succeeds; also long enough to be truncated
        long_literal = "x" * 64
        q = f"SELECT '{long_literal}' AS x"
        rows = list(tcluster.query(q))
        assert rows and rows[0].get("x") == long_literal

        spans = otel_pipeline.spans.get_finished_spans()
        qspans = [s for s in spans if "Couchbase.Query" in s.name]
        assert qspans, "no query span emitted"
        stmt = qspans[-1].attributes.get("db.statement")
        assert stmt and stmt.endswith("…"), f"expected truncated statement, got {stmt!r}"

        # metrics exist (query path also records duration/ops)
        data = otel_pipeline.metric_reader.get_metrics_data()
        assert data and getattr(data, "resource_metrics", None)
    finally:
        try:
            cluster.close()
        except Exception:
            pass

