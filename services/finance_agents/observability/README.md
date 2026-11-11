# observability/

OpenTelemetry (OTel) traces and Prometheus metrics for the API; this is the in-app side of L1 Cloud / Platform.

## Files

| File | What it owns |
|---|---|
| `otel.py` | `setup_otel(app)`: one tracer provider exporting to the OTel collector and to Phoenix, FastAPI and psycopg instrumentation, LangGraph spans, and `/metrics`. `record_tool_span()`: one TOOL span per guarded tool call, called by `harness/runner.py`. |
| `__init__.py` | Package marker. |

## How it is used

`api/app.py` calls `setup_otel(app)` once at import. The collector config is `infra/otel/collector.yaml`; the Amazon Web Services (AWS) sidecar is `infra/terraform/modules/observability`.

## Rules that hold here

- Startup never fails here. A collector that is down only drops spans; each missing instrumentor logs a warning and the app keeps serving.
- `/metrics` and `/healthz` are excluded from metrics and from traces, so health checks do not bury the agent turns.
- Phoenix (http://localhost:6006) files traces under the project `multi-agent-finance`: each turn is one trace with the graph nodes and the TOOL spans.
- Tests set `OTEL_SDK_DISABLED=true` (`tests/conftest.py`).

## Production vs this demo

In production: the collector sidecar fans out to X-Ray or Tempo and Phoenix; sampling is set there.
In this demo: both endpoints are localhost from docker compose and every span is exported, because the traffic is one developer.

## Read next

- [`../../infra/README.md`](../../../infra/README.md).
