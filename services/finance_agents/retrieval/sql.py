"""THE ACL predicate. One owner; the retriever and the fact store both splice it in.

SECURITY INVARIANT: every candidate query (dense, sparse, facts) carries this predicate BEFORE
fusion and rerank, so an unauthorized chunk never enters a ranked list. `c` is the chunks (or facts)
alias, `d` the documents alias. Parameters: org (uuid str), user (external_id), groups (text[]).
Tests assert the literal text appears in both candidate queries.
"""

from __future__ import annotations

from ..contracts import Principal

ACL_PREDICATE = (
    "EXISTS (SELECT 1 FROM document_acl a JOIN principals p ON p.id = a.principal_id "
    "WHERE a.document_id = c.document_id AND p.org_id = %(org)s AND "
    "((p.kind = 'user' AND p.external_id = %(user)s) OR (p.kind = 'group' AND p.external_id = ANY(%(groups)s))))"
)

SCOPE_PREDICATE = "c.org_id = %(org)s AND d.deleted_at IS NULL AND " + ACL_PREDICATE


def acl_params(principal: Principal) -> dict[str, object]:
    return {"org": str(principal.org_id), "user": principal.user_id, "groups": list(principal.groups)}


_CHUNK_COLUMNS = "c.id, c.document_id, c.org_id, c.ordinal, c.section, c.content, c.metadata, d.uri, d.title, d.version"

DENSE_SQL = f"""
SELECT {_CHUNK_COLUMNS}, (c.embedding <=> %(vec)s::vector) AS distance
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.embedding IS NOT NULL AND c.embedding_model = %(model)s AND {SCOPE_PREDICATE}
ORDER BY c.embedding <=> %(vec)s::vector
LIMIT %(k)s
"""

SPARSE_SQL = f"""
SELECT {_CHUNK_COLUMNS},
       ts_rank_cd(c.content_tsv, websearch_to_tsquery('english', %(q)s)) AS rank
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.content_tsv @@ websearch_to_tsquery('english', %(q)s) AND {SCOPE_PREDICATE}
ORDER BY rank DESC
LIMIT %(k)s
"""

FACTS_SQL = f"""
SELECT c.id, c.document_id, c.entity, c.concept, c.period_start, c.period_end, c.value, c.unit, c.provenance,
       d.uri AS document_uri, d.title AS document_title, d.version AS document_version
FROM facts c
JOIN documents d ON d.id = c.document_id
WHERE c.entity = %(entity)s AND c.concept = ANY(%(concepts)s)
  AND (%(period_end)s::date IS NULL OR c.period_end = %(period_end)s::date)
  AND {SCOPE_PREDICATE}
ORDER BY c.period_end DESC NULLS LAST
LIMIT %(k)s
"""
