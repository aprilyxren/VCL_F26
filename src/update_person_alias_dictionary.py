"""Append titled-person aliases to the List of Records person dictionary.

Only rows with a rule-based ``suggested_person`` are used. Existing keys and
aliases are never removed or reordered; new aliases are appended, and a new
key is created only for a person the dictionary does not yet contain.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import person_dictionary


# Canonical forms for people who are new to the dictionary.
NEW_PERSON_SEEDS = {
    "Thomas Cecil, Earl of Exeter": ["Thomas Cecil", "Earl of Exeter", "Thomas, Earl of Exeter"],
    "John King, Bishop of London": ["John King", "Bishop of London", "Lord Bishop of London"],
}


def clean_alias(value: str) -> str:
    return " ".join(value.split()).strip(" ,.;")


def update(source: Path, dictionary_path: Path) -> dict[str, list[str]]:
    data = person_dictionary.load(dictionary_path)
    added: dict[str, list[str]] = {}
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            person = row.get("suggested_person", "")
            if not person:
                continue
            if person not in data["people"]:
                data["people"][person] = {
                    "aliases": [], "source": "territorial_title_rule", "basis": row.get("rule_note", ""),
                }
                for seed in NEW_PERSON_SEEDS.get(person, []):
                    data["people"][person]["aliases"].append(seed)
                    added.setdefault(person, []).append(seed)
            aliases = data["people"][person].setdefault("aliases", [])
            alias = clean_alias(row.get("observed_span", ""))
            known = {value.casefold() for value in aliases}
            if alias and alias.casefold() not in known:
                aliases.append(alias)
                added.setdefault(person, []).append(alias)
    person_dictionary.save(dictionary_path, data)
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("dictionary", type=Path)
    args = parser.parse_args()
    for person, aliases in update(args.source, args.dictionary).items():
        print(f"{person}: added {len(aliases)} aliases")
        for alias in aliases:
            print(f"  {alias}")
