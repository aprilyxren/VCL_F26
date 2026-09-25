"""Export occurrence-level person mentions for manual review."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = [
    "review_decision",
    "manual_person_name",
    "title_override",
    "create_safe_alias",
    "review_note",
    "occurrence_id",
    "observed_span",
    "name_string",
    "label",
    "mention_kind",
    "resolution_status",
    "page_id",
    "section_id",
    "source_id",
    "span_start",
    "span_end",
    "sentence_text",
]


def export(source: Path, output: Path) -> None:
    mentions = json.load(source.open(encoding="utf-8"))
    reviewable = [
        mention for mention in mentions
        if mention["label"] in {"PERSON_NAME", "PERSON_REF"}
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for mention in reviewable:
            row = {field: mention.get(field, "") for field in FIELDS}
            for field in ("review_decision", "manual_person_name", "title_override", "create_safe_alias", "review_note"):
                row[field] = ""
            writer.writerow(row)
    print(f"Exported {len(reviewable)} occurrence-level person rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.source, args.output)
