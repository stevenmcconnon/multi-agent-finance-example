"""One filing -> documents + document_acl + chunks (embedded) + facts, in ONE transaction.

Same content_hash already stored for (org, source, source_id) -> nothing is written. A changed hash
re-upserts the document row (ON CONFLICT (org, source, source_id, version)) and replaces its children.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog
from pgvector import Vector

from .. import db
from ..authz import audit
from ..retrieval.embed import get_embedder
from . import acl

log = structlog.get_logger(__name__)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def already_stored(msg: dict[str, Any], digest: str) -> Any | None:
    rows = await db.fetch(
        "SELECT id FROM documents WHERE org_id = %s AND source = %s AND source_id = %s AND content_hash = %s "
        "AND deleted_at IS NULL",
        (str(msg["org_id"]), msg["source"], msg["source_id"], digest),
    )
    return rows[0]["id"] if rows else None


async def write(
    msg: dict[str, Any],
    data: bytes,
    mime_type: str,
    chunks: list[tuple[str, str]],
    fact_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Returns {document_id, chunks, facts, skipped}. Embeds before the transaction opens (slow, no DB)."""
    digest = content_hash(data)
    if (doc_id := await already_stored(msg, digest)) is not None:
        log.info("ingest.unchanged", source_id=msg["source_id"], document_id=str(doc_id))
        return {"document_id": str(doc_id), "chunks": 0, "facts": 0, "skipped": True}

    embedder = get_embedder()
    vectors = await embedder.embed([text for _, text in chunks])
    readers = await acl.principal_ids(msg["org_id"], msg["form"])
    metadata = {k: msg.get(k) for k in ("cik", "ticker", "form", "filed_at", "report_date", "primary_doc")}

    p = await db.pool()
    async with p.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO documents (org_id, source, source_id, version, title, uri, content_hash, mime_type, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "ON CONFLICT (org_id, source, source_id, version) DO UPDATE SET title = EXCLUDED.title, "
            "uri = EXCLUDED.uri, content_hash = EXCLUDED.content_hash, mime_type = EXCLUDED.mime_type, "
            "metadata = EXCLUDED.metadata, ingested_at = now(), deleted_at = NULL RETURNING id",
            (
                str(msg["org_id"]),
                msg["source"],
                msg["source_id"],
                msg["version"],
                msg.get("title"),
                msg["uri"],
                digest,
                mime_type,
                json.dumps(metadata),
            ),
        )
        row = await cur.fetchone()
        assert row is not None  # INSERT ... RETURNING always yields one row
        doc_id = row["id"]
        for table in ("facts", "chunks", "document_acl"):  # facts reference chunks: delete children first
            await conn.execute(f"DELETE FROM {table} WHERE document_id = %s", (doc_id,))
        async with conn.cursor() as c:
            await c.executemany(
                "INSERT INTO document_acl (document_id, principal_id) VALUES (%s, %s)",
                [(doc_id, pid) for pid in readers],
            )
            await c.executemany(
                "INSERT INTO chunks (document_id, org_id, ordinal, section, content, embedding, embedding_model) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (doc_id, str(msg["org_id"]), i, section, text, Vector(vec), embedder.model_name)
                    for i, ((section, text), vec) in enumerate(zip(chunks, vectors, strict=True))
                ],
            )
            await c.executemany(
                "INSERT INTO facts (document_id, org_id, entity, concept, period_start, period_end, value, unit, "
                "provenance) VALUES (%s, %s, %s, %s, %s::date, %s::date, %s, %s, %s::jsonb)",
                [
                    (
                        doc_id,
                        str(msg["org_id"]),
                        f["entity"],
                        f["concept"],
                        f["period_start"],
                        f["period_end"],
                        f["value"],
                        f["unit"],
                        json.dumps(f["provenance"]),
                    )
                    for f in fact_rows
                ],
            )
    result = {"document_id": str(doc_id), "chunks": len(chunks), "facts": len(fact_rows), "skipped": False}
    await audit.record(
        "ingest",
        None,
        {
            **result,
            "org_id": str(msg["org_id"]),
            "source": msg["source"],
            "source_id": msg["source_id"],
            "version": msg["version"],
            "form": msg["form"],
            "readers": len(readers),
            "content_hash": digest,
        },
    )
    log.info("ingest.written", **result)
    return result
