"""Export hard-negative candidates in a human-editable CSV layout."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = [
    "review_decision",
    "review_note",
    "occurrence_id",
    "observed_span",
    "proposed_training_label",
    "review_status",
    "page_id",
    "span_start",
    "span_end",
    "sentence_text",
]


def export(source: Path, output: Path) -> None:
    candidates = json.load(source.open(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for candidate in candidates:
            row = {field: candidate.get(field, "") for field in FIELDS}
            row["review_decision"] = ""
            row["review_note"] = ""
            writer.writerow(row)
    print(f"Exported {len(candidates)} review rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.source, args.output)
