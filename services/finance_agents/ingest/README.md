# ingest/

The L2 Data layer: Securities and Exchange Commission (SEC) EDGAR filings into Postgres as documents, access rules, embedded chunks and numeric facts.

## Files

| File | What it owns |
|---|---|
| `edgar.py` | EDGAR HTTP client; descriptive User-Agent, at most 10 requests per second. |
| `parse.py` | Pure: HTML bytes to sections, then `chunk()` (700 characters, 80 overlap). |
| `facts.py` | Pure: XBRL `companyfacts` to fact rows for one filing. |
| `acl.py` | `groups_for(form)`: which groups may read a form (the simulated item ACL). |
| `writer.py` | `write()`: one filing to `documents` + `document_acl` + `chunks` + `facts` in one transaction. |
| `jobs.py` | `ingest_jobs` bookkeeping; `MAX_ATTEMPTS = 5`. |
| `workflow.py` | Temporal activities, `IngestFilingWorkflow`, `DiscoverWorkflow`, and `ingest_inline`. |
| `worker.py` | Temporal worker on task queue `ingest`. |
| `cli.py` | `run`, `worker`, `migrate` commands. |
| `__main__.py` | Lets `python -m finance_agents.ingest` run the CLI. |

## How it is used

`make ingest` runs `python -m finance_agents.ingest.cli run --tickers AAPL,MSFT ...`, inline by default or through Temporal. The daily schedule in `infra/terraform/modules/schedule` runs the same CLI.

## Rules that hold here

- Network calls happen only inside Temporal activities (`discover`, `fetch`); `parse.py` and `facts.py` do no input or output. Workflows hold no input or output. Convention, not tested.
- The fifth failure marks a job dead: `ingest_jobs.status = 'dead'` is the dead letter. Tested by `test_jobs_go_dead_on_fifth_failure_and_then_skip` in `tests/test_ingest.py`.
- The idempotency key is a hash of org, source, source id and version (`test_idempotency_key_is_stable_and_version_sensitive`); an unchanged `content_hash` writes nothing.
- This folder may use `boto3` for S3 (the architecture test exempts `ingest/`).

## Production vs this demo

In production: Temporal on Fargate or Temporal Cloud; originals go to S3; sources like SharePoint via Microsoft Graph; PDFs via PyMuPDF or Textract.
In this demo: a Temporal dev server or `--inline` mode, HTML filings only, and ACLs simulated by form type, because EDGAR is public and needs no fleet.

## Read next

- [`../../docs/adr/0003-temporal-owns-ingestion-durability.md`](../../../docs/adr/0003-temporal-owns-ingestion-durability.md).
- [`../../docs/adr/0004-edgar-as-the-corpus.md`](../../../docs/adr/0004-edgar-as-the-corpus.md).
