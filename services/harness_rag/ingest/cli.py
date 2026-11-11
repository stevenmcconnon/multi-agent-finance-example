"""`uv run python -m harness_rag.ingest.cli run|worker|migrate`."""

from __future__ import annotations

import asyncio
import logging

import structlog
import typer

from .. import db
from ..config import settings
from . import worker
from .workflow import TASK_QUEUE, DiscoverWorkflow, discover, ingest_inline

app = typer.Typer(no_args_is_help=True, help="SEC EDGAR -> Postgres ingestion")
DEV_ORG = "00000000-0000-0000-0000-000000000001"  # acme (db/migrations/0002_seed_dev.sql)
log = structlog.get_logger(__name__)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


async def _run_inline(org: str, tickers: list[str], forms: list[str], limit: int) -> None:
    for msg in await discover(org, tickers, forms, limit):
        try:
            status = await ingest_inline(msg)
        except Exception as exc:  # noqa: BLE001  one filing failing must not stop the rest; the jobs row keeps the count
            log.error("ingest.attempt_failed", source_id=msg["source_id"], error=str(exc))
            continue
        typer.echo(f"{msg['ticker']} {msg['form']} {msg['source_id']}: {status}")


async def _run_temporal(org: str, tickers: list[str], forms: list[str], limit: int) -> None:
    client = await worker.connect()
    handle = await client.start_workflow(
        DiscoverWorkflow.run,
        args=[org, tickers, forms, limit],
        id=f"discover-{'-'.join(tickers)}-{'-'.join(forms)}",
        task_queue=TASK_QUEUE,
    )
    typer.echo(f"started DiscoverWorkflow {handle.id}; `cli worker` must be running")


@app.command()
def run(
    tickers: str = typer.Option(..., help="comma-separated, e.g. AAPL,MSFT"),
    forms: str = typer.Option("10-K,8-K"),
    limit: int = typer.Option(2, help="newest filings per ticker"),
    org: str = typer.Option(DEV_ORG),
    inline: bool | None = typer.Option(None, "--inline/--temporal", help="default: --inline when HR_MODE=local"),
):
    """Discover filings and ingest them: in-process (--inline) or as a Temporal DiscoverWorkflow."""
    go = _run_inline if (settings.hr_mode == "local" if inline is None else inline) else _run_temporal
    asyncio.run(go(org, _csv(tickers), _csv(forms), limit))


@app.command("worker")
def worker_cmd():
    """Temporal worker on task queue `ingest`."""
    asyncio.run(worker.run())


@app.command()
def migrate():
    """Apply db/migrations in order."""

    async def _run() -> list[str]:
        try:
            return await db.run_migrations()
        finally:
            await db.close_pool()  # an open pool keeps reconnecting and the process never exits

    applied = asyncio.run(_run())
    typer.echo(f"applied {applied or 'nothing (up to date)'}")


@app.callback()
def _logging():
    logging.basicConfig(level=settings.hr_log_level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.hr_log_level))
    )


if __name__ == "__main__":
    app()
