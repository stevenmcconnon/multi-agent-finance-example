# Architecture decision record (ADR) 0004: U.S. Securities and Exchange Commission (SEC) Electronic Data Gathering, Analysis, and Retrieval system (EDGAR) filings with simulated item-level access control lists (ACLs) as the corpus

Status: accepted · 2026-11

## Context

The system is built for a SharePoint library where every item carries its own permissions. A public sample
cannot ship a real tenant's documents or their Microsoft Graph ACLs, yet the permission behaviour must be
demonstrable with real text, structured numbers and versions.

## Decision

The corpus is SEC EDGAR: Form 10-K (annual report), Form 10-Q (quarterly report) and Form 8-K (current report) primary documents and the eXtensible Business Reporting Language (XBRL) `companyfacts` numbers for the
same accession. Item-level ACLs are assigned at ingest by form (`ingest/acl.py`: 10-K/10-Q readable by
finance and analysts, 8-K by engineering too), which gives the three seeded identities visibly different
views. `documents.source` is `edgar`, `source_id` the accession number, `version` the same (an amendment
is a new version). The connector sends the `User-Agent` the SEC requires (`EDGAR_USER_AGENT`, app name plus
contact e-mail) and keeps under ten requests per second.

    In production: SharePoint via Microsoft Graph, items and their permission principals, delta queries
    for change.
    In this demo: EDGAR public filings with ACLs the demo assigns, because the data is public, free,
    versioned, and has real tables.

## Alternatives

- **Synthetic documents.** Fully controlled, but no real parsing problems, no real numbers, and reviewers
  recognise filler.
- **Wikipedia or a public quality assurance (QA) dataset.** Real text, but no natural notion of restricted items or versions,
  and gold answers would tempt the evals into exact-match grading.
- **A real SharePoint tenant.** Not shippable in a public repository.

## Consequences

- Eval datasets carry no gold answers (`evals/datasets/qa.jsonl`); the suites assert citations of the
  right form from readable documents, and the graded evals use faithfulness and relevancy, not exact text.
- The ACL mapping is the demo's; nothing in retrieval or policy depends on it, so swapping the connector
  changes `ingest/`, not `retrieval/` or `authz/`.
- Section detection is shallow on EDGAR's HyperText Markup Language (HTML) (most has no heading tags); citations still carry document
  and chunk, and a layout-aware splitter is a named shortcut in `ingest/parse.py`.
