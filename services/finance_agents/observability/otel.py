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

        # Phoenix files traces under this project name instead of "default".
        resource = Resource.create(
            {SERVICE_NAME: settings.otel_service_name, "openinference.project.name": "multi-agent-finance"}
        )
        provider = TracerProvider(resource=resource)
        for endpoint in (settings.otel_exporter_otlp_endpoint, settings.phoenix_collector_endpoint):
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=5))
            )
        trace.set_tracer_provider(provider)
        # The container health check calls /healthz every few seconds; traced, it buries the agent turns.
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="healthz,metrics")
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


def record_tool_span(tool: str, arguments: dict, ok: bool, error: str | None, elapsed_s: float, user: str) -> None:
    """One TOOL span per guarded tool call, so a trace shows what the Researcher ran and how it ended.
    Emitted after the call with its real start time; never raises into the caller."""
    try:
        import json
        import time

        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        end = time.time_ns()
        span = trace.get_tracer("finance_agents.harness").start_span(
            f"tool {tool}",
            start_time=end - int(elapsed_s * 1e9),
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": tool,
                "input.value": json.dumps(arguments, default=str)[:2000],
                "output.value": "ok" if ok else f"refused or failed: {error}",
                "user.id": user,
            },
        )
        span.set_status(Status(StatusCode.OK if ok else StatusCode.ERROR, None if ok else error))
        span.end(end_time=end)
    except Exception as exc:  # tracing must never break a tool call
        log.debug("tool span not recorded: %s", exc)
