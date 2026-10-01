"""Read and write the List of Records person alias dictionary.

Shape::

    {
      "description": "...",
      "people": {
        "Robert Johnson": {
          "aliases": ["Robert Johnson", "Alderman Iohnson", ...],
          "source": "list_of_records" | "curated" | "territorial_title_rule",
          "basis": "Historical reason for the identification.",
          "name_groups": ["Alderman Iohnson", ...],   # optional, curated name_string forms
          "extra_aliases": [...],                     # optional, literal spellings
          "skip_aliases": [...]                       # optional, spellings never to add
        }
      },
      "excluded": {"M’ Deputy": "Office; held by John then Nicholas Ferrar."}
    }

Aliases are only ever appended; existing people and aliases are never removed.
"""

from __future__ import annotations

import json
from pathlib import Path


DESCRIPTION = (
    "Person alias dictionary for the Virginia Company records. Each person has "
    "observed alias spellings plus the source and historical basis of the "
    "identification. 'excluded' lists frequent name strings deliberately not "
    "assigned to anyone, with the reason."
)
FIELD_ORDER = ("aliases", "source", "basis", "name_groups", "extra_aliases", "skip_aliases")


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "people" not in data:
        raise ValueError(f"{path} is not in the people/excluded format")
    data.setdefault("excluded", {})
    return data


def save(path: Path, data: dict) -> None:
    # ``aliases`` is always written (first); other fields only when non-empty.
    people = {
        name: {
            field: entry.get(field, [])
            for field in FIELD_ORDER
            if field == "aliases" or entry.get(field) not in (None, "", [])
        }
        for name, entry in data["people"].items()
    }
    output = {"description": data.get("description", DESCRIPTION), "people": people, "excluded": data["excluded"]}
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def aliases_by_person(data: dict) -> dict[str, list[str]]:
    return {name: entry.get("aliases", []) for name, entry in data["people"].items()}
