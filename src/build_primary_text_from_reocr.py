"""Rebuild the primary text from the layout-aware re-OCR, keeping page IDs.

Every existing primary page (``V01-P0001`` ...) is matched to a re-OCR page
image by printed page number, then verified by word overlap with the old
text, so an Internet Archive page-numbering error cannot silently swap
pages. Old pages that duplicate another page (the original scans repeat some
runs of pages) are emptied and marked ``duplicate``. Gaps in the printed
numbers are filled from the neighbouring pages,
and an unverified match falls back to nearby page images before giving up.

The new ``text`` is the running head (first line, as ``date_pages`` and
``flag_page_roles`` expect) followed by the body. Margin notes and footnotes
are kept in their own fields and are not part of ``text``. Pages without a
verified match keep their old text (``ocr_source = "original"``).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


MIN_OVERLAP = 0.25
LEAF_SEARCH = 3


def printed_number(text: str) -> int | None:
    first = next((line.strip() for line in text.split("\n") if line.strip()), "")
    match = re.search(r"(\d{1,3})\s*[|.]?\s*$", first) or re.match(r"^\s*(\d{1,3})\s", first)
    return int(match.group(1)) if match else None


def words(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"[A-Za-z]{4,}", text)}


def overlap(old: str, new: str) -> float:
    a, b = words(old), words(new)
    return len(a & b) / max(1, min(len(a), len(b))) if a and b else 0.0


def fill_gaps(numbers: list[int | None]) -> list[int | None]:
    """Fill a missing printed number when its neighbours agree on it."""
    filled = list(numbers)
    for i, value in enumerate(filled):
        if value is not None:
            continue
        before = next(((j, filled[j]) for j in range(i - 1, -1, -1) if filled[j] is not None), None)
        after = next(((j, numbers[j]) for j in range(i + 1, len(numbers)) if numbers[j] is not None), None)
        if before and after and after[1] - before[1] == after[0] - before[0]:
            filled[i] = before[1] + (i - before[0])
        elif before and not after:
            filled[i] = before[1] + (i - before[0])
    return filled


def new_text(record: dict) -> str:
    head = record["head"].strip()
    body = record["body"].strip()
    return f"{head}\n\n{body}" if head else body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True, help="current combined_primary_text.json")
    parser.add_argument("--reocr-dir", type=Path, required=True, help="folder of volN_tesseract_layout.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    args = parser.parse_args()

    pages = json.loads(args.primary.read_text(encoding="utf-8"))
    mapping_rows = []
    by_volume: dict[int, list[dict]] = {}
    for page in pages:
        by_volume.setdefault(page["volume"], []).append(page)

    for volume, volume_pages in by_volume.items():
        records = json.loads((args.reocr_dir / f"vol{volume}_tesseract_layout.json").read_text(encoding="utf-8"))
        by_leaf = {r["leaf"]: r for r in records}
        by_printed: dict[int, list[dict]] = {}
        for r in records:
            if str(r["printed_page"]).isdigit():
                by_printed.setdefault(int(r["printed_page"]), []).append(r)
        numbers = fill_gaps([printed_number(p.get("original_text", p["text"])) for p in volume_pages])
        leaf_words = {r["leaf"]: words(new_text(r) + " " + r["footnotes"]) for r in records}

        def score(old_words: set[str], leaf: int) -> float:
            other = leaf_words[leaf]
            return len(old_words & other) / max(1, min(len(old_words), len(other))) if old_words and other else 0.0

        # Score candidates for every page: printed number, the neighbourhood of
        # its expected leaf, and (when none verifies) every leaf in the volume.
        proposals: list[tuple[float, int, int]] = []
        expected_leaf = None
        for index, number in enumerate(numbers):
            old_words = words(volume_pages[index].get("original_text", volume_pages[index]["text"]))
            near = {c["leaf"] for c in by_printed.get(number, [])} if number is not None else set()
            anchors = set(near) | ({expected_leaf + 1} if expected_leaf is not None else set())
            for anchor in list(anchors):
                near |= {leaf for leaf in range(anchor - LEAF_SEARCH, anchor + LEAF_SEARCH + 1) if leaf in by_leaf}
            scored = sorted(((score(old_words, leaf), leaf) for leaf in near), reverse=True)
            if not scored or scored[0][0] < MIN_OVERLAP:
                scored = sorted(((score(old_words, leaf), leaf) for leaf in by_leaf), reverse=True)[:5]
            proposals += [(sc, index, leaf) for sc, leaf in scored[:5] if sc >= MIN_OVERLAP]
            if scored and scored[0][0] >= MIN_OVERLAP:
                expected_leaf = scored[0][1]
        # Assign strongest matches first, so a weak neighbour claim cannot take a leaf.
        matches: dict[int, tuple[float, dict]] = {}
        used_leaves: set[int] = set()
        for sc, index, leaf in sorted(proposals, reverse=True):
            if index in matches or leaf in used_leaves:
                continue
            matches[index] = (sc, by_leaf[leaf])
            used_leaves.add(leaf)
        # Blank pages take the free leaf between their matched neighbours.
        for index, page in enumerate(volume_pages):
            if index in matches or page.get("original_text", page["text"]).strip():
                continue
            prev = next((matches[j][1]["leaf"] + (index - j) for j in range(index - 1, -1, -1) if j in matches), None)
            if prev is not None and prev in by_leaf and prev not in used_leaves:
                matches[index] = (0.0, by_leaf[prev])
                used_leaves.add(prev)

        # The original scans repeat some pages (e.g. Volume II printed pp. 136-150
        # appear twice). A leftover page that copies a matched page is a duplicate.
        duplicate_of: dict[int, str] = {}
        matched_words = {i: words(volume_pages[i].get("original_text", volume_pages[i]["text"])) for i in matches}
        for index, page in enumerate(volume_pages):
            old_words = words(page.get("original_text", page["text"]))
            if index in matches or len(old_words) < 20:
                continue
            for other, other_words in matched_words.items():
                if len(old_words & other_words) / max(1, min(len(old_words), len(other_words))) >= 0.9:
                    duplicate_of[index] = volume_pages[other]["page_id"]
                    break

        for index, (page, number) in enumerate(zip(volume_pages, numbers)):
            old = page.get("original_text", page["text"])
            best_score, best = matches.get(index, (0.0, None))
            page["original_text"] = old
            if index in duplicate_of:
                page["text"] = ""
                page["margin_notes"] = []
                page["footnotes"] = ""
                page["ocr_source"] = "duplicate"
                page["duplicate_of"] = duplicate_of[index]
            elif best is not None:
                page["text"] = new_text(best)
                page["margin_notes"] = [{"text": n["text"], "side": n["side"]} for n in best["margin_notes"]]
                page["footnotes"] = best["footnotes"]
                page["ocr_source"] = "tesseract_reocr"
                page["reocr_leaf"] = best["leaf"]
                page["printed_page"] = best["printed_page"]
            else:
                page["ocr_source"] = "original"
                page["margin_notes"] = []
                page["footnotes"] = ""
            mapping_rows.append({
                "page_id": page["page_id"], "volume": volume, "printed_page_old": number or "",
                "reocr_leaf": page.get("reocr_leaf", ""), "printed_page_reocr": page.get("printed_page", ""),
                "overlap": round(best_score, 3), "ocr_source": page["ocr_source"],
                "margin_notes": len(page["margin_notes"]),
            })

    args.output.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    with args.mapping_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(mapping_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(mapping_rows)
    for volume in sorted(by_volume):
        rows = [r for r in mapping_rows if r["volume"] == volume]
        switched = sum(1 for r in rows if r["ocr_source"] == "tesseract_reocr")
        duplicates = sum(1 for r in rows if r["ocr_source"] == "duplicate")
        print(f"Volume {volume}: {switched}/{len(rows)} pages switched to re-OCR; "
              f"{sum(r['margin_notes'] for r in rows)} margin notes; {duplicates} duplicate pages emptied; "
              f"{len(rows) - switched - duplicates} kept original")


if __name__ == "__main__":
    main()
