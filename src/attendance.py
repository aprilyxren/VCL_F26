"""Find court attendance lists (the names after ``PRESENT``).

A meeting entry opens with a heading and a ``PRESENT`` list of names before
the descriptive text begins::

    PRESENT.
    m’ Deputy Ferrar. m'’ Bromefeild. m’ Bartham.
    m’ Brooke. m’ Riseley. m’ Combes.

    The Court haveinge made request vnto m’ Chamberlyne ...

The list runs while lines read as names: title markers (m', S', Lo:, Capt.,
kn'), capitalized words, and short connectors. It allows single blank lines
and can continue onto the next page; it ends at the first prose line (mostly
lowercase words) or a margin note. People inside a block are *present*;
people elsewhere are *mentioned*.
"""

from __future__ import annotations

import re


PRESENT_RE = re.compile(r"(?m)^[^\n]{0,40}\b(?:PRESENT|Present)\b\W*$")
RECORDS_HEAD_RE = re.compile(r"(?i)RECORDS\s+OF\s+T[HI]E\s+VIRGINIA|^\s*[A-Z]+\s+\d{1,2},?\s+1[56]\d\d")
# Lowercase words that still belong in a name list.
LIST_WORDS = {
    "of", "the", "and", "kn", "knt", "knight", "knights", "esq", "esquire", "with", "others", "diuers",
    "divers", "dive", "more", "some", "of", "y", "ye", "lord", "lo", "earle", "vic", "visc", "viscount",
    "deputy", "deputie", "treasuror", "treasurer", "secretary", "secretarie", "auditor", "auditors",
    "alderman", "ald", "doctor", "capt", "captaine", "sir", "mr", "m", "right", "honorable", "hono",
    "honourable", "rolles", "wards", "excheq", "deane", "privie", "priuy", "seale", "keeper",
}
MAX_BLOCK_LINES = 80


def is_list_line(line: str) -> bool:
    """True for a line of names (``m’ Brooke. m’ Riseley.``), false for prose."""
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", line)
    if not words:
        return True  # punctuation or a bare folio number like [105]
    content = [w for w in words if len(w) >= 3]
    if not content:
        return True
    prose = [w for w in content if w[0].islower() and w.casefold() not in LIST_WORDS]
    return len(prose) / len(content) < 0.35 and len(prose) < 4


def blocks_on_page(text: str, start_offset: int = 0) -> tuple[list[tuple[int, int]], bool]:
    """Return attendance blocks starting at ``PRESENT`` lines (or, when
    ``start_offset`` is 0 and called for a continuation, at the page top), and
    whether the last block ran to the end of the page."""
    blocks: list[tuple[int, int]] = []
    open_at_end = False
    for match in PRESENT_RE.finditer(text, start_offset):
        end, open_at_end = scan_list(text, match.end())
        blocks.append((match.start(), end))
    return blocks, open_at_end


def scan_list(text: str, position: int) -> tuple[int, bool]:
    """Extend a list from ``position``; return (end offset, reached page end)."""
    end = position
    blank_run = 0
    lines = 0
    for line_match in re.finditer(r"[^\n]*\n?", text[position:]):
        line = line_match.group(0)
        if not line:
            break
        lines += 1
        if lines > MAX_BLOCK_LINES:
            return end, False
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                return end, False
            continue
        if not is_list_line(line):
            return end, False
        blank_run = 0
        end = position + line_match.end()
    return end, True


def attendance_blocks(pages: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Map page_id -> attendance spans, following lists onto the next page."""
    result: dict[str, list[tuple[int, int]]] = {}
    carry = False
    previous_volume = None
    for page in pages:
        text = page["text"]
        spans: list[tuple[int, int]] = []
        if carry and page["volume"] == previous_volume:
            # Skip the running head, then continue the list if it still reads as names.
            first_break = text.find("\n")
            head_end = first_break + 1 if first_break >= 0 and RECORDS_HEAD_RE.search(text[:first_break]) else 0
            body = text[head_end:]
            first_line = next((l for l in body.split("\n") if l.strip()), "")
            if first_line and is_list_line(first_line):
                end, still_open = scan_list(text, head_end)
                if end > head_end:
                    spans.append((head_end, end))
                carry = still_open
            else:
                carry = False
        blocks, open_at_end = blocks_on_page(text)
        spans.extend(blocks)
        if blocks:
            carry = open_at_end
        result[page["page_id"]] = spans
        previous_volume = page["volume"]
    return result


def in_blocks(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s <= start and end <= e for s, e in spans)
