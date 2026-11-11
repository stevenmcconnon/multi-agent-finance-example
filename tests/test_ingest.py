"""Ingest: chunker, idempotency key, facts filter, dead-letter after 5 failures. No network, no DB."""

from __future__ import annotations

from itertools import pairwise

import pytest
from finance_agents.ingest import facts, jobs, parse
from finance_agents.ingest.acl import groups_for

MSG = {
    "org_id": "00000000-0000-0000-0000-000000000001",
    "source": "edgar",
    "source_id": "0000320193-24-000123",
    "version": "0000320193-24-000123",
    "form": "10-K",
    "ticker": "AAPL",
}


# ---- chunker ----
def _text(n_sentences: int, words_per: int = 10) -> str:
    return " ".join(" ".join(f"s{i}w{j}" for j in range(words_per - 1)) + f" s{i}end." for i in range(n_sentences))


def test_chunker_no_empty_chunks_and_overlap_is_sentence_tail():
    chunks = parse.chunk([("Item 1", _text(250))], size=100, overlap=20)
    assert len(chunks) > 2
    assert all(section == "Item 1" and text.strip() for section, text in chunks)
    for (_, a), (_, b) in pairwise(chunks):
        tail = a.split()[-20:]  # 2 ten-word sentences carried over
        assert b.split()[:20] == tail
        assert a.endswith("end.")  # boundary on a sentence end
    assert "s249end." in chunks[-1][1]  # nothing lost at the end


def test_chunker_hard_splits_a_giant_sentence_and_skips_empty_sections():
    chunks = parse.chunk([("x", " ".join(["w"] * 1500) + " end."), ("empty", "   ")], size=700, overlap=80)
    assert [s for s, _ in chunks] == ["x", "x", "x"]
    assert all(len(t.split()) <= 700 + 80 for _, t in chunks)


def test_parse_html_sections_and_hidden_xbrl_dropped():
    html = (
        b"<html><body><ix:header><p>HIDDEN</p></ix:header><script>var x=1</script>"
        b"<h2>Item 1. Business</h2><p>We make things.</p><p>Lots of them.</p><h2>Item 1A</h2><p>Risk.</p>"
    )
    assert parse.parse(html, "text/html") == [
        ("Item 1. Business", "We make things. Lots of them."),
        ("Item 1A", "Risk."),
    ]


# ---- idempotency ----
def test_idempotency_key_is_stable_and_version_sensitive():
    k = jobs.idempotency_key(MSG["org_id"], "edgar", MSG["source_id"], MSG["version"])
    assert k == jobs.idempotency_key(MSG["org_id"], "edgar", MSG["source_id"], MSG["version"]) and len(k) == 64
    assert k != jobs.idempotency_key(MSG["org_id"], "edgar", MSG["source_id"], "0000320193-24-000124")


# ---- facts ----
COMPANYFACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "start": "2023-10-01",
                            "end": "2024-09-28",
                            "val": 391035000000,
                            "accn": MSG["source_id"],
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-11-01",
                            "frame": "CY2024",
                        },
                        {
                            "start": "2022-09-25",
                            "end": "2023-09-30",
                            "val": 383285000000,
                            "accn": "0000320193-23-000106",
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-03",
                        },
                    ]
                }
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {
                            "start": "2023-10-01",
                            "end": "2024-09-28",
                            "val": 6.08,
                            "accn": MSG["source_id"],
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-11-01",
                        }
                    ]
                }
            },
            "Assets": {
                "units": {
                    "USD": [
                        {
                            "end": "2024-09-28",
                            "val": 364980000000,
                            "accn": MSG["source_id"],
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-11-01",
                        }
                    ]
                }
            },
            "SomethingElse": {"units": {"USD": [{"end": "2024-09-28", "val": 1, "accn": MSG["source_id"]}]}},
        }
    }
}


def test_facts_keep_only_this_filing_and_parse_units_periods():
    rows = facts.extract(COMPANYFACTS, MSG["source_id"], "AAPL")
    by = {r["concept"]: r for r in rows}
    assert set(by) == {"Revenues", "EarningsPerShareDiluted", "Assets"}
    assert by["Revenues"]["value"] == 391035000000 and by["Revenues"]["unit"] == "USD"
    assert (by["Revenues"]["period_start"], by["Revenues"]["period_end"]) == ("2023-10-01", "2024-09-28")
    assert by["Revenues"]["provenance"]["frame"] == "CY2024" and by["Revenues"]["provenance"]["fy"] == 2024
    assert by["EarningsPerShareDiluted"]["unit"] == "USD/shares"
    assert by["Assets"]["period_start"] is None and '"val": 364980000000' in by["Assets"]["provenance"]["raw_text"]
    assert all(r["entity"] == "AAPL" and r["provenance"]["accn"] == MSG["source_id"] for r in rows)


# ---- discover: a typo in one ticker must not fail the batch ----
async def test_discover_skips_an_unknown_ticker(monkeypatch):
    from finance_agents.ingest import edgar, workflow

    async def ciks():
        return {"AAPL": 320193}

    async def submissions(cik):
        return [
            {
                "form": "10-K",
                "accessionNumber": "0000320193-24-000123",
                "primaryDocument": "aapl.htm",
                "filingDate": "2024-11-01",
                "reportDate": "2024-09-28",
            }
        ]

    monkeypatch.setattr(edgar, "ticker_to_cik", ciks)
    monkeypatch.setattr(edgar, "submissions", submissions)
    msgs = await workflow.discover(MSG["org_id"], ["NOPE", "aapl"], ["10-K"], 2)
    assert [m["ticker"] for m in msgs] == ["AAPL"] and msgs[0]["cik"] == 320193


# ---- acl mapping ----
def test_acl_groups_follow_contracts():
    assert groups_for("8-K") == ("engineering", "finance", "analysts")
    assert groups_for("10-K/A") == groups_for("10-K") == ("finance", "analysts")


# ---- jobs: dead on the 5th failure (ingest_jobs.status='dead' is the dead letter) ----
class _FakeJobsTable:
    """Just enough of ingest_jobs for jobs.py's three statements."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def fetch(self, sql: str, params):
        if sql.startswith("INSERT INTO ingest_jobs"):
            row = self.rows.setdefault(params[4], {"id": params[4], "status": "queued", "attempts": 0})
            return [dict(row)]
        if "SET status = 'dead'" in sql:
            self.rows[params[0]]["status"] = "dead"
            return [{"id": params[0]}]
        status, error, inc, job_id = params
        row = self.rows[job_id]
        row.update(status=status, error=error, attempts=row["attempts"] + inc)
        return [{"attempts": row["attempts"]}]


async def test_jobs_go_dead_on_fifth_failure_and_then_skip():
    table, calls = _FakeJobsTable(), []

    async def boom(msg):
        calls.append(1)
        raise RuntimeError("edgar 503")

    for _ in range(jobs.MAX_ATTEMPTS - 1):
        with pytest.raises(RuntimeError):
            await jobs.run(MSG, boom, fetch=table.fetch)
        assert table.rows[next(iter(table.rows))]["status"] == "failed"
    with pytest.raises(jobs.JobDead):
        await jobs.run(MSG, boom, fetch=table.fetch)
    (row,) = table.rows.values()
    assert row["status"] == "dead" and row["attempts"] == 5 and row["error"] == "edgar 503"
    assert await jobs.run(MSG, boom, fetch=table.fetch) == "skipped" and len(calls) == 5


async def test_jobs_succeed_then_skip():
    table = _FakeJobsTable()

    async def ok(msg):
        return None

    assert await jobs.run(MSG, ok, fetch=table.fetch) == "succeeded"
    assert await jobs.run(MSG, ok, fetch=table.fetch) == "skipped"


# ---- writer registers the `ingest` audit kind (audit.AUDIT_KINDS is authz's list) ----
async def test_writer_ingest_audit_kind_is_accepted(monkeypatch):
    from finance_agents.authz import audit
    from finance_agents.ingest import writer

    seen = []

    async def sink(kind, principal, payload, thread_id):
        seen.append((kind, principal, payload))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    await audit.record("ingest", None, {"document_id": "x"})
    assert seen == [("ingest", None, {"document_id": "x"})]
    assert writer.content_hash(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_parse_html_drops_xml_declaration_and_comments():
    from finance_agents.ingest.parse import parse_html

    html = b"<?xml version='1.0' encoding='ASCII'?><html><body><!-- hidden --><p>Visible prose.</p></body></html>"
    text = " ".join(t for _, t in parse_html(html))
    assert "xml" not in text and "hidden" not in text and "Visible prose." in text


async def test_bedrock_embedder_retries_throttling_then_succeeds():
    import io
    import json

    from botocore.exceptions import ClientError
    from finance_agents.retrieval.embed import BedrockEmbedder

    calls = []

    class Client:
        def invoke_model(self, **_):
            calls.append(1)
            if len(calls) < 3:
                raise ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
            return {"body": io.BytesIO(json.dumps({"embedding": [0.0] * 512}).encode())}

    vecs = await BedrockEmbedder(client=Client()).embed(["a"])
    assert len(vecs) == 1 and len(calls) == 3
