"""Group duplicate name strings without merging person identities."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


CAPTAIN_RE = re.compile(r"(?i)\b(?:captain|captaine|capt\.?|capt:)\b")


def group_names(source: Path, occurrence_output: Path, group_output: Path) -> tuple[int, int]:
    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        name = (row.get("name_string") or "").strip()
        if not name:
            name = (row.get("observed_span") or "").strip()
        row["name_group_key"] = name.casefold()
        grouped[row["name_group_key"]].append(row)

    for index, key in enumerate(sorted(grouped), start=1):
        group_id = f"NAME_{index:05d}"
        for row in grouped[key]:
            row["name_group_id"] = group_id
            sentence = row.get("sentence_text", "")
            name = row.get("name_string", "")
            captain_context = bool(CAPTAIN_RE.search(sentence)) and "smith" in name.casefold()
            row["contextual_person_suggestion"] = "Captain John Smith" if captain_context else ""
            row["person_resolution_status"] = (
                "suggested_context_match" if captain_context else "unresolved_person_identity"
            )

    occurrence_fields = list(rows[0].keys()) if rows else []
    occurrence_output.parent.mkdir(parents=True, exist_ok=True)
    with occurrence_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=occurrence_fields)
        writer.writeheader()
        writer.writerows(rows)

    group_rows = []
    for key in sorted(grouped):
        members = grouped[key]
        group_rows.append({
            "name_group_id": members[0]["name_group_id"],
            "name_group_key": key,
            "display_name": members[0].get("name_string") or members[0].get("observed_span", ""),
            "occurrence_count": len(members),
            "observed_forms": " | ".join(sorted({m.get("observed_span", "") for m in members})),
            "captain_context_suggestion_count": sum(bool(m.get("contextual_person_suggestion")) for m in members),
            "person_identity_status": "suggestion_only",
            "review_note": "Do not merge occurrences into one person without occurrence-specific context or an approved safe alias.",
        })
    group_fields = [
        "name_group_id", "name_group_key", "display_name", "occurrence_count",
        "observed_forms", "captain_context_suggestion_count",
        "person_identity_status", "review_note",
    ]
    with group_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=group_fields)
        writer.writeheader()
        writer.writerows(group_rows)

    return len(rows), len(group_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("occurrence_output", type=Path)
    parser.add_argument("group_output", type=Path)
    args = parser.parse_args()
    occurrences, groups = group_names(args.source, args.occurrence_output, args.group_output)
    print(f"Grouped {occurrences} occurrences into {groups} name groups")
