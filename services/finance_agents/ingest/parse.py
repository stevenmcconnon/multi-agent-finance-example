"""Filing bytes -> [(section, text)] -> chunks. Pure functions, no IO.

In production: PDF via PyMuPDF or Textract.
In this demo: HTML only, because EDGAR primary documents are HTML.
"""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, Comment, Declaration, ProcessingInstruction, XMLParsedAsHTMLWarning

Section = tuple[str, str]
_HTAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_BLOCKS = ["p", "div", "td", "th", "li", *_HTAGS]
_WS = re.compile(r"\s+")
_PART = re.compile(r"PART\s+([IVX]+)\.?", re.I)
# "Item 1A. Risk Factors" (10-K: the period is required, a running page header "Item 1A" has none) or
# "Item 5.02 Departure of ..." (8-K). Group 1 = the item number.
_ITEM = re.compile(r"Item\s+(\d+\.\d+|\d+[A-C]?(?=\.))\.?\s*", re.I)
_HEADING_MAX = 200  # the longest 8-K item title (5.02) runs ~160 characters; body paragraphs are longer
COVER = "Cover page"


def parse_html(data: bytes) -> list[Section]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)  # inline-XBRL filings open with <?xml ?>
        soup = BeautifulSoup(data, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):  # ix:header = hidden inline-XBRL block
        tag.decompose()
    # In production: a layout-aware parser (font weight, anchors, PDF outlines) also names sub-sections
    #   ("Note 4, Investments") and qualifies 10-Q items by their Part.
    # In this demo: only the SEC item lines ("Item 1A. Risk Factors") and h1-h6 name sections, because
    #   10-K and 8-K are the only forms ingested and the item is what a reader cites.
    # Pass 1: every text node becomes (key, text); key is None for prose, else the heading's identity.
    nodes: list[tuple[tuple[str, str] | None, str]] = []
    part = ""
    done = None  # the block whose text was already taken whole as a heading
    for s in soup.find_all(string=True):
        if isinstance(s, (Comment, Declaration, ProcessingInstruction)):  # <?xml ...?> and <!-- --> are not prose
            continue
        text = _WS.sub(" ", s).strip()
        if not text:
            continue
        if done is not None:
            if done in s.parents:
                continue
            done = None
        if s.find_parent(_HTAGS) is not None:
            nodes.append((("h", text), text))
            continue
        if not (_ITEM.match(text) or _PART.match(text)):
            last_key, last_text = nodes[-1] if nodes else (None, "")
            if last_key and last_key[0] != "h" and ". " not in last_text and len(text) < _HEADING_MAX:
                # a bare "Item 8.01" whose title sits in the next cell or div
                nodes[-1] = (last_key, f"{last_text}. {text.rstrip('. ')}")
            else:
                nodes.append((None, text))
            continue
        # A heading stands alone: its whole block is short. One styled heading is often several text
        # nodes ("ITEM 1A. RIS" + "K FACTORS"), so read the block, not the node.
        block = s.find_parent(_BLOCKS)
        full = _WS.sub(" ", block.get_text()).strip() if block is not None else text
        item = _ITEM.match(full)
        if len(full) >= _HEADING_MAX or not (item or _PART.fullmatch(full)):
            nodes.append((None, text))  # a cross-reference inside a paragraph
            continue
        done = block
        if item is None:  # "PART II": remembered so Part I Item 1 and Part II Item 1 stay distinct, never a section
            part = full.split()[1].rstrip(".").upper()
            continue
        number, title = item.group(1).upper(), full[item.end() :].rstrip(". ")
        nodes.append(((part, number), f"Item {number}. {title}" if title else f"Item {number}"))
    # Pass 2: the table of contents lists every heading first, so only a heading's LAST occurrence opens a
    # section; the earlier ones stay prose in the section they sit in (the cover page).
    last = {key: i for i, (key, _) in enumerate(nodes) if key}
    out: list[Section] = []
    section = COVER
    buf: list[str] = []
    for i, (key, text) in enumerate(nodes):
        if key and last[key] == i:
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
