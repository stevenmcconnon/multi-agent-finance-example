"""setup_otel(app): one TracerProvider exporting to the OTel collector AND to Phoenix (LangGraph spans via
openinference), FastAPI + psycopg instrumentation, Prometheus /metrics. Startup never fails here: a
collector that is down only costs dropped spans (BatchSpanProcessor logs and moves on).

In production: the collector sidecar fans out to X-Ray/Tempo and Phoenix; sampling is set there.
In this demo: both endpoints are localhost from docker compose and every span is exported, because the
traffic is one developer.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from ..config import settings

log = logging.getLogger(__name__)
_done = False


def setup_otel(app: FastAPI) -> None:
    global _done
    if _done:  # uvicorn --reload and tests may import the app twice
        return
    _done = True
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.otel_service_name}))
        for endpoint in (settings.otel_exporter_otlp_endpoint, settings.phoenix_collector_endpoint):
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=5))
            )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        PsycopgInstrumentor().instrument(tracer_provider=provider)
        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            LangChainInstrumentor().instrument(tracer_provider=provider)
        except Exception as exc:
            log.warning("LangGraph tracing not enabled: %s", exc)
    except Exception as exc:
        log.warning("OpenTelemetry not enabled: %s", exc)
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(excluded_handlers=["/metrics", "/healthz"]).instrument(app).expose(
            app, endpoint="/metrics", include_in_schema=False
        )
    except Exception as exc:
        log.warning("Prometheus /metrics not enabled: %s", exc)
