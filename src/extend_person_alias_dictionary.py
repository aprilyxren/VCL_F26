"""Add frequent person names to the List of Records person alias dictionary.

Reads the NER output (``ner_mentions.csv``): every usable ``PERSON`` mention,
with its name taken from the observed span minus a leading title (``m*``,
``Capt.``, ``S'``, ``Lo:`` ...). Name groups (grouped by the normalized name)
that occur at least ``--min-count`` times are resolved, most frequent first:

* a group listed in a person's ``name_groups`` is appended to that person;
* a group listed under ``excluded`` is skipped with its recorded reason;
* a group that exactly matches one person's alias is appended to that person;
* anything else is left ``unreviewed`` (add it to ``name_groups`` or
  ``excluded`` in the dictionary to resolve it).

Curation lives in the dictionary itself (see ``person_dictionary.py``).
Aliases are only ever appended. A frequency-sorted log records every group.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

import person_dictionary
import re

from filter_person_review_queue import normalized_phrase

# A leading title in an observed span: ``m* Seaward``, ``Capt. Tucker``, ``S': ffra: Wyate``.
LEADING_TITLE_RE = re.compile(
    r"(?i)^(?:(?:captain|captaine|captayne|capt\.?|cap[’'*:.]+|sir|s[’'*:]+|lady|lord|lo[:.]|doctor|dr\.?|"
    r"d[’'*:]+|master|mistress|mr\.?|mrs\.?|m[’'*ᵣ\"”™]+|mt)\s*[,;:\-—–]?\s*)+")


def name_of(row: dict[str, str]) -> str:
    """The person's name: ``name_string`` when present, else the span minus its
    title when a first name and surname remain (``m’ Abraham Chamberlyn``).
    A title with only a surname (``m* Wrote``) keeps the title, as the curated
    name groups do; punctuation is ignored when grouping, so it matches ``M’ Wrote``."""
    if row.get("name_string"):
        return row["name_string"]
    span = " ".join(row.get("observed_span", "").split())
    bare = LEADING_TITLE_RE.sub("", span)
    return bare if len(re.findall(r"[A-Za-z]{2,}", bare)) >= 2 else span


def person_rows(source: Path):
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if "label" in row and (row["label"] != "PERSON" or row.get("usable_for_association") != "yes"):
                continue
            yield row


MAX_FORMS = 8
LOG_FIELDS = [
    "rank", "name_key", "display_name", "occurrence_count", "action",
    "person_key", "aliases_added", "basis",
]


def clean(value: str) -> str:
    # § and || mark the editor's insertions in the edition; they are not part of a name.
    return " ".join(value.replace("§", "").replace("||", "").split()).strip(" ,.;:-'’")


def append_unique(values: list[str], new_values: list[str]) -> list[str]:
    known = {value.casefold() for value in values}
    added = []
    for value in new_values:
        value = clean(value)
        if value and value.casefold() not in known:
            known.add(value.casefold())
            values.append(value)
            added.append(value)
    return added


def extend(source: Path, dictionary_path: Path, log_output: Path, min_count: int) -> None:
    counts: Counter[str] = Counter()
    names: dict[str, Counter[str]] = defaultdict(Counter)
    forms: dict[str, Counter[str]] = defaultdict(Counter)
    for row in person_rows(source):
        name = name_of(row)
        key, _ = normalized_phrase(name)
        if not key:
            continue
        counts[key] += 1
        names[key][clean(name)] += 1
        forms[key][clean(name)] += 1
        forms[key][clean(row.get("observed_span", ""))] += 1

    data = person_dictionary.load(dictionary_path)
    people = data["people"]
    alias_index: dict[str, set[str]] = defaultdict(set)
    curated: dict[str, tuple[str, str, str]] = {}
    missing_groups: list[str] = []
    for person, entry in people.items():
        for alias in [person, *entry.get("aliases", [])]:
            phrase, _ = normalized_phrase(alias)
            if phrase:
                alias_index[phrase].add(person)
        action = "existing_key" if entry.get("source") == "list_of_records" else "curated_key"
        for form in entry.get("name_groups", []):
            key, _ = normalized_phrase(form)
            if key not in counts:
                missing_groups.append(f"{person}: {form}")  # spelled differently in the current text
            curated[key] = (action, person, entry.get("basis", ""))
    for form, reason in data["excluded"].items():
        key, _ = normalized_phrase(form)
        curated[key] = ("excluded", "", reason)

    log = []
    summary: Counter[str] = Counter()
    ranked = [(key, count) for key, count in counts.most_common() if count >= min_count]
    for rank, (key, count) in enumerate(ranked, start=1):
        action, person, basis = curated.get(key, ("", "", ""))
        if not action:
            matches = alias_index.get(key, set())
            if len(matches) == 1:
                action, person = "matched_existing_alias", next(iter(matches))
            elif len(matches) > 1:
                action, basis = "ambiguous_alias", "Matches aliases of: " + "; ".join(sorted(matches))
            else:
                action = "unreviewed"
        added: list[str] = []
        if person:
            entry = people[person]
            skipped = {clean(alias).casefold() for alias in entry.get("skip_aliases", [])}
            added = append_unique(entry.setdefault("aliases", []), [
                form for form, _ in forms[key].most_common(MAX_FORMS) if form.casefold() not in skipped
            ])
        summary[action] += 1
        log.append({
            "rank": rank, "name_key": key, "display_name": names[key].most_common(1)[0][0],
            "occurrence_count": count, "action": action, "person_key": person,
            "aliases_added": "|".join(added), "basis": basis,
        })

    for person, entry in people.items():
        extra = append_unique(entry.setdefault("aliases", []), entry.get("extra_aliases", []))
        if extra:
            log.append({"action": "extra_alias", "person_key": person,
                        "aliases_added": "|".join(extra), "basis": entry.get("basis", "")})

    person_dictionary.save(dictionary_path, data)
    with log_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in LOG_FIELDS} for row in log)

    print(f"{len(ranked)} name groups with >= {min_count} occurrences")
    for action, count in summary.most_common():
        print(f"  {action}: {count}")
    print(f"Dictionary has {len(people)} people")
    if missing_groups:
        print(f"{len(missing_groups)} curated name groups do not occur in this text (kept, nothing appended), e.g. "
              + "; ".join(missing_groups[:5]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("dictionary", type=Path)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--min-count", type=int, default=3)
    args = parser.parse_args()
    extend(args.source, args.dictionary, args.log_output, args.min_count)
