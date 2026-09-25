"""Collect review candidates for spaCy's incorrect-spans training data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


KNOWN_GENERIC_FORMS = {
    "deputy", "deputie", "counsell", "councell", "counsel", "councel", "treasurer",
    "majesty", "ibid", "statutes", "nos",
}

PROPOSED_LABELS = {
    "deputy": "TITLE_OR_ROLE",
    "deputie": "TITLE_OR_ROLE",
    "counsell": "ORG_OR_COMMITTEE",
    "councell": "ORG_OR_COMMITTEE",
    "counsel": "ORG_OR_COMMITTEE",
    "councel": "ORG_OR_COMMITTEE",
    "treasurer": "TITLE_OR_ROLE",
    "majesty": "TITLE_OR_ROLE",
    "ibid": "NOT_ENTITY",
    "statutes": "NOT_ENTITY",
    "nos": "EDITORIAL_REFERENCE",
}


def build(source: Path, output: Path) -> None:
    mentions = json.load(source.open(encoding="utf-8"))
    candidates = []
    for mention in mentions:
        if mention["observed_span"].strip().casefold() not in KNOWN_GENERIC_FORMS:
            continue
        candidates.append({
            "occurrence_id": mention["occurrence_id"],
            "page_id": mention["page_id"],
            "span_start": mention["span_start"],
            "span_end": mention["span_end"],
            "observed_span": mention["observed_span"],
            "sentence_text": mention["sentence_text"],
            "proposed_training_label": PROPOSED_LABELS[
                mention["observed_span"].strip().casefold()
            ],
            "review_status": "candidate",
            "review_note": "Confirm whether this occurrence is generic/non-person usage before training.",
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(candidates, handle, ensure_ascii=False, indent=2)
    print(f"Collected {len(candidates)} hard-negative candidates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
