# src/agentcy/observability/couchbase_shim.py
from __future__ import annotations
from typing import Any, Callable, Optional
import time
import os

from opentelemetry import trace, metrics
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.util.types import AttributeValue
from agentcy.observability.config import (
    RKeys, CB_TRACE_STATEMENTS, CB_STATEMENT_MAXLEN, CB_METRICS_ENABLED
)

_duration_ms = None
_ops_total   = None
_errors_total= None

_TRACER_NAME = "agentcy.couchbase"
def _get_tracer():
    return trace.get_tracer(_TRACER_NAME)


def _ensure_instruments() -> None:
    """Create instruments at first use so tests (and prod) bind to the active MeterProvider."""
    global _duration_ms, _ops_total, _errors_total
    if not CB_METRICS_ENABLED:
        return
    if _duration_ms and _ops_total and _errors_total:
        return
    m = metrics.get_meter("agentcy.couchbase")
    if _duration_ms is None:
        _duration_ms = m.create_histogram(
            name="db.client.operation.duration",
            unit="ms",
            description="Latency of Couchbase client operations",
        )
    if _ops_total is None:
        _ops_total = m.create_counter(
            name="db.client.operations",
            unit="1",
            description="Count of Couchbase client operations",
        )
    if _errors_total is None:
        _errors_total = m.create_counter(
            name="db.client.errors",
            unit="1",
            description="Count of failed Couchbase client operations",
        )

def _attrs_base(
    *,
    bucket: str,
    scope: Optional[str] = None,
    collection: Optional[str] = None,
    op: Optional[str] = None,
    conn_str: Optional[str] = None,
    extra: Optional[dict[str, AttributeValue]] = None,
) -> dict[str, AttributeValue]:
    a: dict[str, AttributeValue] = {
        "db.system": "couchbase",
        "db.name": bucket,                   # semantic conv: database name
    }
    if scope:
        a["db.collection.scope"] = scope     # vendor-ish; helpful for filtering
    if collection:
        a["db.collection.name"] = collection
    if op:
        a["db.operation"] = op
    if conn_str:
        a["server.address"] = conn_str
    if extra:
        a.update(extra)
    return a

def _record_metrics(op: str, attrs: dict[str, AttributeValue], start_ns: int, ok: bool):
    if not CB_METRICS_ENABLED:
        return
    
    _ensure_instruments()
    dur_ms = (time.time_ns() - start_ns) / 1_000_000.0
    if _duration_ms:
        _duration_ms.record(dur_ms, attributes={"db.operation": op, **attrs})
    if _ops_total:
        _ops_total.add(1, attributes={"outcome": "success" if ok else "error", **attrs})
    if not ok and _errors_total:
        _errors_total.add(1, attributes={"db.operation": op, **attrs})

def _with_span(
    *,
    name: str,
    attrs: dict[str, AttributeValue],
    op: str,
    fn: Callable[[], Any],
):
    start = time.time_ns()
    with _get_tracer().start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
        try:
            res = fn()
            _record_metrics(op, attrs, start, ok=True)
            return res
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR))
            _record_metrics(op, attrs, start, ok=False)
            raise


class TracedCollection:
    """
    Wraps SafeCollection (or raw Collection) and emits spans/metrics
    for KV calls. Falls through for everything else.
    """
    def __init__(self, raw, *, bucket: str, scope: str, collection: str, conn_str: Optional[str] = None):
        self._raw = raw
        self._bucket = bucket
        self._scope = scope
        self._collection = collection
        self._conn_str = conn_str

    # Common wrapper
    def _wrap(self, op: str, call: Callable[[], Any]):
        _ensure_instruments()
        attrs = _attrs_base(
            bucket=self._bucket,
            scope=self._scope,
            collection=self._collection,
            op=op,
            conn_str=self._conn_str,
        )
        name = f"Couchbase.{op} {self._bucket}.{self._scope}.{self._collection}"
        return _with_span(name=name, attrs=attrs, op=op, fn=call)

    # KV operations you use today
    def insert(self, key, value, *a, **kw):
        return self._wrap("insert", lambda: self._raw.insert(key, value, *a, **kw))

    def upsert(self, key, value, *a, **kw):
        return self._wrap("upsert", lambda: self._raw.upsert(key, value, *a, **kw))

    def replace(self, key, value, *a, **kw):
        return self._wrap("replace", lambda: self._raw.replace(key, value, *a, **kw))

    def get(self, key, *a, **kw):
        return self._wrap("get",    lambda: self._raw.get(key, *a, **kw))

    def remove(self, key, *a, **kw):
        return self._wrap("remove", lambda: self._raw.remove(key, *a, **kw))

    def exists(self, key, *a, **kw):
        return self._wrap("exists", lambda: self._raw.exists(key, *a, **kw))

    # Everything else passes through
    def __getattr__(self, name):
        return getattr(self._raw, name)


class TracedCluster:
    def __init__(self, raw_cluster, *, default_bucket: str, conn_str: Optional[str] = None, traced_txns=None):
        self._raw = raw_cluster
        self._bucket = default_bucket
        self._conn_str = conn_str
        self._traced_txns = traced_txns  # optional, supplied by pool

    @staticmethod
    def _maybe_truncate(stmt: str, maxlen: int) -> str:
        if maxlen <= 0 or len(stmt) <= maxlen:
            return stmt
        return stmt[: maxlen - 1] + "…"
    

    def query(self, statement: str, *a, **kw):
        # Avoid leaking huge statements (toggle-able)
        _ensure_instruments()
        extra = {}
        if CB_TRACE_STATEMENTS and statement:
            stmt = statement if len(statement) <= CB_STATEMENT_MAXLEN else (statement[:CB_STATEMENT_MAXLEN] + "…")
            extra["db.statement"] = TracedCluster._maybe_truncate(statement, CB_STATEMENT_MAXLEN)
 
        attrs = _attrs_base(
            bucket=self._bucket,
            op="query",
            conn_str=self._conn_str,
            extra=extra,
        )
        name = f"Couchbase.Query {self._bucket}"
        return _with_span(
            name=name,
            attrs=attrs,
            op="query",
            fn=lambda: self._raw.query(statement, *a, **kw),
        )

    # Transactions: return traced facade if provided
    @property
    def transactions(self):
        if self._traced_txns is not None:
            return self._traced_txns
        return self._raw.transactions  # fallback

    # pass-through everything else
    def __getattr__(self, name):
        return getattr(self._raw, name)



class TracedTransactions:
    def __init__(self, raw_txns, *, bucket: str, scope: Optional[str] = None, conn_str: Optional[str] = None):
        self._raw = raw_txns
        self._bucket = bucket
        self._scope = scope
        self._conn_str = conn_str

    def run(self, user_fn: Callable, *a, **kw):
        _ensure_instruments()
        attrs = _attrs_base(bucket=self._bucket, scope=self._scope, op="txn.run", conn_str=self._conn_str)
        name  = f"Couchbase.Transaction {self._bucket}"

        def _wrapped(ctx):
            tctx = TracedAttemptContext(ctx, bucket=self._bucket, scope=self._scope, conn_str=self._conn_str)
            return user_fn(tctx)

        return _with_span(name=name, attrs=attrs, op="txn.run", fn=lambda: self._raw.run(_wrapped, *a, **kw))


class TracedAttemptContext:
    """Wraps AttemptContext methods we use, capturing child spans."""
    def __init__(self, raw_ctx, *, bucket: str, scope: Optional[str], conn_str: Optional[str]):
        self._raw = raw_ctx
        self._bucket = bucket
        self._scope = scope
        self._conn_str = conn_str

    def _wrap(self, op: str, call: Callable[[], Any]):
        attrs = _attrs_base(bucket=self._bucket, scope=self._scope, op=op, conn_str=self._conn_str)
        name  = f"Couchbase.Txn.{op} {self._bucket}"
        return _with_span(name=name, attrs=attrs, op=op, fn=call)

    def get(self, coll, key, *a, **kw):
        # coll may be TracedCollection or SafeCollection; extract names if present
        return self._wrap("get",    lambda: self._raw.get(getattr(coll, "_raw", coll), key, *a, **kw))

    def insert(self, coll, key, value, *a, **kw):
        return self._wrap("insert", lambda: self._raw.insert(getattr(coll, "_raw", coll), key, value, *a, **kw))

    def replace(self, doc, value=None, *a, **kw):
        # SDK replace can be ctx.replace(doc, body) where doc is a txn document ref
        return self._wrap("replace", lambda: self._raw.replace(doc, value, *a, **kw))

    def remove(self, doc, *a, **kw):
        return self._wrap("remove", lambda: self._raw.remove(doc, *a, **kw))

    # passthrough for anything else 
    def __getattr__(self, name):
        return getattr(self._raw, name)