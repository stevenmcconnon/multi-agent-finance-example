# db/

The L2 Data layer's schema: Postgres 16 with the pgvector extension, plus the Cedar policies that decide access.

## Files

| File | What it owns |
|---|---|
| `migrations/0001_schema.sql` | Every table and index below. |
| `migrations/0002_seed_dev.sql` | Dev seed: org `acme`, users alice, bob, carol and their groups. No documents; ingest adds those. |
| `policies/` | Cedar policies and schema. See its [README](policies/README.md). |

## Tables (from `0001_schema.sql`)

| Table | One line |
|---|---|
| `orgs` | A tenant (slug and name). |
| `principals` | Users and groups in one namespace, per org. |
| `group_members` | Which principal belongs to which group. |
| `documents` | Metadata for each filing version; `uri` points at the original. |
| `document_acl` | Which principals may read each document (the item-level ACL, access control list). |
| `chunks` | Text pieces with a 512-dimension `embedding` (HNSW index) and a generated `content_tsv` (GIN index). |
| `facts` | Structured numbers (entity, concept, period, value, unit, provenance). |
| `ingest_jobs` | One row per filing version; `status` includes `dead`. |
| `idempotency_keys` | Stored results of keyed tool calls. |
| `audit_events` | One row per decision, retrieval, tool call, answer. |
| `eval_records` | Scores from evaluation runs. |

`schema_migrations` is created by `db.run_migrations()`, and LangGraph creates its own checkpoint tables.

## Store by purpose

Authority is the original document (S3 in production, EDGAR here). Search is this pgvector and `tsvector` index. Control is jobs, audit and checkpoints. Acceleration is Redis, not in this folder.

## How it is used

`python -m finance_agents.ingest.cli migrate` (or API boot) runs `run_migrations()`: files apply in numeric order, each in a transaction, and re-runs do nothing.

## Rules that hold here

- Migrations only move forward; add `0003_...sql`, never edit an applied file.
- Roles are not a column: a role is a group named `role:<role>` (see the header of `0002`).
- Documents are soft-deleted (`deleted_at`); queries must exclude them.
- The 512 dimension is fixed (ADR 0005).

## Read next

- [`../services/finance_agents/retrieval/README.md`](../services/finance_agents/retrieval/README.md).
- [`../docs/adr/0001-pgvector-over-opensearch.md`](../docs/adr/0001-pgvector-over-opensearch.md).
