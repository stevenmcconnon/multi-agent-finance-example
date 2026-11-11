"""Citation quality: evals/datasets/qa.jsonl through the real turn graph.

There are no gold answers (ADR 0004: the corpus is live EDGAR text). What is asserted instead:
an `answer` case produces a non-abstained, verifier-grounded answer with at least one citation, every citation
names a document the caller may read, and at least one cites the form the case requires (`must_cite_form`);
an `abstain` case produces the abstain text with no citations.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("finance_agents.agent.graph")
from corpus import DOCS_BY_ID, USERS, load_cases, may_read
from finance_agents.agent import graph
from finance_agents.gateway import ABSTAIN_TEXT

CASES = load_cases("qa.jsonl")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_answers_cite_readable_documents_of_the_right_form(case, audit_sink):
    principal = USERS[case["user"]]
    answer = await graph.run_turn(principal, case["question"], f"qa-{case['id']}-{uuid.uuid4()}")

    if case["expect"] == "abstain":
        assert answer.abstained is True and answer.citations == []
        assert answer.text == ABSTAIN_TEXT
        return

    assert answer.abstained is False, f"abstained: {answer.text}"
    assert answer.citations, "a non-abstained answer must carry at least one citation"
    assert answer.verification is not None and answer.verification.grounded is True
    forms = set()
    for c in answer.citations:
        doc = DOCS_BY_ID.get(c.document_id)
        assert doc is not None, f"citation to a document outside the corpus: {c.document_id}"
        assert may_read(principal, doc), f"{principal.user_id} cited {doc['title']} which they may not read"
        forms.add(doc["form"])
    assert case["must_cite_form"] in forms, f"expected a {case['must_cite_form']} citation, got {sorted(forms)}"

    # the `answer` audit row lists exactly the cited documents
    (row,) = [r for r in audit_sink if r["kind"] == "answer"]
    assert {c["document_id"] for c in row["payload"]["citations"]} == {str(c.document_id) for c in answer.citations}


async def test_every_citation_in_the_answer_text_resolves(audit_sink):
    """[n] markers in the draft map onto the evidence list; the graph keeps only cited evidence as citations."""
    answer = await graph.run_turn(
        USERS["alice"], "What risk factors does MSFT disclose in its 10-K?", f"cite-{uuid.uuid4()}"
    )
    assert not answer.abstained
    assert all(c.chunk_id is not None and c.snippet for c in answer.citations)
