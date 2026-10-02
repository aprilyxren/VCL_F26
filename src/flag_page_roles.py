"""Flag back-of-book index pages and editorial lines inside the primary text.

The primary text still contains the edition's indexes (``Southampton
Hundred, I, 535.``) and per-document editorial apparatus (``Document in
Library of Congress, Washington, D. C.``). Names there sit next to unrelated
names and places, so they must not produce person-place associations. This
script annotates rather than deletes: the source text is unchanged.

* ``page_roles.csv``: ``index`` for the trailing run of index-shaped pages in
  each volume, otherwise ``record``.
* ``editorial_spans.csv``: character spans of running heads and editorial
  metadata lines on record pages.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from source_loader import load_primary_pages


# A line ending in page references: ``Abbott, Morris, I, 225, 229.``
INDEX_ENTRY_RE = re.compile(
    r"[,.]\s*(?:[IV]{1,3}[,.]\s*)?\d{1,3}(?:\s*[-–]\s*\d{1,3})?"
    r"(?:\s*[,;]\s*(?:[IV]{1,3}[,.]\s*)?\d{1,3}(?:\s*[-–]\s*\d{1,3})?)*\s*[.;,]?\s*$"
)
INDEX_RATIO = 0.3
EDITORIAL_LINE_RE = re.compile(
    r"(?im)^[^\n]*(?:Document\s+in\s+(?:the\s+)?(?:Library|Public|British|Virginia|Ferrar|Manchester)"
    r"|Manuscript\s+Records\s+Virginia\s+Company|List\s+of\s+Records,?\s+No"
    r"|Washington,?\s+D\.?\s*C|Autograph\s+(?:Letter|Signatures?)|Colonial\s+Office|Ferrar\s+Papers"
    r"|Manchester\s+Papers|Magdalene\s+College|Manu\w{3,8}\s+Records|Public\s+Record\s+Office"
    r"|British\s+Museum|Privy\s+Council\s+Register|Patent\s+Roll|Bodleian|Lambeth"
    r"|RECORDS\s+OF\s+T[HI]E\s+VIRGINIA\s+COMPANY)[^\n]*$"
)
RECORDS_HEAD_RE = re.compile(r"(?i)RECORDS\s+OF\s+T[HI]E\s+VIRGINIA")


def index_ratio(text: str) -> float:
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return 0.0
    return sum(1 for line in lines if INDEX_ENTRY_RE.search(line)) / len(lines)


def page_roles(pages: list[dict]) -> dict[str, str]:
    """Mark each volume's trailing run of index pages (blank pages included)."""
    roles = {page["page_id"]: "record" for page in pages}
    for volume in sorted({page["volume"] for page in pages}):
        volume_pages = [page for page in pages if page["volume"] == volume]
        run: list[str] = []
        for page in reversed(volume_pages):
            text = page["text"]
            if not text.strip() or index_ratio(text) >= INDEX_RATIO:
                run.append(page["page_id"])
                continue
            break
        # Require real index content, not just trailing blank pages.
        if any(index_ratio(next(p["text"] for p in volume_pages if p["page_id"] == pid)) >= INDEX_RATIO
               for pid in run):
            for page_id in run:
                roles[page_id] = "index"
    return roles


def editorial_spans(page: dict) -> list[tuple[int, int, str]]:
    text = page["text"]
    spans = []
    first_end = text.find("\n")
    first_end = len(text) if first_end < 0 else first_end
    leading = len(text) - len(text.lstrip())
    if text.strip():
        spans.append((leading, first_end, "running_head"))
    for match in EDITORIAL_LINE_RE.finditer(text):
        if match.start() >= first_end:
            spans.append((match.start(), match.end(), "editorial_metadata"))
    return spans


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--roles-output", type=Path, required=True)
    parser.add_argument("--spans-output", type=Path, required=True)
    args = parser.parse_args()
    pages = load_primary_pages(args.source)
    roles = page_roles(pages)
    with args.roles_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["page_id", "volume", "role"])
        for page in pages:
            writer.writerow([page["page_id"], page["volume"], roles[page["page_id"]]])
    span_count = 0
    with args.spans_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["page_id", "char_start", "char_end", "kind", "text"])
        for page in pages:
            if roles[page["page_id"]] != "record":
                continue
            for start, end, kind in editorial_spans(page):
                writer.writerow([page["page_id"], start, end, kind, " ".join(page["text"][start:end].split())[:160]])
                span_count += 1
    index_pages = sum(1 for role in roles.values() if role == "index")
    print(f"Flagged {index_pages} index pages; wrote {span_count} editorial spans")


if __name__ == "__main__":
    main()
