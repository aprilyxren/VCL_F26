"""Re-map the hand-reviewed NER sample onto the current text and score the NER.

The review sample was marked on the original OCR. After the switch to the
re-OCR, each reviewed span is located again in the page's new text: the same
words matched flexibly, then the longest word alone, choosing the hit closest
to the span's old relative position. The re-mapped copy keeps the reviewer's
marks with new offsets.

Scoring compares each reviewed span's mark (``person`` / ``place`` / blank)
with the NER label overlapping it. Reviewed fragments (``John``) that now sit
inside a correctly tagged full name are reported separately, not as errors.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def locate(span: str, text: str, expected: int) -> tuple[int, int] | None:
    words = re.findall(r"[A-Za-z]{2,}", span)
    if not words:
        return None
    for pattern in (r"\W+".join(map(re.escape, words)), re.escape(max(words, key=len))):
        hits = list(re.finditer(rf"(?i)(?<![A-Za-z]){pattern}(?![A-Za-z])", text))
        if hits:
            hit = min(hits, key=lambda m: abs(m.start() - expected))
            return hit.start(), hit.end()
    return None


def gold_label(row: dict) -> str:
    return {"person": "PERSON", "place": "PLACE"}.get(row["review_status"].strip().lower(), "NONE")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--remapped-output", type=Path, required=True)
    args = parser.parse_args()

    pages = {p["page_id"]: p for p in json.loads(args.primary.read_text(encoding="utf-8"))}
    with args.sample.open(encoding="utf-8-sig", newline="") as handle:
        sample = list(csv.DictReader(handle))
    mentions: dict[str, list[dict]] = defaultdict(list)
    with args.mentions.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mentions[row["page_id"]].append(row)

    remapped = []
    for row in sample:
        page = pages[row["page_id"]]
        old = page.get("original_text", page["text"])
        start, end = int(row["span_start"]), int(row["span_end"])
        if page.get("ocr_source") == "tesseract_reocr":
            expected = int(start / max(1, len(old)) * len(page["text"]))
            hit = locate(row["observed_span"], page["text"], expected)
        else:
            hit = (start, end)
        row = dict(row)
        row["new_span_start"], row["new_span_end"] = hit if hit else ("", "")
        remapped.append(row)
    fields = list(sample[0]) + ["new_span_start", "new_span_end"]
    with args.remapped_output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(remapped)

    confusion: Counter = Counter()
    fragments = 0
    for row in remapped:
        if row["new_span_start"] == "":
            continue
        s, e = int(row["new_span_start"]), int(row["new_span_end"])
        overlapping = [m for m in mentions[row["page_id"]] if int(m["span_start"]) < e and s < int(m["span_end"])]
        labels = {m["label"] for m in overlapping}
        predicted = "PERSON" if "PERSON" in labels else "PLACE" if "PLACE" in labels else "NONE"
        gold = gold_label(row)
        # A reviewed fragment ("John") now inside a longer tagged name is not a false positive.
        if gold == "NONE" and predicted != "NONE" and all(
                int(m["span_end"]) - int(m["span_start"]) > (e - s) + 3 for m in overlapping if m["label"] == predicted):
            fragments += 1
            continue
        confusion[(predicted, gold)] += 1

    mapped = sum(1 for r in remapped if r["new_span_start"] != "")
    print(f"re-mapped {mapped}/{len(remapped)} reviewed spans; {fragments} fragments now inside full names (skipped)")
    for label in ("PERSON", "PLACE"):
        tp = confusion[(label, label)]
        fp = sum(v for (p, g), v in confusion.items() if p == label and g != label)
        fn = sum(v for (p, g), v in confusion.items() if g == label and p != label)
        print(f"  {label}: precision {tp / max(1, tp + fp):.1%} ({tp}/{tp + fp}), recall {tp / max(1, tp + fn):.1%} ({tp}/{tp + fn})")


if __name__ == "__main__":
    main()
