"""Refresh provisional person groups using current non-person/place rules.

The refresh is removal-only, so previously reviewed groups do not reappear.
It applies the current prose-fragment rules, exact place aliases, and explicit
geographic constructions. It does not remove a group merely because it
contains a place word (for example, ``Henry Southampton``), and it preserves
ambiguous cases such as the person ``Peirce Cape``.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from filter_person_review_queue import (
    is_clear_non_person_phrase,
    load_place_aliases,
    normalized_phrase,
    place_exclusion_reason,
    requires_contextual_person_review,
)

def read_contexts(path: Path | None) -> dict[str, list[str]]:
    contexts: dict[str, list[str]] = {}
    if path is None:
        return contexts
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            value = row.get("name_string") or row.get("observed_span", "")
            key, _ = normalized_phrase(value)
            if key:
                contexts.setdefault(key, []).append(row.get("sentence_text", ""))
    return contexts


def filter_groups(
    groups: Path,
    output: Path,
    removed_output: Path | None,
    forms: dict[str, set[tuple[str, str, str]]],
    contexts: dict[str, list[str]],
) -> tuple[int, int, Counter[str]]:
    with groups.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        kept: list[dict[str, str]] = []
        removed: list[dict[str, str]] = []
        reason_counts: Counter[str] = Counter()
        for row in reader:
            display_name = row.get("display_name", "")
            key, _ = normalized_phrase(display_name or row.get("name_group_key", ""))
            sentence = " ".join(contexts.get(key, []))
            if is_clear_non_person_phrase(display_name):
                reason = "current_clear_non_person_rule"
            elif requires_contextual_person_review(display_name):
                reason = "contextual_reference_not_full_name"
            else:
                reason = place_exclusion_reason(display_name, sentence, forms)
            if reason:
                row["exclusion_reason"] = reason
                removed.append(row)
                reason_counts[reason] += 1
            else:
                kept.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    if removed_output is not None:
        removed_fields = fieldnames + ["exclusion_reason"]
        with removed_output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=removed_fields)
            writer.writeheader()
            writer.writerows(removed)
    return len(kept), len(removed), reason_counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--occurrences", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--removed-output", type=Path)
    args = parser.parse_args()
    forms = load_place_aliases(args.authority)
    contexts = read_contexts(args.occurrences)
    kept, removed, reasons = filter_groups(
        args.groups, args.output, args.removed_output, forms, contexts
    )
    print(f"Kept {kept} person groups")
    print(f"Removed {removed} non-name/place groups")
    for reason, count in sorted(reasons.items()):
        print(f"  {reason}: {count}")


if __name__ == "__main__":
    main()
