# Architecture decision record (ADR) 0003: Temporal owns ingestion durability; no Simple Queue Service (SQS) in the path

Status: accepted · 2026-11

## Context

Ingesting a filing is a multi-step job (discover, claim, fetch, parse and write, finish) against an external
application programming interface (API) with rate limits and occasional failures. Each filing version must run at most once to completion,
failures must retry with backoff, and a filing that keeps failing must land somewhere a person can see.

## Decision

Temporal workflows (`ingest/workflow.py`) own retries, backoff, timeouts and at-most-once start:
`DiscoverWorkflow` starts one `IngestFilingWorkflow` per filing with the workflow id equal to the job's
idempotency key, so Temporal itself refuses a duplicate. Activities are plain async functions with a
`RetryPolicy` of five attempts and exponential backoff. The dead letter is a row: `ingest_jobs.status='dead'`
after the fifth failure (`ingest/jobs.py`), queryable with the rest of the control plane. There is no SQS
queue and no queue module; the `hr_queue_backend` / `sqs_*` settings named in early drafts were removed.
The same activities run inline without Temporal (`cli run --inline`) for the local quickstart.

    In production: Temporal on Fargate or Temporal Cloud, workers as an Amazon Elastic Container Service (ECS) service, EventBridge
    Scheduler starting the discover run.
    In this demo: `temporal server start-dev` in compose, because the sample needs no fleet.

## Alternatives

- **SQS + a worker loop + a DLQ.** Simpler infrastructure, no server to run, and it is enough when each
  message is one short idempotent step. It would be the right choice if ingestion were a single
  "fetch-and-write" step per document with no ordering, no fan-out and no need to inspect a stuck job's
  history; the dead-letter queue (DLQ) then *is* the dead letter.
- **Step Functions.** Native on Amazon Web Services (AWS), but a state machine per filing is heavier to author than a workflow
  function, and there is no local server for a $0 stack.
- **Celery / Redis Queue (RQ) over Redis.** Retries without history or deterministic replay; Redis is the acceleration
  store here, not a durability store.

## Consequences

- Multi-step state (which activity failed, with what input) is in Temporal's history and the user interface (UI) (:8233).
- One more server to run locally and on Fargate; the `temporal` service is part of the cost sheet.
- The `ingest_jobs` row stays the single place a human looks for "what is dead", whether the job ran under
  Temporal or inline.
