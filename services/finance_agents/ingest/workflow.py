"""Temporal: DiscoverWorkflow(tickers, forms, limit) -> one IngestFilingWorkflow child per filing.

Every activity is a plain async function and works without Temporal (cli.py --inline calls them directly).
Workflows hold no IO: claim/fetch/parse_and_write/finish are activities; Temporal retries each up to
MAX_ATTEMPTS with backoff, and the final failure marks the job dead (ingest_jobs.status='dead' is the dead letter).
"""

from __future__ import annotations

# ruff: noqa: PLR0402  the `from pkg import mod` form it wants re-executes mod inside the Temporal sandbox
import asyncio
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    import structlog

    # full dotted imports: `from . import x` re-executes x inside the sandbox instead of passing it through
    import finance_agents.ingest.edgar as edgar
    import finance_agents.ingest.facts as facts
    import finance_agents.ingest.jobs as jobs
    import finance_agents.ingest.parse as parse
    import finance_agents.ingest.writer as writer
    from finance_agents.config import settings

log = structlog.get_logger(__name__)
TASK_QUEUE = "ingest"
RETRY = RetryPolicy(
    maximum_attempts=jobs.MAX_ATTEMPTS,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
)
# In production: originals go to S3 (S3_RAW_BUCKET), the Authority store every worker host can read.
# In this demo: a gitignored local folder when no bucket is set, because the worker runs on one laptop.
RAW_DIR = Path("data/edgar")


def message(org_id: str, cik: int, ticker: str, filing: dict[str, Any]) -> dict[str, Any]:
    accn = filing["accessionNumber"]
    return {
        "org_id": str(org_id),
        "source": "edgar",
        "source_id": accn,
        "version": accn,
        "cik": cik,
        "ticker": ticker,
        "form": filing["form"],
        "primary_doc": filing["primaryDocument"],
        "filed_at": filing["filingDate"],
        "report_date": filing.get("reportDate") or None,
        "title": f"{ticker} {filing['form']} filed {filing['filingDate']}",
        "uri": edgar.primary_document_url(cik, accn, filing["primaryDocument"]),
    }


# ---------- raw originals (Authority store) ----------
def _store_raw(msg: dict[str, Any], data: bytes) -> str:
    key = f"edgar/{msg['cik']}/{msg['source_id']}/{msg['primary_doc']}"
    if settings.s3_raw_bucket:
        import boto3

        boto3.client("s3", region_name=settings.aws_region).put_object(
            Bucket=settings.s3_raw_bucket, Key=key, Body=data
        )
        return f"s3://{settings.s3_raw_bucket}/{key}"
    path = RAW_DIR / key.removeprefix("edgar/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _load_raw(uri: str) -> bytes:
    if uri.startswith("s3://"):
        import boto3

        bucket, key = uri[5:].split("/", 1)
        return boto3.client("s3", region_name=settings.aws_region).get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(uri).read_bytes()


# ---------- activities ----------
@activity.defn
async def discover(org_id: str, tickers: list[str], forms: list[str], limit: int) -> list[dict[str, Any]]:
    """Newest `limit` filings per ticker among `forms`, as ingest messages."""
    ciks = await edgar.ticker_to_cik()
    out: list[dict[str, Any]] = []
    for ticker in tickers:
        cik = ciks.get(ticker.upper())
        if cik is None:  # a typo must not fail (and retry) the whole batch
            log.warning("ingest.unknown_ticker", ticker=ticker)
            continue
        picked = [f for f in await edgar.submissions(cik) if f["form"] in forms and f["primaryDocument"]][:limit]
        out.extend(message(org_id, cik, ticker.upper(), f) for f in picked)
    log.info("ingest.discovered", count=len(out), tickers=tickers, forms=forms)
    return out


@activity.defn
async def claim(msg: dict[str, Any]) -> str | None:
    """Job id, or None when this filing version already succeeded / is dead."""
    job = await jobs.claim(msg)
    if job is None:
        return None
    await jobs.mark(job["id"], "running")
    return str(job["id"])


@activity.defn
async def fetch(msg: dict[str, Any]) -> dict[str, str]:
    """Download the primary document and park the original; returns {raw_uri, content_type} (small payload)."""
    data, content_type = await edgar.fetch_document(msg["uri"])
    return {"raw_uri": await asyncio.to_thread(_store_raw, msg, data), "content_type": content_type}


@activity.defn
async def parse_and_write(msg: dict[str, Any], raw: dict[str, str]) -> dict[str, Any]:
    data = await asyncio.to_thread(_load_raw, raw["raw_uri"])
    # CPU-bound (lxml over a multi-MB filing): keep it off the worker's event loop
    chunks = await asyncio.to_thread(lambda: parse.chunk(parse.parse(data, raw["content_type"])))
    # shortcut: companyfacts (one JSON per company, MBs) is fetched per filing; cache per cik if volume grows
    fact_rows = facts.extract(await edgar.company_facts(msg["cik"]), msg["source_id"], msg["ticker"])
    return await writer.write(msg, data, raw["content_type"], chunks, fact_rows)


@activity.defn
async def finish(job_id: str, status: str, error: str | None, attempts: int, msg: dict[str, Any]) -> str:
    """Record the outcome; a failure that exhausted Temporal's retries is stored as dead (the dead letter)."""
    return await jobs.mark(job_id, status, error, attempts=attempts)


async def ingest_inline(msg: dict[str, Any]) -> str:
    """The same steps without Temporal, one attempt, through the jobs table (cli --inline)."""

    async def work(m: dict[str, Any]) -> None:
        await parse_and_write(m, await fetch(m))

    return await jobs.run(msg, work)


# ---------- workflows ----------
@workflow.defn
class IngestFilingWorkflow:
    @workflow.run
    async def run(self, msg: dict[str, Any]) -> str:
        job_id = await workflow.execute_activity(
            claim, msg, start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY
        )
        if job_id is None:
            return "skipped"
        try:
            raw = await workflow.execute_activity(
                fetch, msg, start_to_close_timeout=timedelta(minutes=2), retry_policy=RETRY
            )
            await workflow.execute_activity(
                parse_and_write,
                args=[msg, raw],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RETRY,
            )
        except ActivityError as exc:
            cause = exc.cause
            await workflow.execute_activity(
                finish,
                args=[job_id, "failed", str(cause)[:2000], jobs.MAX_ATTEMPTS, msg],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
            )
            raise
        return await workflow.execute_activity(
            finish,
            args=[job_id, "succeeded", None, 0, msg],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )


@workflow.defn
class DiscoverWorkflow:
    @workflow.run
    async def run(self, org_id: str, tickers: list[str], forms: list[str], limit: int) -> int:
        msgs = await workflow.execute_activity(
            discover,
            args=[org_id, tickers, forms, limit],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
        )
        handles = []
        for m in msgs:
            try:
                handles.append(
                    await workflow.start_child_workflow(
                        IngestFilingWorkflow.run,
                        m,
                        task_queue=TASK_QUEUE,
                        # workflow id = idempotency key: Temporal refuses a second start of the same filing version
                        id=f"ingest-{jobs.idempotency_key(m['org_id'], m['source'], m['source_id'], m['version'])}",
                    )
                )
            except WorkflowAlreadyStartedError:
                workflow.logger.info("ingest.already_running source_id=%s", m["source_id"])
        await asyncio.gather(*handles, return_exceptions=True)  # a dead filing does not fail the batch
        return len(handles)


ACTIVITIES: list[Callable[..., Any]] = [discover, claim, fetch, parse_and_write, finish]
WORKFLOWS = [IngestFilingWorkflow, DiscoverWorkflow]
