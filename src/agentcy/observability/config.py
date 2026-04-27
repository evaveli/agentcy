from __future__ import annotations
import os, socket, uuid
from typing import Dict, Optional
from opentelemetry.sdk.resources import Resource

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints & protocol (env-overrideable)
# ─────────────────────────────────────────────────────────────────────────────
OTEL_EXPORTER_OTLP_PROTOCOL: str = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")  # "grpc" | "http/protobuf"
OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:  str = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",  OTEL_EXPORTER_OTLP_ENDPOINT)
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", OTEL_EXPORTER_OTLP_ENDPOINT)
OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:    str = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",    OTEL_EXPORTER_OTLP_ENDPOINT)
OTEL_EXPORTER_OTLP_INSECURE: bool = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() in {"1","true","yes"}
OTEL_SKIP_PROVIDERS: bool = os.getenv("AGENTCY_OTEL_SKIP_PROVIDERS", "0").lower() in {"1", "true", "yes"}


OTEL_ENDPOINTS: dict[str, str] = {
    "traces":  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
    "metrics": OTEL_EXPORTER_OTLP_METRICS_ENDPOINT,
    "logs":    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT,
}

# ─────────────────────────────────────────────────────────────────────────────
# Service identity (env-overrideable)
# ─────────────────────────────────────────────────────────────────────────────
# Prefer standard OTel envs; fall back to your framework’s SERVICE_NAME, etc.
SERVICE_NAME:        str  = os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME", "agentcy-service")
SERVICE_NAMESPACE:   str  = os.getenv("OTEL_SERVICE_NAMESPACE", "agentcy")
SERVICE_VERSION:     str  = os.getenv("OTEL_SERVICE_VERSION", "")              # optional
SERVICE_INSTANCE_ID: str  = os.getenv("SERVICE_INSTANCE_ID", f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
DEPLOY_ENV:          str  = os.getenv("DEPLOY_ENV", "prod")
OTEL_RESOURCE_ATTRS: str  = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")          # raw "k=v,k2=v2"
CB_TRACE_STATEMENTS: bool = os.getenv("CB_TRACE_STATEMENTS", "0").lower() in {"1","true","yes"}
CB_STATEMENT_MAXLEN: int  = int(os.getenv("CB_STATEMENT_MAXLEN", "1024"))
CB_METRICS_ENABLED:  bool = os.getenv("CB_METRICS_ENABLED", "1").lower() in {"1","true","yes"}

# ─────────────────────────────────────────────────────────────────────────────
# Canonical resource keys – used across the code-base
# ─────────────────────────────────────────────────────────────────────────────
class RKeys(str):
    SERVICE_NAME        = "service.name"
    SERVICE_NAMESPACE   = "service.namespace"
    SERVICE_VERSION     = "service.version"
    SERVICE_INSTANCE_ID = "service.instance.id"
    DEPLOY_ENV          = "deployment.environment"
    CB_TRACE_STATEMENTS = "couchbase.trace.statements"
    CB_STATEMENT_MAXLEN = "couchbase.statement.maxlen"
    CB_METRICS_ENABLED  = "couchbase.metrics.enabled"

# ─────────────────────────────────────────────────────────────────────────────
# Instrumentation toggles (env-overrideable)
# ─────────────────────────────────────────────────────────────────────────────
INSTR_FASTAPI:  bool = os.getenv("OTEL_INSTRUMENT_FASTAPI",  "1").lower() in {"1","true","yes"}
INSTR_AIOPIKA:  bool = os.getenv("OTEL_INSTRUMENT_AIOPIKA",  "1").lower() in {"1","true","yes"}
INSTR_DBAPI:    bool = os.getenv("OTEL_INSTRUMENT_DBAPI",    "1").lower() in {"1","true","yes"}
INSTR_LOGGING:  bool = os.getenv("OTEL_INSTRUMENT_LOGGING",  "1").lower() in {"1","true","yes"}
INSTR_HTTPX:    bool = os.getenv("OTEL_INSTRUMENT_HTTPX",    "1").lower() in {"1","true","yes"}
INSTR_REQUESTS: bool = os.getenv("OTEL_INSTRUMENT_REQUESTS", "1").lower() in {"1","true","yes"}

INSTRUMENTATIONS: dict[str, bool] = {
    "fastapi":  INSTR_FASTAPI,
    "aio-pika": INSTR_AIOPIKA,
    "dbapi":    INSTR_DBAPI,
    "logging":  INSTR_LOGGING,
    "httpx":    INSTR_HTTPX,
    "requests": INSTR_REQUESTS,
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper – parse k=v,k2=v2 into a dict
# ─────────────────────────────────────────────────────────────────────────────
def _parse_kv(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for kv in (raw or "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Helper – build the OpenTelemetry Resource once
# ─────────────────────────────────────────────────────────────────────────────
def otel_resource(app_title: Optional[str] = None) -> Resource:
    """
    Build a Resource with sane defaults + env overrides.
    app_title lets FastAPI apps avoid env if desired.
    """
    base = {
        RKeys.SERVICE_NAME:        SERVICE_NAME or (app_title or "agentcy-service"),
        RKeys.SERVICE_NAMESPACE:   SERVICE_NAMESPACE,
        RKeys.SERVICE_INSTANCE_ID: SERVICE_INSTANCE_ID,
        RKeys.DEPLOY_ENV:          DEPLOY_ENV,
    }
    if SERVICE_VERSION:
        base[RKeys.SERVICE_VERSION] = SERVICE_VERSION

    # Allow extension/override via OTEL_RESOURCE_ATTRIBUTES
    base.update(_parse_kv(OTEL_RESOURCE_ATTRS))
    return Resource.create(base)
