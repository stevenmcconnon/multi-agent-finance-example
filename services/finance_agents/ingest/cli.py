"""`uv run python -m finance_agents.ingest.cli demo|run|worker|migrate`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import structlog
import typer

from .. import db
from ..config import settings
from . import demo_corpus, edgar, jobs, worker
from .workflow import TASK_QUEUE, DiscoverWorkflow, IngestFilingWorkflow, discover, ingest_inline

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


def _edgar(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an EDGAR command; a refused User-Agent is one readable line and exit 2, not a stack trace."""

    async def go() -> Any:
        edgar.check_user_agent()
        await edgar.ticker_to_cik()  # one cheap call up front: a 403 here burns no job attempts
        try:
            return await coro
        finally:
            await db.close_pool()  # an open pool keeps reconnecting and the process never exits

    try:
        return asyncio.run(go())
    except edgar.EdgarAccessError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    finally:
        coro.close()  # no "never awaited" warning when the checks above refuse before it starts


def _inline(inline: bool | None) -> bool:
    return settings.hr_mode == "local" if inline is None else inline


@app.command()
def run(
    tickers: str = typer.Option(..., help="comma-separated, e.g. AAPL,MSFT"),
    forms: str = typer.Option("10-K,8-K"),
    limit: int = typer.Option(2, help="newest filings per form per ticker"),
    org: str = typer.Option(DEV_ORG),
    inline: bool | None = typer.Option(None, "--inline/--temporal", help="default: --inline when HR_MODE=local"),
):
    """Discover the newest filings and ingest them: in-process (--inline) or as a Temporal DiscoverWorkflow."""
    go = _run_inline if _inline(inline) else _run_temporal
    _edgar(go(org, _csv(tickers), _csv(forms), limit))


async def _ready(org: str, source_ids: list[str]) -> dict[str, int]:
    """{accession: chunk count} for filings that are searchable now: the database, not the job status, decides."""
    rows = await db.fetch(
        "SELECT d.source_id, count(*) AS chunks FROM documents d JOIN chunks c ON c.document_id = d.id "
        "WHERE d.org_id = %s AND d.source = 'edgar' AND d.source_id = ANY(%s) AND d.deleted_at IS NULL "
        "GROUP BY d.source_id",
        (org, source_ids),
    )
    return {r["source_id"]: r["chunks"] for r in rows}


async def _forget(msgs: list[dict[str, Any]]) -> None:
    """--rebuild: drop the two idempotency marks (finished job, stored content hash) so each filing is parsed again."""
    for m in msgs:
        key = jobs.idempotency_key(m["org_id"], m["source"], m["source_id"], m["version"])
        await db.execute("DELETE FROM ingest_jobs WHERE idempotency_key = %s", (key,))
        await db.execute(
            "UPDATE documents SET content_hash = '' WHERE org_id = %s AND source = %s AND source_id = %s",
            (m["org_id"], m["source"], m["source_id"]),
        )


async def _demo(org: str, inline: bool, rebuild: bool) -> int:
    """Ingest the pinned manifest; returns how many of its filings are NOT ready."""
    msgs = demo_corpus.messages(org)
    if rebuild:
        await _forget(msgs)
    client = None if inline else await worker.connect()

    async def one(m: dict[str, Any]) -> str:
        if client is None:
            return await ingest_inline(m)
        key = jobs.idempotency_key(m["org_id"], m["source"], m["source_id"], m["version"])
        return await client.execute_workflow(IngestFilingWorkflow.run, m, id=f"ingest-{key}", task_queue=TASK_QUEUE)

    errors: dict[str, str] = {}
    for m in msgs:
        try:
            await one(m)
        except Exception as exc:  # noqa: BLE001  one filing failing must not stop the rest; the summary reports it
            errors[m["source_id"]] = str(exc) or type(exc).__name__
    ready = await _ready(org, [m["source_id"] for m in msgs])
    for m in msgs:
        sid = m["source_id"]
        state = f"ready ({ready[sid]} chunks)" if sid in ready else f"FAILED {errors.get(sid, 'no chunks stored')}"
        typer.echo(f"{m['ticker']} {m['form']} {sid}: {state}")
    typer.echo(f"demo corpus: {len(ready)}/{len(msgs)} filings ready")
    return len(msgs) - len(ready)


@app.command()
def demo(
    org: str = typer.Option(DEV_ORG),
    inline: bool | None = typer.Option(None, "--inline/--temporal", help="default: --inline when HR_MODE=local"),
    rebuild: bool = typer.Option(False, help="parse and embed again even when a filing is already loaded"),
):
    """Ingest the five pinned demo filings (ingest/demo_corpus.py); exits 1 unless all of them are ready."""
    if _edgar(_demo(org, _inline(inline), rebuild)):
        raise typer.Exit(1)


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
