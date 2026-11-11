"""SEC EDGAR client. Fair access: a descriptive User-Agent and at most 10 requests/second."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..config import settings

# SEC fair-access policy: the User-Agent must name the app and a contact email, or every call is a 403.
HEADERS = {"User-Agent": settings.edgar_user_agent, "Accept-Encoding": "gzip, deflate"}
MIN_INTERVAL = 0.1  # SEC allows 10 req/s

USER_AGENT_HELP = (
    "SEC EDGAR refused the request: set EDGAR_USER_AGENT in .env to your app name plus a contact email "
    "(for example `EDGAR_USER_AGENT=finance-agents-demo jane@yourcompany.com`), then run `make up` again."
)


class EdgarAccessError(RuntimeError):
    """EDGAR answers 403 to a missing or placeholder User-Agent; retrying cannot fix it."""


def check_user_agent() -> None:
    if "@example.com" in settings.edgar_user_agent or "@" not in settings.edgar_user_agent:
        raise EdgarAccessError(USER_AGENT_HELP)


_gate = asyncio.Lock()
_next_ok = 0.0
_tickers: dict[str, int] = {}


async def _get(url: str) -> httpx.Response:
    global _next_ok
    async with _gate:
        wait = _next_ok - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _next_ok = time.monotonic() + MIN_INTERVAL
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code == 403:
            raise EdgarAccessError(USER_AGENT_HELP)
        resp.raise_for_status()
        return resp


async def ticker_to_cik() -> dict[str, int]:
    """{TICKER: cik}; fetched once per process."""
    if not _tickers:
        data = (await _get("https://www.sec.gov/files/company_tickers.json")).json()
        _tickers.update({row["ticker"].upper(): int(row["cik_str"]) for row in data.values()})
    return _tickers


async def submissions(cik: int) -> list[dict[str, Any]]:
    """Recent filings, newest first: form, accessionNumber, primaryDocument, filingDate, reportDate."""
    # shortcut: only `filings.recent` (last ~1000 filings), skip the paged `filings.files`; add when limits exceed it
    recent = (await _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")).json()["filings"]["recent"]
    keys = ("form", "accessionNumber", "primaryDocument", "filingDate", "reportDate")
    return [dict(zip(keys, row, strict=False)) for row in zip(*(recent[k] for k in keys), strict=False)]


def primary_document_url(cik: int, accession: str, primary_doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_doc}"


async def fetch_document(url: str) -> tuple[bytes, str]:
    resp = await _get(url)
    return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]


async def company_facts(cik: int) -> dict[str, Any]:
    return (await _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")).json()
