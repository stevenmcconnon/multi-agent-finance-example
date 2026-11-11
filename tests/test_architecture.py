"""The harness rule as a test: vendor SDKs live in their doors, the agent never touches Postgres directly,
and every candidate query over chunks or facts carries the ACL predicate. Pure AST / string checks."""

from __future__ import annotations

import ast
from pathlib import Path

from harness_rag.retrieval import sql

PKG = Path(__file__).resolve().parents[1] / "services" / "harness_rag"
BOTO3_ALLOWED = {
    "gateway/bedrock.py",
    "retrieval/embed.py",
    "retrieval/rerank.py",
    "authz/verified_permissions.py",
}


def _imports(path: Path) -> set[str]:
    """Top-level module names imported anywhere in the file (including inside functions)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def test_boto3_only_behind_the_doors():
    # ingest/ may reach S3/SQS directly (its queue and raw-store doors); every other AWS call has a named file.
    offenders = {str(p.relative_to(PKG)) for p in _files() if "boto3" in _imports(p)} - BOTO3_ALLOWED
    offenders = {o for o in offenders if not o.startswith("ingest/")}
    assert offenders == set(), f"boto3 imported outside its doors: {offenders}"


def test_agent_never_imports_psycopg():
    for p in (PKG / "agent").glob("*.py"):
        assert not ({"psycopg", "psycopg_pool"} & _imports(p)), f"{p.name} talks to Postgres directly"


def test_every_chunk_or_fact_query_carries_the_acl_predicate():
    queries = {
        name: val
        for name, val in vars(sql).items()
        if name.endswith("_SQL") and isinstance(val, str) and ("FROM chunks" in val or "FROM facts" in val)
    }
    assert {"DENSE_SQL", "SPARSE_SQL", "FACTS_SQL"} <= set(queries)
    for name, q in queries.items():
        assert sql.ACL_PREDICATE in q, f"{name} lacks the ACL predicate"
        assert q.index(sql.ACL_PREDICATE) < q.index("LIMIT"), f"{name} filters after LIMIT"


def test_documents_route_reuses_the_predicate():
    from harness_rag.api import app as api

    assert sql.ACL_PREDICATE in api.DOCUMENTS_SQL and "d.deleted_at IS NULL" in api.DOCUMENTS_SQL
