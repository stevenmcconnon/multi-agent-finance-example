"""XBRL companyfacts -> fact rows for one filing. Pure."""

from __future__ import annotations

import json
from typing import Any

CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "EarningsPerShareDiluted",
    "CashAndCashEquivalentsAtCarryingValue",
)


def extract(companyfacts: dict[str, Any], accn: str, entity: str) -> list[dict[str, Any]]:
    """Facts from `us-gaap` concepts in CONCEPTS whose `accn` is this filing, so each ties to its document."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    rows = []
    for concept in CONCEPTS:
        for unit, items in gaap.get(concept, {}).get("units", {}).items():
            for f in items:
                if f.get("accn") != accn or f.get("val") is None:
                    continue
                rows.append(
                    {
                        "entity": entity,
                        "concept": concept,
                        "period_start": f.get("start"),
                        "period_end": f.get("end"),
                        "value": f["val"],
                        "unit": unit,
                        "provenance": {k: f.get(k) for k in ("form", "accn", "fy", "fp", "frame", "filed")}
                        | {"raw_text": json.dumps(f, sort_keys=True)},
                    }
                )
    return rows
