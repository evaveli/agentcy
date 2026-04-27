from __future__ import annotations

import threading
from typing import Optional
from weakref import WeakSet

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GRPCMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GRPCSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HTTPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HTTPSpanExporter,
)
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.b3 import B3MultiFormat
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics._internal.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agentcy.observability.config import (
    INSTRUMENTATIONS,
    OTEL_ENDPOINTS,
    OTEL_EXPORTER_OTLP_INSECURE,
    OTEL_EXPORTER_OTLP_PROTOCOL,
    OTEL_SKIP_PROVIDERS,
    otel_resource,
)

_OBS_PROVIDERS_STARTED = False
_OBS_PROVIDERS_LOCK = threading.Lock()
_INSTRUMENTED_APPS: "WeakSet[FastAPI]" = WeakSet()


def _mk_trace_exporter():
    if OTEL_EXPORTER_OTLP_PROTOCOL.lower().startswith("http"):
        return HTTPSpanExporter(endpoint=OTEL_ENDPOINTS["traces"])
    return GRPCSpanExporter(
        endpoint=OTEL_ENDPOINTS["traces"], insecure=OTEL_EXPORTER_OTLP_INSECURE
    )


def _mk_metric_exporter():
    if OTEL_EXPORTER_OTLP_PROTOCOL.lower().startswith("http"):
        return HTTPMetricExporter(endpoint=OTEL_ENDPOINTS["metrics"])
    return GRPCMetricExporter(
        endpoint=OTEL_ENDPOINTS["metrics"], insecure=OTEL_EXPORTER_OTLP_INSECURE
    )


def _providers_already_configured() -> bool:
    tp = trace.get_tracer_provider()
    return isinstance(tp, TracerProvider) and getattr(tp, "_agentcy_locked", False)


def _init_tracing(resource: Resource):
    if OTEL_SKIP_PROVIDERS:
        return

    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        if getattr(current, "_agentcy_locked", False) or getattr(
            current, "_active_span_processor", None
        ):
            return

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_mk_trace_exporter()))
    setattr(provider, "_agentcy_locked", True)
    trace.set_tracer_provider(provider)


def _init_metrics(resource: Resource):
    if OTEL_SKIP_PROVIDERS:
        return

    try:
        mp = metrics.get_meter_provider()
        if mp is not None and type(mp).__name__.lower().endswith("meterprovider"):
            return
    except Exception:
        pass

    reader = PeriodicExportingMetricReader(_mk_metric_exporter())
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[reader])
    )


def _set_propagators():
    set_global_textmap(
        CompositePropagator(
            [
                TraceContextTextMapPropagator(),
                B3MultiFormat(),
            ]
        )
    )


def _init_providers_once(resource: Resource) -> None:
    global _OBS_PROVIDERS_STARTED
    if _OBS_PROVIDERS_STARTED:
        return

    with _OBS_PROVIDERS_LOCK:
        if _OBS_PROVIDERS_STARTED:
            return

        _set_propagators()
        _init_tracing(resource)
        _init_metrics(resource)

        if INSTRUMENTATIONS.get("aio-pika"):
            try:
                AioPikaInstrumentor().instrument(
                    tracer_provider=trace.get_tracer_provider()
                )
            except Exception:
                pass
        if INSTRUMENTATIONS.get("requests"):
            try:
                RequestsInstrumentor().instrument()
            except Exception:
                pass
        if INSTRUMENTATIONS.get("httpx"):
            try:
                HTTPXClientInstrumentor().instrument()
            except Exception:
                pass
        if INSTRUMENTATIONS.get("logging"):
            try:
                LoggingInstrumentor().instrument(set_logging_format=True)
            except Exception:
                pass

        _OBS_PROVIDERS_STARTED = True


def _instrument_app_once(app: Optional[FastAPI]) -> None:
    if app is None or not INSTRUMENTATIONS.get("fastapi"):
        return
    if app in _INSTRUMENTED_APPS:
        return
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=trace.get_tracer_provider()
    )
    _INSTRUMENTED_APPS.add(app)


def start_observability(app: Optional[FastAPI] = None, app_title: Optional[str] = None):
    if getattr(start_observability, "_started", False):
        return
    resource = otel_resource(app_title or getattr(app, "title", None))

    _set_propagators()
    _init_providers_once(resource)
    _instrument_app_once(app)
    setattr(start_observability, "_started", True)


__all__ = ["start_observability"]
