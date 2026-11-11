# ingest/

The L2 Data layer: Securities and Exchange Commission (SEC) EDGAR filings into Postgres as documents, access rules, embedded chunks and numeric facts.

## Files

| File | What it owns |
|---|---|
| `edgar.py` | EDGAR HTTP client; descriptive User-Agent, at most 10 requests per second. |
| `parse.py` | Pure: HTML bytes to sections named from the SEC item headings ("Cover page", then for example "Item 1A. Risk Factors"), then `chunk()` (about 700 words, 80 words of overlap). |
| `facts.py` | Pure: XBRL `companyfacts` to fact rows for one filing. |
| `acl.py` | `groups_for(form)`: which groups may read a form (the simulated item ACL). |
| `writer.py` | `write()`: one filing to `documents` + `document_acl` + `chunks` + `facts` in one transaction. |
| `jobs.py` | `ingest_jobs` bookkeeping; `MAX_ATTEMPTS = 5`. |
| `workflow.py` | Temporal activities, `IngestFilingWorkflow`, `DiscoverWorkflow`, and `ingest_inline`. |
| `worker.py` | Temporal worker on task queue `ingest`. |
| `demo_corpus.py` | The pinned demo corpus: five filings (two 10-K, three 8-K) with every field a discovered filing has. |
| `cli.py` | `demo`, `run`, `worker`, `migrate` commands. |
| `__main__.py` | Lets `python -m finance_agents.ingest` run the CLI. |

## How it is used

`make ingest` runs `python -m finance_agents.ingest.cli demo` (the five pinned filings; it fails unless all five are ready) and then `make warm`, one real question that loads the embedding and reranking models. `make ingest-rebuild` is `demo --rebuild` (parse and embed again); `make ingest-latest` is `run --tickers AAPL,MSFT --forms 10-K,8-K --limit 1` (the newest filing per form per ticker). Both commands run inline when `HR_MODE=local` and take `--temporal` to run as Temporal workflows (start the worker with `docker compose --profile temporal up -d`). The daily schedule in `infra/terraform/modules/schedule` runs the `run` command.

## Rules that hold here

- Network calls happen only inside Temporal activities (`discover`, `fetch`); `parse.py` and `facts.py` do no input or output. Workflows hold no input or output. Convention, not tested.
- The fifth failure marks a job dead: `ingest_jobs.status = 'dead'` is the dead letter. Tested by `test_jobs_go_dead_on_fifth_failure_and_then_skip` in `tests/test_ingest.py`.
- The idempotency key is a hash of org, source, source id and version (`test_idempotency_key_is_stable_and_version_sensitive`); an unchanged `content_hash` writes nothing.
- Discovery logs and skips an unknown ticker instead of retrying or failing the rest of the batch
  (`test_discover_skips_an_unknown_ticker`).
- This folder may use `boto3` for S3 (the architecture test exempts `ingest/`).

## Production vs this demo

In production: Temporal on Fargate or Temporal Cloud; originals go to S3; sources like SharePoint via Microsoft Graph; PDFs via PyMuPDF or Textract.
In this demo: `--inline` mode by default with the Temporal dev server and worker optional, a pinned corpus so the example questions keep working, HTML filings only, and ACLs simulated by form type, because EDGAR is public and needs no fleet. The tests call the activities as plain functions; the Temporal workflows and worker have not been run live.

## Read next

- [`../../docs/adr/0003-temporal-owns-ingestion-durability.md`](../../../docs/adr/0003-temporal-owns-ingestion-durability.md).
- [`../../docs/adr/0004-edgar-as-the-corpus.md`](../../../docs/adr/0004-edgar-as-the-corpus.md).
