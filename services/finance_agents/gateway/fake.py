"""FakeGateway: deterministic, offline. Answers are built ONLY from the evidence in the prompt.

In production: Bedrock (gateway/bedrock.py) understands, plans, writes and verifies.
In this demo: this stub does all four with rules, because the local stack runs without cloud spend. It reports
itself as "offline-stub" everywhere (GET /config, audit rows, the verifier name) so nobody takes it for a model.

Prompt protocol shared with agent/team.py and bedrock.py: the generate role receives "QUESTION: ...\n\nEVIDENCE:"
followed by numbered lines "[n] <text>" (see `format_evidence`); the verify role receives
"ANSWER:\n...\n\nEVIDENCE:\n[n] ...".
"""

from __future__ import annotations

import json
import re

from ..contracts import ModelRole, ModelUsage, QueryIntent, ToolCall, ToolSpec, UnderstoodQuery

# The Writer's sentinel for "the evidence does not answer the question"; the graph maps it to
# reason no_relevant_evidence and shows that reason's sentence, never this string.
ABSTAIN_TEXT = "I can't answer that from the documents you have access to."

KNOWN_TICKERS = {"AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "IBM", "ORCL", "INTC"}
_COMPANY = re.compile(
    r"\b(apple|microsoft|amazon|google|alphabet|meta|facebook|nvidia|tesla|ibm|oracle|intel)\b", re.IGNORECASE
)
_COMPANY_TICKER = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "ibm": "IBM",
    "oracle": "ORCL",
    "intel": "INTC",
}
_NOT_TICKERS = {"US", "SEC", "EPS", "ACL", "XBRL", "CFO", "CEO"}
# Phrase -> XBRL concept (names from ingest/facts.py CONCEPTS). Order matters: a matched phrase is removed, so
# "earnings per share" is not also read as "earnings" and "net sales" not also as "net income".
_CONCEPTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:\bearn\w*\s+)?(?<!dividend )(?<!dividends )\bper share\b|\beps\b"), "EarningsPerShareDiluted"),
    (re.compile(r"\bnet sales\b|\brevenues?\b|\bsales\b"), "Revenues"),
    (re.compile(r"\bnet income\b|\bprofits?\b|\bearn(?:ings|ed|s)?\b"), "NetIncomeLoss"),
    (re.compile(r"\btotal assets\b"), "Assets"),
    (re.compile(r"\bliabilities\b"), "Liabilities"),
    (re.compile(r"\bequity\b"), "StockholdersEquity"),
    (re.compile(r"\bcash\b"), "CashAndCashEquivalentsAtCarryingValue"),
]
_VALUE_QUESTION = re.compile(
    r"\b(?:what (?:was|were|is|are)|what did .*\b(?:earn|make|report)|how (?:much|many)|percent|margin)\b"
)
_COMPARE = re.compile(r"\b(?:change[sd]?|compar\w+|versus|vs|grow\w*|grew|increase[sd]?|decrease[sd]?)\b")
# What makes free text a request about the filings: a question word or an imperative. out_of_scope is the exception.
_REQUEST = re.compile(
    r"\b(?:what|how|when|who|whom|whose|why|which|where|did|does|do|is|are|was|were|can|could|would|will|has|have|"
    r"compare|summari[sz]e|tell|explain|show|give|describe|list|find|outline|name|identify|provide|report|discuss)\b"
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_TICKER = re.compile(r"\b[A-Za-z]{1,5}\b")
_EVIDENCE_LINE = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)
_CITE = re.compile(r"\s*\[\d+\]")
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_TICKET = re.compile(
    r"^\s*(?:please\s+)?(?:create|open|file)\s+(?:a\s+)?ticket\b[:\s-]*(?P<title>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# The Researcher's structured lines (agent/team.py `fact_line` and its calculator line): always kept, first.
_FACT_LINE = re.compile(r"XBRL concept \w+\)\.$|computed by the calculator tool\.$")
_WORD = re.compile(r"[A-Za-z0-9]+")
_STOP = set(
    "the a an of in on to for and or is are was were be been what which who whom how did does do its it about say "
    "says said from with by that this these those as at has have had their they any such not can could would may "
    "according describe describes tell show list compare summarize summarise explain give find outline identify "
    "provide discuss please you your i me my there many much main major key most fiscal year years quarter".split()
)
# Never content: "hello", "thanks, that is great" and the like are not requests about the filings.
_SMALL_TALK = set("hello hi hey thanks thank thx bye goodbye good morning afternoon evening great cool nice".split())
# Question words that must appear in the sentence as this phrase (a six-letter stem would let "divided" pass for
# "dividend"); also the two abbreviations a filing spells out.
_PHRASES = {
    "cfo": "chief financial officer",
    "ceo": "chief executive officer",
    "dividend": "dividend",
    "dividends": "dividend",
    "director": "director",
    "directors": "director",
}
MAX_SENTENCES = 5


def extract_json(text: str) -> dict:
    """The first {...} block in a model reply, parsed."""
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ValueError("model returned no JSON object")
    return json.loads(m.group(0))


def format_evidence(texts: list[str]) -> str:
    return "\n".join(f"[{i}] {' '.join(t.split())}" for i, t in enumerate(texts, start=1))


# A sentence ends at . ! ? followed by space, except after a name's initial or a title ("Emma N. Walmsley", "Ms.")
# or before a lowercase letter ("Apple Inc. consummated the issuance").
_SENTENCE_END = re.compile(r"(?<=[.!?])(?<!\s[A-Z]\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bDr\.)\s+(?![a-z])")
_LIST_MARKER = re.compile(r"^\([a-z0-9]{1,3}\)\s+")  # "(b) On November 4, ..." reads as "On November 4, ..."


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def _words(text: str) -> list[str]:
    """Lowercase content words: longer than two characters and not a stop word."""
    return [w for w in (m.lower() for m in _WORD.findall(text)) if len(w) > 2 and w not in _STOP]


def _stems(text: str) -> set[str]:
    """Content words, crudely stemmed: plural "s" dropped, first six letters."""
    return {w.rstrip("s")[:6] for w in _words(text)}


# Company names and tickers say whose filing, not what is asked: every sentence of an Apple filing says "Apple".
_ENTITY_WORDS = {w.lower() for w in (*_COMPANY_TICKER, *KNOWN_TICKERS)}


def _terms(question: str) -> set[str]:
    """What a sentence must match: the question's content words without entity words, as a phrase (`_PHRASES`)
    or a stem."""
    # Numbers are never terms: a sentence that merely shares "2024" and "2026" with the question does not answer it.
    return {
        _PHRASES.get(w) or w.rstrip("s")[:6] for w in _words(question) if w not in _ENTITY_WORDS and not w.isdigit()
    }


def _score(terms: set[str], sentence: str) -> int:
    """How many distinct question terms the sentence matches: a phrase by containment, a stem by prefix
    ("stand" matches "standing"; stems under four letters must be equal)."""
    low, stems = sentence.lower(), _stems(sentence)

    def hit(t: str) -> bool:
        if t in _PHRASES.values():
            return t in low
        return any(t == s or (min(len(t), len(s)) >= 4 and (t.startswith(s) or s.startswith(t))) for s in stems)

    return sum(hit(t) for t in terms)


def _candidate(s: str) -> bool:
    """A readable sentence, never table debris: starts with a capital letter, no exhibit-index header or
    checkbox row, and mostly letters."""
    return (
        40 <= len(s) <= 400
        and s[0].isalpha()
        and s[0].isupper()
        # form boxes, exhibit indexes and signature blocks are not prose
        and not any(junk in s for junk in ("Exhibit Number", "☐", "☒", "/s/"))
        and sum(c.isalpha() for c in s) * 2 >= sum(not c.isspace() for c in s)
    )


def _best_sentence(text: str, terms: set[str]) -> tuple[int, str]:
    """(matched question terms, sentence) for the sentence of `text` closest to the question."""
    best = (0, "")
    for s in (_LIST_MARKER.sub("", s) for s in _sentences(text)):
        if _candidate(s) and (score := _score(terms, s)) > best[0]:
            best = (score, s)
    return best


def write_extractive(prompt: str) -> str:
    """The generate role: structured fact lines first, then the most relevant sentence of each evidence item,
    best first, each kept verbatim (so the verify role can find it) and cited by its evidence index. A sentence
    qualifies only when it matches two of the question's terms (one, when the question has only one): a wrong
    sentence is worse than the abstention."""
    question = prompt.partition("QUESTION:")[2].partition("\n")[0]
    terms = _terms(question)
    need = max(min(2, len(terms)), 1)
    facts: list[str] = []
    scored: list[tuple[int, str]] = []
    for i, text in _EVIDENCE_LINE.findall(prompt.partition("EVIDENCE:")[2]):
        if _FACT_LINE.search(text):
            facts.append(f"{text} [{i}]")
        elif (best := _best_sentence(text, terms))[0] >= need:
            scored.append((best[0], f"{best[1]} [{i}]"))
    scored.sort(key=lambda t: -t[0])  # stable: ties keep the reranker's order
    # A sentence sharing two words beside one sharing four is noise: keep those with at least half the best score.
    kept = [s for score, s in scored if score * 2 >= scored[0][0]][:MAX_SENTENCES]
    return " ".join(facts + kept) or ABSTAIN_TEXT


def understand_heuristic(question: str) -> UnderstoodQuery:
    q = question.strip()
    if _TICKET.match(q):
        return UnderstoodQuery(original=question, rewritten=q, intent=QueryIntent.action)
    lower = q.lower()
    entities = {t.upper() for t in _TICKER.findall(q) if t.upper() in KNOWN_TICKERS}
    entities |= {t for t in _TICKER.findall(q) if t.isupper() and len(t) >= 2 and t not in _NOT_TICKERS}
    entities |= {_COMPANY_TICKER[name.lower()] for name in _COMPANY.findall(q)}
    concepts: list[str] = []
    rest = lower
    for pattern, concept in _CONCEPTS:
        if pattern.search(rest):
            concepts.append(concept)
            rest = pattern.sub(" ", rest)
    asks = q.endswith("?") or bool(_REQUEST.search(lower))
    # A bare "aapl revenue 2025" names a company and a figure: a request for that figure.
    keywords = bool(entities and concepts) and not asks
    if len(set(_words(q)) - _SMALL_TALK) < 2 or not (asks or keywords):
        return UnderstoodQuery(original=question, rewritten=q, intent=QueryIntent.out_of_scope)
    years = list(dict.fromkeys(_YEAR.findall(q)))
    if concepts and (len(years) >= 2 or _COMPARE.search(lower)):
        intent = QueryIntent.compare
    elif concepts and (keywords or _VALUE_QUESTION.search(lower)):
        intent = QueryIntent.numeric
    else:
        intent = QueryIntent.lookup
    return UnderstoodQuery(
        original=question,
        rewritten=q.rstrip("?"),
        intent=intent,
        entities=sorted(entities),
        concepts=concepts,
        period_hint=",".join(years) or None,
    )


def plan_heuristic(question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
    """What a model would propose: facts for numeric intents, a ticket on request, else search. Never a
    tool outside `tools`; the harness (Cedar in run_tool) still decides whether a call may run."""
    offered = {t.name for t in tools}
    if (m := _TICKET.match(question)) and "create_ticket" in offered:
        title = re.sub(r"^(?:to|for)\s+", "", m.group("title").strip(), flags=re.IGNORECASE)
        return [ToolCall(tool="create_ticket", arguments={"title": title[:200]})]
    calls: list[ToolCall] = []
    if understood.intent in (QueryIntent.numeric, QueryIntent.compare) and "facts_sql" in offered:
        calls += [
            ToolCall(tool="facts_sql", arguments={"entity": e, "concept": c})
            for e in understood.entities
            for c in understood.concepts
        ]
    if understood.intent not in (QueryIntent.out_of_scope, QueryIntent.action) and "search_documents" in offered:
        calls.append(
            ToolCall(
                tool="search_documents",
                arguments={
                    "query": understood.original,
                    "rewritten": understood.rewritten,
                    "intent": understood.intent.value,
                },
            )
        )
    return calls


class FakeGateway:
    model_name = "offline-stub"  # HR_MODEL_BACKEND=fake selects it; the name says what it is

    async def understand(self, question: str) -> UnderstoodQuery:
        return understand_heuristic(question)

    async def plan(self, question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
        return plan_heuristic(question, understood, tools)

    async def complete(
        self,
        role: ModelRole,
        system: str,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> tuple[str, ModelUsage]:
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        usage = ModelUsage(input_tokens=len(prompt) // 4, model=self.model_name)
        if role == ModelRole.verify:
            answer_part, _, evidence_part = prompt.partition("EVIDENCE:")
            answer = answer_part.replace("ANSWER:", "").strip()
            evidence = " ".join(" ".join(t.split()) for _, t in _EVIDENCE_LINE.findall(evidence_part))
            unsupported = [
                s for s in _sentences(_CITE.sub("", answer)) if s and s not in evidence and s != ABSTAIN_TEXT
            ]
            out = json.dumps(
                {
                    "grounded": not unsupported,
                    "confidence": 0.0 if unsupported else 0.9,
                    "unsupported_claims": unsupported,
                }
            )
        else:
            out = write_extractive(prompt)
        usage.output_tokens = len(out) // 4
        return out, usage
