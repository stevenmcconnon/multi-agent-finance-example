-- harness-rag control plane + search. Store by purpose:
--   Authority = S3 originals (sharepoint stand-in); Search = this pgvector/tsvector index;
--   Control = checkpoints/jobs/audit/evals/idempotency; Acceleration = Redis (not here).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- identity & authority mirror ----------
CREATE TABLE IF NOT EXISTS orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS principals (          -- users and groups, one namespace
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs(id),
  kind text NOT NULL CHECK (kind IN ('user','group')),
  external_id text NOT NULL,                        -- cognito sub / group name
  display_name text,
  UNIQUE (org_id, kind, external_id)
);
CREATE TABLE IF NOT EXISTS group_members (
  group_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  member_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, member_id)
);

-- ---------- documents (replicated metadata; S3/EDGAR stays source of truth) ----------
CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs(id),
  source text NOT NULL,                             -- 'edgar' | 'upload' | 'sharepoint'
  source_id text NOT NULL,                          -- edgar accession number etc.
  version text NOT NULL,                            -- amendment / etag / version id
  title text,
  uri text NOT NULL,                                -- s3://... or https://www.sec.gov/...
  content_hash text NOT NULL,
  mime_type text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,      -- cik, ticker, form, period, filed_at ...
  ingested_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  UNIQUE (org_id, source, source_id, version)
);
CREATE TABLE IF NOT EXISTS document_acl (           -- item-level permission principals
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  principal_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  permission text NOT NULL DEFAULT 'read',
  PRIMARY KEY (document_id, principal_id, permission)
);
CREATE INDEX IF NOT EXISTS document_acl_principal_idx ON document_acl(principal_id);

-- ---------- chunks: hybrid index (dense + sparse) ----------
CREATE TABLE IF NOT EXISTS chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id uuid NOT NULL REFERENCES orgs(id),
  ordinal int NOT NULL,
  section text,
  content text NOT NULL,
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  embedding vector(512),                            -- jina-v2-small / titan-v2@512; see embedding_model
  embedding_model text NOT NULL,
  token_count int,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin(content_tsv);
CREATE INDEX IF NOT EXISTS chunks_org_idx ON chunks(org_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- ---------- structured facts: numbers never guessed ----------
CREATE TABLE IF NOT EXISTS facts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id uuid NOT NULL REFERENCES orgs(id),
  chunk_id uuid REFERENCES chunks(id),
  entity text NOT NULL,                             -- e.g. ticker / CIK
  concept text NOT NULL,                            -- e.g. Revenues, NetIncomeLoss
  period_start date,
  period_end date,
  value numeric NOT NULL,
  unit text NOT NULL,                               -- USD, shares, pure
  provenance jsonb NOT NULL                         -- {page, table, row, col, raw_text}
);
CREATE INDEX IF NOT EXISTS facts_lookup_idx ON facts(org_id, entity, concept, period_end);

-- ---------- control plane ----------
CREATE TABLE IF NOT EXISTS ingest_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs(id),
  source text NOT NULL,
  source_id text NOT NULL,
  version text NOT NULL,
  idempotency_key text UNIQUE NOT NULL,             -- sha256(org,source,source_id,version)
  status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','dead')),
  attempts int NOT NULL DEFAULT 0,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS idempotency_keys (       -- tool-call idempotency
  key text PRIMARY KEY,
  tool text NOT NULL,
  response jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  org_id uuid,
  user_id text,
  thread_id text,
  kind text NOT NULL,                               -- policy.decision | retrieval | tool.call | model.call | answer | hitl
  payload jsonb NOT NULL                            -- query, retrieved doc ids, decision, model, latency_ms, citations
);
CREATE INDEX IF NOT EXISTS audit_thread_idx ON audit_events(thread_id, at);
CREATE TABLE IF NOT EXISTS eval_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  suite text NOT NULL,
  case_id text NOT NULL,
  thread_id text,
  scores jsonb NOT NULL,
  passed boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- LangGraph checkpointer creates its own tables (checkpoints, checkpoint_blobs, checkpoint_writes).
