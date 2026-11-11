"""Filing bytes -> [(section, text)] -> chunks. Pure functions, no IO.

In production: PDF via PyMuPDF or Textract.
In this demo: HTML only, because EDGAR primary documents are HTML.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment, Declaration, ProcessingInstruction

Section = tuple[str, str]
_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_WS = re.compile(r"\s+")


def parse_html(data: bytes) -> list[Section]:
    soup = BeautifulSoup(data, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):  # ix:header = hidden inline-XBRL block
        tag.decompose()
    # In production: a layout-aware splitter (bold/centered paragraph headings, Item N. patterns) names sections.
    # In this demo: only h1-h6 mark sections and the rest is 'preamble', because citations still carry
    # document + chunk and most EDGAR HTML has no h-tags to exploit.
    out: list[Section] = []
    section = "preamble"
    buf: list[str] = []
    for s in soup.find_all(string=True):
        if isinstance(s, (Comment, Declaration, ProcessingInstruction)):  # <?xml ...?> and <!-- --> are not prose
            continue
        text = _WS.sub(" ", s).strip()
        if not text:
            continue
        if s.find_parent(_HEADINGS) is not None:
            if buf:
                out.append((section, " ".join(buf)))
            section, buf = text, []
        else:
            buf.append(text)
    if buf:
        out.append((section, " ".join(buf)))
    return out


def parse(data: bytes, content_type: str) -> list[Section]:
    return parse_html(data)


def _sentences(text: str, size: int) -> list[str]:
    """Sentences, each at most `size` words (a longer one is hard-split by words)."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", text.strip()):
        words = part.split()
        while len(words) > size:
            out.append(" ".join(words[:size]))
            words = words[size:]
        if words:
            out.append(" ".join(words))
    return out


def chunk(sections: list[Section], size: int = 700, overlap: int = 80) -> list[Section]:
    """~`size`-word chunks per section with `overlap` trailing words carried into the next one.
    Boundaries fall on sentence ends unless a single sentence exceeds `size`."""
    out: list[Section] = []
    for section, text in sections:
        cur: list[str] = []
        words = 0
        fresh = False  # cur holds something beyond the carried-over tail
        for sent in _sentences(text, size):
            cur.append(sent)
            words += len(sent.split())
            fresh = True
            if words < size:
                continue
            out.append((section, " ".join(cur)))
            tail: list[str] = []
            kept = 0
            for prev in reversed(cur):
                n = len(prev.split())
                if kept + n > overlap:
                    break
                tail.insert(0, prev)
                kept += n
            cur, words, fresh = tail, kept, False
        if cur and fresh:
            out.append((section, " ".join(cur)))
    return out
