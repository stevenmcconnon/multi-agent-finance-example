"""The pinned demo corpus: five filings, every field `workflow.message()` needs, no "newest" discovery.

In production: the corpus is whatever the source holds; `cli run` and the daily schedule discover new filings.
In this demo: `make ingest` loads exactly these five, because the example questions (docs/CONTRACTS.md)
  must keep working whatever Apple and Microsoft file next week.
"""

from __future__ import annotations

from typing import Any

from .workflow import message

AAPL, MSFT = 320193, 789019  # SEC central index keys

# Field names are EDGAR's own (data.sec.gov/submissions), so an entry is a drop-in for a discovered filing.
FILINGS: list[dict[str, Any]] = [
    {
        "cik": AAPL,
        "ticker": "AAPL",
        "form": "10-K",
        "accessionNumber": "0000320193-26-000026",
        "primaryDocument": "aapl-20260926.htm",
        "filingDate": "2026-11-03",
        "reportDate": "2026-09-26",
    },
    {
        "cik": MSFT,
        "ticker": "MSFT",
        "form": "10-K",
        "accessionNumber": "0001193125-26-323660",
        "primaryDocument": "msft-20260630.htm",
        "filingDate": "2026-07-29",
        "reportDate": "2026-06-30",
    },
    {  # notes issuance
        "cik": AAPL,
        "ticker": "AAPL",
        "form": "8-K",
        "accessionNumber": "0001140361-26-042798",
        "primaryDocument": "ef20085077_8k.htm",
        "filingDate": "2026-11-09",
        "reportDate": "2026-11-05",
    },
    {  # notes issuance
        "cik": MSFT,
        "ticker": "MSFT",
        "form": "8-K",
        "accessionNumber": "0001193125-26-447173",
        "primaryDocument": "d178557d8k.htm",
        "filingDate": "2026-11-04",
        "reportDate": "2026-10-29",
    },
    {  # a director not standing for re-election
        "cik": MSFT,
        "ticker": "MSFT",
        "form": "8-K",
        "accessionNumber": "0001193125-26-448357",
        "primaryDocument": "d177209d8k.htm",
        "filingDate": "2026-11-05",
        "reportDate": "2026-11-04",
    },
]


def messages(org_id: str) -> list[dict[str, Any]]:
    return [message(org_id, f["cik"], f["ticker"], f) for f in FILINGS]
