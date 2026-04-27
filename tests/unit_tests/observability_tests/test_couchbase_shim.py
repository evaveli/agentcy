from __future__ import annotations
import sys
from types import SimpleNamespace
import pytest


def _reload_shim(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))

    # Use a single, consistent import path everywhere
    for m in (
        "agentcy.observability.couchbase_shim",
        "agentcy.observability.config",
        "src.agentcy.observability.couchbase_shim",
        "src.agentcy.observability.config",
    ):
        if m in sys.modules:
            del sys.modules[m]

    import src.agentcy.observability.config as cfg
    import importlib
    importlib.reload(cfg)  # ← ensures CB_* constants re-read from env

    import src.agentcy.observability.couchbase_shim as shim
    importlib.reload(shim)
    return shim


# ---------- Fakes for Couchbase raw objects ----------------------------------

class FakeKV:
    def __init__(self):
        self.store = {}
        self.calls = []

    def insert(self, key, value, *a, **kw):
        self.calls.append(("insert", key))
        if key in self.store:
            raise RuntimeError("exists")
        self.store[key] = value
        return {"ok": True}

    def upsert(self, key, value, *a, **kw):
        self.calls.append(("upsert", key))
        self.store[key] = value
        return {"ok": True}

    def replace(self, key, value, *a, **kw):
        self.calls.append(("replace", key))
        if key not in self.store:
            raise RuntimeError("notfound")
        self.store[key] = value
        return {"ok": True}

    def get(self, key, *a, **kw):
        self.calls.append(("get", key))
        if key == "boom":
            raise RuntimeError("boom")
        return SimpleNamespace(content_as={"value": self.store.get(key)})

    def remove(self, key, *a, **kw):
        self.calls.append(("remove", key))
        self.store.pop(key, None)
        return {"ok": True}

    def exists(self, key, *a, **kw):
        self.calls.append(("exists", key))
        return SimpleNamespace(exists=key in self.store)


class FakeCluster:
    def __init__(self):
        self.queries = []

    def query(self, statement: str, *a, **kw):
        self.queries.append(statement)
        return [{"row": 1}]

    @property
    def transactions(self):
        return self._txns


class FakeAttemptContext:
    def __init__(self):
        self.calls = []

    def get(self, coll, key, *a, **kw):
        self.calls.append(("get", key))
        return SimpleNamespace(content_as={"v": "x"})

    def insert(self, coll, key, value, *a, **kw):
        self.calls.append(("insert", key))
        return {"ok": True}

    def replace(self, doc, value=None, *a, **kw):
        self.calls.append(("replace", "doc"))
        return {"ok": True}

    def remove(self, doc, *a, **kw):
        self.calls.append(("remove", "doc"))
        return {"ok": True}


class FakeTransactions:
    def __init__(self, fake_ctx: FakeAttemptContext):
        self.fake_ctx = fake_ctx
        self.runs = 0

    def run(self, user_fn, *a, **kw):
        self.runs += 1
        # The shim wraps ctx before calling user_fn
        return user_fn(self.fake_ctx)

# ---------- Tests -------------------------------------------------------------

def test_kv_spans_and_metrics_success(monkeypatch, otel_pipeline):

    shim = _reload_shim(monkeypatch,
                        CB_METRICS_ENABLED="1",
                        CB_TRACE_STATEMENTS="0")

    raw = FakeKV()
    # TracedCollection expects a "raw" that has real KV; we can wrap the fake directly
    coll = shim.TracedCollection(
        raw,
        bucket="agentcy",
        scope="_default",
        collection="pipelines",
        conn_str="couchbase://localhost",
    )

    coll.upsert("k1", {"a": 1})
    coll.get("k1")

    spans = otel_pipeline.spans.get_finished_spans()
    names = [s.name for s in spans]
    assert any("Couchbase.upsert" in n for n in names)
    assert any("Couchbase.get" in n for n in names)

    # check attrs present
    for s in spans:
        assert s.attributes.get("db.system") == "couchbase"
        assert s.attributes.get("db.name") == "agentcy"
        assert s.attributes.get("db.collection.name") == "pipelines"

    # metrics: counter & histogram should have points
    data = otel_pipeline.metric_reader.get_metrics_data()
    # flatten quick check that at least one instrument exists
    assert data.resource_metrics, "no metrics exported"


def test_kv_span_error_and_error_counter(monkeypatch, otel_pipeline):
    shim = _reload_shim(monkeypatch,
                        CB_METRICS_ENABLED="1")

    raw = FakeKV()
    coll = shim.TracedCollection(raw, bucket="b", scope="_d", collection="c")

    with pytest.raises(RuntimeError):
        coll.get("boom")

    spans = otel_pipeline.spans.get_finished_spans()
    # last span is the erroring get
    err_span = spans[-1]
    assert err_span.status.is_ok is False
    assert err_span.status.status_code.name == "ERROR"

    # metrics include an error increment; just assert something exported
    data = otel_pipeline.metric_reader.get_metrics_data()
    assert data.resource_metrics


def test_query_statement_capture_and_truncation(monkeypatch, otel_pipeline):
    # turn on statement capture + tiny max length
    shim = _reload_shim(monkeypatch,
                        CB_TRACE_STATEMENTS="1",
                        CB_STATEMENT_MAXLEN="10")

    fake_cluster = FakeCluster()
    # wire fake txns property expected by TracedCluster.transactions
    fake_cluster._txns = FakeTransactions(FakeAttemptContext())

    tcluster = shim.TracedCluster(fake_cluster, default_bucket="b", conn_str="cstr")

    long_sql = "SELECT something_really_long FROM table WHERE x = 1"
    list(tcluster.query(long_sql))  # iterate to consume

    spans = otel_pipeline.spans.get_finished_spans()
    qspans = [s for s in spans if "Couchbase.Query" in s.name]
    assert qspans, "query span not produced"
    stmt = qspans[0].attributes.get("db.statement")
    assert stmt is not None
    assert stmt.endswith("…")  # truncated


def test_query_statement_not_recorded_when_disabled(monkeypatch, otel_pipeline):
    shim = _reload_shim(monkeypatch, CB_TRACE_STATEMENTS="0")

    fake_cluster = FakeCluster()
    fake_cluster._txns = FakeTransactions(FakeAttemptContext())
    tcluster = shim.TracedCluster(fake_cluster, default_bucket="b", conn_str="cstr")

    list(tcluster.query("SELECT 1"))

    spans = otel_pipeline.spans.get_finished_spans()
    qspans = [s for s in spans if "Couchbase.Query" in s.name]
    assert qspans
    assert "db.statement" not in qspans[0].attributes


def test_transactions_wrap_and_child_ops(monkeypatch, otel_pipeline):
    shim = _reload_shim(monkeypatch)

    fake_ctx = FakeAttemptContext()
    txns = FakeTransactions(fake_ctx)
    ttx = shim.TracedTransactions(txns, bucket="b", scope="_d", conn_str="cstr")

    def user_fn(ctx):
        # ctx here is TracedAttemptContext (shim), which should call into fake_ctx
        coll = SimpleNamespace(_raw=FakeKV())  # shim strips _raw if present; we don't care for return
        ctx.get(coll, "k")
        ctx.insert(coll, "k", {"x": 1})
        ctx.replace("doc", {"y": 2})
        ctx.remove("doc")

    ttx.run(user_fn)

    # Underlying fake context should have seen all ops
    assert [c[0] for c in fake_ctx.calls] == ["get", "insert", "replace", "remove"]

    # Spans: one for txn.run + four child spans
    spans = otel_pipeline.spans.get_finished_spans()
    names = [s.name for s in spans]
    assert any("Couchbase.Transaction" in n for n in names)
    assert sum(1 for n in names if n.startswith("Couchbase.Txn.")) == 4


