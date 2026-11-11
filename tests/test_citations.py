"""The answer a reader sees: citation markers count from 1 and each source shows the sentence that cites it."""

from uuid import uuid4

from finance_agents.agent.graph import number_citations
from finance_agents.contracts import Citation


def _ev(text: str) -> tuple[str, Citation]:
    return text, Citation(document_id=uuid4(), uri="https://example.test/doc", snippet=text[:20])


def test_markers_are_renumbered_in_order_of_use_and_snippets_quote_the_cited_sentence():
    evidence = [_ev(f"Filler passage {i}.") for i in range(6)]
    evidence.append(_ev("Unrelated opening. Changes to the supply chain involve significant risks. More text."))
    evidence.append(_ev("Trade restrictions can be expensive. Another sentence."))
    draft = "Changes to the supply chain involve significant risks. [7] Trade restrictions can be expensive. [8]"
    text, cites = number_citations(draft, evidence)
    assert text == "Changes to the supply chain involve significant risks. [1] Trade restrictions can be expensive. [2]"
    assert [c.snippet for c in cites] == [
        "Changes to the supply chain involve significant risks.",
        "Trade restrictions can be expensive.",
    ]


def test_a_paraphrase_keeps_the_source_snippet_and_unknown_markers_are_dropped():
    evidence = [_ev("Revenue grew because of services."), _ev("Costs fell.")]
    text, cites = number_citations("Services drove growth [2][9] and costs declined [1].", evidence)
    assert text == "Services drove growth [1] and costs declined [2]."
    assert [c.snippet for c in cites] == ["Costs fell."[:20], "Revenue grew because"[:20]]


def test_no_markers_cites_all_the_evidence_unchanged():
    evidence = [_ev("One."), _ev("Two.")]
    text, cites = number_citations("An answer with no markers.", evidence)
    assert text == "An answer with no markers." and len(cites) == 2
