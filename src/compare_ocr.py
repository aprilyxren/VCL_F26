"""Compare a volume's current OCR with a layout-aware re-OCR, page by page.

Pages are matched by printed page number (the raw files are not always in
printed order). For each matched page it reports:

* whether the running head yields a date (``date_pages.parse_dates``);
* how many person/place authority forms are found (the NER's own matcher),
  counting the re-OCR's body and margin notes together so both sides cover
  the same printed text;
* how many of the re-OCR's margin notes appear glued into the current text;
* the re-OCR's mean word confidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter
from pathlib import Path

import person_dictionary
from build_ner import AuthorityMatcher, build_entries
from date_pages import VOLUME_YEARS, parse_dates


def printed_number(first_line: str) -> str | None:
    match = re.search(r"(\d{1,3})\s*[|.]?\s*$", first_line) or re.match(r"^\s*(\d{1,3})\s", first_line)
    return match.group(1) if match else None


def current_pages(raw_path: Path) -> dict[str, str]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    pages = {}
    for text in raw.values():
        first = next((line.strip() for line in text.split("\n") if line.strip()), "")
        number = printed_number(first)
        if number and number not in pages:
            pages[number] = text
    return pages


def name_hits(matcher: AuthorityMatcher, text: str) -> Counter:
    return Counter(label for _, _, label, _ in matcher.find(text) if label in {"PERSON", "PLACE"})


def glued(note: str, current: str) -> bool:
    """A margin note is glued in when its opening words share a line with other text."""
    words = re.findall(r"[A-Za-z]{3,}", note)[:2]
    if len(words) < 2:
        return False
    for line in current.split("\n"):
        if all(w in line for w in words) and len(re.findall(r"[A-Za-z]{3,}", line)) > len(re.findall(r"[A-Za-z]{3,}", note)) + 3:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--reocr", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--place-resolutions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--volume", type=int, default=1, help="Selects the year range for running-head dates.")
    args = parser.parse_args()
    years = VOLUME_YEARS.get(args.volume, (1580, 1630))

    entries, _ = build_entries(person_dictionary.load(args.dictionary), args.authority, args.place_resolutions)
    matcher = AuthorityMatcher(entries)
    current = current_pages(args.raw)
    reocr = {r["printed_page"]: r for r in json.loads(args.reocr.read_text(encoding="utf-8")) if r["printed_page"]}

    rows = []
    for number in sorted(set(current) & set(reocr), key=int):
        cur, new = current[number], reocr[number]
        cur_first = next((line.strip() for line in cur.split("\n") if line.strip()), "")
        # Compare the same printed text: leave out the running head on both sides
        # (and the re-OCR's separated footnotes, which the current text mixes in).
        cur_body = cur.split("\n", 1)[1] if "\n" in cur.strip() else ""
        new_text = "\n".join([new["body"], *[m["text"] for m in new["margin_notes"]], new["footnotes"]])
        cur_names, new_names = name_hits(matcher, cur_body), name_hits(matcher, new_text)
        rows.append({
            "printed_page": number,
            "current_head_date": bool(parse_dates(cur_first, years)),
            "reocr_head_date": bool(parse_dates(new["head"], years)),
            "current_persons": cur_names["PERSON"], "reocr_persons": new_names["PERSON"],
            "current_places": cur_names["PLACE"], "reocr_places": new_names["PLACE"],
            "margin_notes": len(new["margin_notes"]),
            "margin_notes_glued_in_current": sum(glued(m["text"], cur) for m in new["margin_notes"]),
            "footnote_chars": len(new["footnotes"]),
            "reocr_conf": new["mean_conf"],
        })
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    def total(key):
        return sum(r[key] for r in rows)

    print(f"{len(rows)} printed pages matched")
    print(f"  running-head dates parsed: current {total('current_head_date')}, re-OCR {total('reocr_head_date')}")
    print(f"  person forms found: current {total('current_persons')}, re-OCR {total('reocr_persons')}")
    print(f"  place forms found:  current {total('current_places')}, re-OCR {total('reocr_places')}")
    print(f"  margin notes separated by re-OCR: {total('margin_notes')} on "
          f"{sum(1 for r in rows if r['margin_notes'])} pages; glued into current text: "
          f"{total('margin_notes_glued_in_current')}")
    confs = [r["reocr_conf"] for r in rows if r["reocr_conf"] is not None]
    print(f"  re-OCR mean word confidence: {statistics.mean(confs):.1f}")


if __name__ == "__main__":
    main()
