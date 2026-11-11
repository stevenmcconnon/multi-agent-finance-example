"""L1/L6 observability: OTel traces (collector + Phoenix), psycopg/FastAPI instrumentation, Prometheus."""

from __future__ import annotations

from .otel import setup_otel

__all__ = ["setup_otel"]
