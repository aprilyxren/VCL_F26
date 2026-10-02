"""Draw a stratified page sample of NER predictions for hand review.

Pages are drawn per volume (record pages with text only) and interleaved
round-robin, so reviewing just the first rows still covers every volume.
Every prediction on a sampled page is listed, candidates included, so the
reviewer can confirm, correct, or reject it. Missed entities are added as new
rows (``review_status=missed``) with the exact text; offsets are filled in later.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

from source_loader import load_primary_pages


REVIEW_FIELDS = [
    "review_order", "page_id", "review_status", "correct_label", "correct_entity", "review_note",
    "predicted_label", "entity_name", "observed_span", "context",
    "source", "entity_key", "mention_id", "span_start", "span_end",
]
CONTEXT = 70


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--page-roles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pages-per-volume", type=int, nargs=4, default=[15, 15, 10, 10],
                        metavar=("V1", "V2", "V3", "V4"))
    parser.add_argument("--seed", type=int, default=1619)
    args = parser.parse_args()

    pages = {page["page_id"]: page for page in load_primary_pages(args.source)}
    with args.page_roles.open(encoding="utf-8", newline="") as handle:
        record = {row["page_id"] for row in csv.DictReader(handle) if row["role"] == "record"}
    mentions: dict[str, list[dict[str, str]]] = defaultdict(list)
    with args.mentions.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # Running heads and editorial lines are already excluded downstream.
            if row["usable_for_association"] == "yes":
                mentions[row["page_id"]].append(row)

    rng = random.Random(args.seed)
    per_volume = []
    for volume, count in zip((1, 2, 3, 4), args.pages_per_volume):
        eligible = sorted(
            pid for pid, page in pages.items()
            if page["volume"] == volume and pid in record and len(page["text"].strip()) > 400 and mentions[pid]
        )
        per_volume.append(rng.sample(eligible, min(count, len(eligible))))
    order = [pid for round_ in range(max(map(len, per_volume))) for vol in per_volume
             if round_ < len(vol) for pid in [vol[round_]]]

    rows = []
    for page_number, page_id in enumerate(order, start=1):
        text = pages[page_id]["text"]
        for mention in sorted(mentions[page_id], key=lambda m: int(m["span_start"])):
            start, end = int(mention["span_start"]), int(mention["span_end"])
            context = (text[max(0, start - CONTEXT):start] + "[[" + text[start:end] + "]]"
                       + text[end:end + CONTEXT])
            rows.append({
                "review_order": page_number, "page_id": page_id, "review_status": "",
                "correct_label": "", "correct_entity": "", "review_note": "",
                "predicted_label": mention["label"], "entity_name": mention["entity_name"],
                "observed_span": " ".join(mention["observed_span"].split()),
                "context": " ".join(context.split()),
                "source": mention["source"], "entity_key": mention["entity_key"],
                "mention_id": mention["mention_id"], "span_start": start, "span_end": end,
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} predictions from {len(order)} pages to {args.output}")


if __name__ == "__main__":
    main()
