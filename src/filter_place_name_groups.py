"""Remove exact authority-seeded place forms from provisional person groups.

This only removes exact place-form groups.  It does not remove a group merely
because it contains a place word (for example, ``Lord of Southampton``),
because a place word may occur inside a person reference or title.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("’", "'").replace("`", "'")
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(".,;:!?()[]{}")


def place_forms(authority: Path, extracted: Path) -> dict[str, set[tuple[str, str]]]:
    forms: dict[str, set[tuple[str, str]]] = {}

    def add(value: str, place_id: str, preferred_name: str) -> None:
        key = normalize(value)
        if key:
            forms.setdefault(key, set()).add((place_id, preferred_name))

    with authority.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            add(row["preferred_name"], row["place_id"], row["preferred_name"])
            for alias in row["aliases"].split("|"):
                add(alias, row["place_id"], row["preferred_name"])

    with extracted.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            add(row["matched_alias"], row["place_id"], row["preferred_name"])
            add(row["observed_span"], row["place_id"], row["preferred_name"])

    return forms


def filter_groups(groups: Path, output: Path, removed_output: Path, forms: dict[str, set[tuple[str, str]]]) -> tuple[int, int]:
    with groups.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        kept: list[dict[str, str]] = []
        removed: list[dict[str, str]] = []
        for row in reader:
            hits: set[tuple[str, str]] = set()
            for candidate in (row.get("display_name", ""), row.get("name_group_key", "")):
                hits.update(forms.get(normalize(candidate), set()))
            if hits:
                row["place_match"] = "; ".join(f"{place_id}:{name}" for place_id, name in sorted(hits))
                removed.append(row)
            else:
                kept.append(row)

    removed_fields = fieldnames + ["place_match"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    with removed_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=removed_fields)
        writer.writeheader()
        writer.writerows(removed)
    return len(kept), len(removed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--extracted", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--removed-output", type=Path, required=True)
    args = parser.parse_args()
    forms = place_forms(args.authority, args.extracted)
    kept, removed = filter_groups(args.groups, args.output, args.removed_output, forms)
    print(f"Kept {kept} person groups")
    print(f"Removed {removed} exact place groups")


if __name__ == "__main__":
    main()
