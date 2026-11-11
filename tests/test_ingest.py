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


# ---- section names: SEC item headings in the text flow (EDGAR HTML has no h-tags) ----
def test_parse_10k_items_name_sections_and_the_table_of_contents_does_not():
    html = (
        b"<html><body><div>Annual report cover.</div>"
        # table of contents: the same headings, split across cells
        b"<table><tr><td>Part I</td></tr><tr><td><a>Item 1.</a></td><td><a>Business</a></td><td>1</td></tr>"
        b"<tr><td><a>Item 1A.</a></td><td><a>Risk Factors</a></td><td>5</td></tr></table>"
        b"<div><span>PART I</span></div><div><span>Item 1.&#160;&#160;&#160;Business</span></div>"
        b"<div>We make phones. See Part I, Item 1A of this Form 10-K under the heading Risk Factors.</div>"
        b"<div>PART I</div><div>Item 1A</div>"  # running page header: no period, not a heading
        b"<p><span>ITEM 1A. RIS</span><span>K FACTORS</span></p>"  # one styled heading, two text nodes
        b"<div>Our supply chain is concentrated.</div>"
        b"<div>As set out in <a>Item 7. Management's Discussion</a>, costs rose.</div>"
        b"</body></html>"
    )
    sections = parse.parse(html, "text/html")
    assert [s for s, _ in sections] == ["Cover page", "Item 1. Business", "Item 1A. RISK FACTORS"]
    cover, business, risks = (t for _, t in sections)
    assert cover.startswith("Annual report cover.") and "Risk Factors 5" in cover  # the contents stay on the cover
    assert business.startswith("We make phones.")
    assert (
        "supply chain" in risks and "As set out in Item 7. Management's Discussion , costs rose." in risks
    )  # a cross-reference
    assert {s for s, _ in parse.chunk(sections)} == {s for s, _ in sections}


def test_parse_8k_items_with_and_without_an_inline_title():
    html = (
        "<html><body><p>FORM 8-K CURRENT REPORT</p>"
        "<p>Item 5.02. Departure of Directors or Certain Officers; Election of Directors; Appointment of "
        "Certain Officers; Compensatory Arrangements of Certain Officers. </p><p>(b) A director will not stand.</p>"
        "<div><div><span>Item 8.01</span></div><div><span>Other Events.</span></div></div><div>Notes were sold.</div>"
        "</body></html>"
    ).encode()
    assert parse.parse(html, "text/html") == [
        ("Cover page", "FORM 8-K CURRENT REPORT"),
        (
            "Item 5.02. Departure of Directors or Certain Officers; Election of Directors; Appointment of "
            "Certain Officers; Compensatory Arrangements of Certain Officers",
            "(b) A director will not stand.",
        ),
        ("Item 8.01. Other Events", "Notes were sold."),
    ]


# ---- discover: newest `limit` per form, so two fresh 8-Ks cannot crowd out the 10-K ----
async def test_discover_takes_the_newest_per_form(monkeypatch):
    from finance_agents.ingest import edgar, workflow

    async def ciks():
        return {"MSFT": 789019}

    async def submissions(cik):
        rows = [("8-K", "a3"), ("8-K", "a2"), ("4", "x"), ("10-K", "k2"), ("8-K", "a1"), ("10-K", "k1")]
        return [
            {"form": f, "accessionNumber": a, "primaryDocument": "d.htm", "filingDate": "2026-01-01", "reportDate": ""}
            for f, a in rows
        ]

    monkeypatch.setattr(edgar, "ticker_to_cik", ciks)
    monkeypatch.setattr(edgar, "submissions", submissions)
    msgs = await workflow.discover(MSG["org_id"], ["MSFT"], ["10-K", "8-K"], 1)
    assert [(m["form"], m["source_id"]) for m in msgs] == [("10-K", "k2"), ("8-K", "a3")]


# ---- demo corpus: pinned manifest, honest summary, non-zero exit on a half load ----
def _cli(monkeypatch, agent="demo-app someone@corp.test"):
    from finance_agents.ingest import cli, edgar

    async def ciks():
        return {}

    monkeypatch.setattr(edgar.settings, "edgar_user_agent", agent)
    monkeypatch.setattr(edgar, "ticker_to_cik", ciks)
    return cli


def test_demo_corpus_manifest_is_the_five_pinned_filings():
    from finance_agents.ingest import demo_corpus

    msgs = demo_corpus.messages(MSG["org_id"])
    assert [(m["ticker"], m["form"], m["source_id"]) for m in msgs] == [
        ("AAPL", "10-K", "0000320193-26-000026"),
        ("MSFT", "10-K", "0001193125-26-323660"),
        ("AAPL", "8-K", "0001140361-26-042798"),
        ("MSFT", "8-K", "0001193125-26-447173"),
        ("MSFT", "8-K", "0001193125-26-448357"),
    ]
    assert msgs[0]["uri"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm"
    assert all(m["filed_at"] and m["report_date"] and m["version"] == m["source_id"] for m in msgs)


def test_demo_command_reports_each_filing_and_fails_on_a_half_load(monkeypatch):
    from typer.testing import CliRunner

    cli = _cli(monkeypatch)

    async def ingest(msg):
        if msg["form"] == "10-K" and msg["ticker"] == "MSFT":
            raise RuntimeError("edgar 503")
        return "succeeded"

    async def ready(org, source_ids):  # what the database holds afterwards
        return {s: 7 for s in source_ids if s != "0001193125-26-323660"}

    monkeypatch.setattr(cli, "ingest_inline", ingest)
    monkeypatch.setattr(cli, "_ready", ready)
    result = CliRunner().invoke(cli.app, ["demo", "--inline"])
    assert result.exit_code == 1
    lines = result.output.strip().splitlines()
    assert lines[0] == "AAPL 10-K 0000320193-26-000026: ready (7 chunks)"
    assert lines[1] == "MSFT 10-K 0001193125-26-323660: FAILED edgar 503"
    assert lines[-1] == "demo corpus: 4/5 filings ready" and len(lines) == 6

    async def all_ready(org, source_ids):
        return {s: 7 for s in source_ids}

    monkeypatch.setattr(cli, "_ready", all_ready)
    result = CliRunner().invoke(cli.app, ["demo", "--inline"])
    assert result.exit_code == 0 and result.output.strip().endswith("demo corpus: 5/5 filings ready")


# ---- EDGAR refuses a missing or placeholder User-Agent: one readable line, exit 2, no stack trace ----
def test_placeholder_user_agent_is_refused_before_any_request(monkeypatch):
    from typer.testing import CliRunner

    cli = _cli(monkeypatch, agent="multi-agent-finance-example contact@example.com")
    for args in (["demo"], ["run", "--tickers", "AAPL"]):
        result = CliRunner().invoke(cli.app, args)
        assert result.exit_code == 2 and "Traceback" not in result.output
        assert "set EDGAR_USER_AGENT in .env" in result.output and len(result.output.strip().splitlines()) == 1


async def test_edgar_403_becomes_the_user_agent_message(monkeypatch):
    from functools import partial

    import httpx
    from finance_agents.ingest import edgar

    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    monkeypatch.setattr(edgar.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(edgar.EdgarAccessError, match="EDGAR_USER_AGENT"):
        await edgar.fetch_document("https://www.sec.gov/x")
