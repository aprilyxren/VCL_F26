"""Suggest dictionary people for frequent unidentified names, for review.

Input: ``person_name_frequency.csv`` (unreviewed name groups), the NER output,
and the person dictionary. For each unreviewed group:

* **given + surname** (``Io: Dauers``, ``Nicho: ffarrar``): abbreviations are
  expanded (Io -> John, Tho -> Thomas) and surnames compared by a
  spelling-insensitive shape (ff = f, u = v, y = i, doubled letters), so
  ``Io: Dauers`` -> John Danvers. One matching person -> suggestion.
* **title + surname** (``m’ Bull``): every person with that surname is a
  candidate. One candidate -> suggestion. Several -> context decides: how
  often each candidate is named in full in the same meetings as this form,
  and whether the form's dates fall in the candidate's attested span.
* no dictionary person: if the text names someone with that surname in full
  (``Fra: Meuerell``), that full name is proposed, standardized
  (``Francis Meuerell``), as a **new** person; spelling variants of one
  surname share one proposal.
* a single candidate whose attested years do not fit this form's dates is
  marked ``date_conflict`` instead of suggested (``m* Newport`` in 1620s
  courts is not Christopher Newport, who died in 1617).

Writes ``person_suggestions.csv``; mark ``confirm`` with ``y`` for the right
suggestions (or write a different person in ``use_person``), then run with
``--apply`` to add the confirmed groups to the dictionary.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import json

import person_dictionary
from extend_person_alias_dictionary import LEADING_TITLE_RE, name_of
from filter_person_review_queue import normalized_phrase
from find_indigenous import skeleton
from build_ner import NON_NAME_WORDS
from link_mentions import GIVEN_NAMES

OFFICE_WORDS = NON_NAME_WORDS | {"deputies", "alderman", "aldermen", "committees", "comittees", "lords", "lo"}


SURNAME_MATCH = 0.82
FIELDS = [
    "confirm", "use_person", "name_group", "occurrences", "suggestion", "kind", "reason",
    "candidates", "years", "examples",
]


def name_parts(name: str) -> tuple[str, str]:
    """(given, surname) after dropping a title; given may be empty."""
    bare = LEADING_TITLE_RE.sub("", name)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", bare)
    if not words:
        return "", ""
    surname = words[-1]
    given = words[0] if len(words) >= 2 else ""
    given = GIVEN_NAMES.get(given.casefold(), given.casefold())
    return given, surname


def surname_shape(surname: str) -> str:
    return skeleton(surname.casefold().replace("ff", "f"))


def same_surname(a: str, b: str) -> bool:
    sa, sb = surname_shape(a), surname_shape(b)
    return sa == sb or (min(len(sa), len(sb)) >= 4 and SequenceMatcher(None, sa, sb).ratio() >= SURNAME_MATCH)


def standard_name(name: str) -> str:
    """``Rich: Rogers`` -> ``Richard Rogers``; ``Tho: ffelgate`` -> ``Thomas Felgate``."""
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", name)
    if len(words) < 2:
        return name
    given = GIVEN_NAMES.get(words[0].casefold(), words[0].casefold()).capitalize()
    surname = words[-1]
    surname = ("F" + surname[2:]) if surname.lower().startswith("ff") else surname[0].upper() + surname[1:]
    return f"{given} {surname}"


def year(row: dict) -> int | None:
    return int(row["date_start"][:4]) if row.get("date_start") else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frequency", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--primary", type=Path, help="Primary text, searched for full names of unknown surnames.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Add confirmed rows of --output to the dictionary.")
    args = parser.parse_args()

    data = person_dictionary.load(args.dictionary)
    if args.apply:
        apply(args.output, data, args.dictionary)
        return

    # Each dictionary person's given name and surname(s), and attested years.
    people: dict[str, dict] = {}
    for person, entry in data["people"].items():
        surnames, givens = set(), set()
        for alias in [person.split(",")[0], *entry.get("aliases", [])]:
            given, surname = name_parts(alias)
            if surname and len(surname) >= 3:
                surnames.add(surname)
            if given:
                givens.add(given)
        people[person] = {"surnames": surnames, "givens": givens, "years": set(), "meetings": Counter()}
    groups: dict[str, list[dict]] = defaultdict(list)
    full_names: dict[str, Counter] = defaultdict(Counter)  # surname shape -> unlinked full names
    with args.mentions.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["label"] != "PERSON" or row["usable_for_association"] != "yes":
                continue
            if row["entity_key"] in people:
                info = people[row["entity_key"]]
                if (y := year(row)) is not None:
                    info["years"].add(y)
                info["meetings"][row["segment_id"]] += 1
                continue
            name = name_of(row)
            key, _ = normalized_phrase(name)
            groups[key].append(row)
            given, surname = name_parts(name)
            if given and surname:
                full_names[surname_shape(surname)][" ".join(name.split())] += 1

    with args.frequency.open(encoding="utf-8", newline="") as handle:
        unreviewed = [r for r in csv.DictReader(handle) if r["action"] == "unreviewed"]

    texts = [p["text"] for p in json.loads(args.primary.read_text(encoding="utf-8"))] if args.primary else []
    abbreviations = "|".join(sorted({re.escape(k) for k in GIVEN_NAMES} | {re.escape(v) for v in GIVEN_NAMES.values()}, key=len, reverse=True))

    def text_full_names(surname: str) -> Counter:
        """``Fra: Meuerell``-style full names for a surname, searched in the page text."""
        found: Counter = Counter()
        pattern = re.compile(rf"(?i)(?<![A-Za-z])((?:{abbreviations})[:.]?)\s+({re.escape(surname)})(?![A-Za-z])")
        for text in texts:
            for m in pattern.finditer(text):
                found[f"{m.group(1)} {m.group(2)}"] += 1
        return found

    new_names: list[str] = []

    def merged_new_name(name: str) -> str:
        """Reuse an earlier proposal for the same person (``Sheppard`` / ``Shippard``)."""
        given, surname = name.split(" ", 1) if " " in name else ("", name)
        for earlier in new_names:
            g2, s2 = earlier.split(" ", 1) if " " in earlier else ("", earlier)
            if g2 == given and same_surname(surname, s2):
                return earlier
        new_names.append(name)
        return name

    rows = []
    for item in unreviewed:
        name = item["display_name"]
        given, surname = name_parts(name)
        mentions = groups.get(item["name_key"], [])
        years = sorted({y for m in mentions if (y := year(m)) is not None})
        examples = " || ".join(" ".join(m["sentence_text"].split())[:150] for m in mentions[:2])
        row = {"confirm": "", "use_person": "", "name_group": name, "occurrences": item["occurrence_count"],
               "years": f"{years[0]}-{years[-1]}" if years else "", "examples": examples}
        if not surname or len(surname) < 3:
            rows.append({**row, "kind": "skip", "reason": "no surname"})
            continue
        if surname.casefold() in OFFICE_WORDS:
            rows.append({**row, "kind": "not_a_name", "reason": f"'{surname}' is an office or title, not a surname"})
            continue
        candidates = [p for p, info in people.items() if any(same_surname(surname, s) for s in info["surnames"])]
        if given:
            candidates = [p for p in candidates if given in people[p]["givens"]] or (
                [] if any(given in people[p]["givens"] for p in candidates) else candidates)
        row["candidates"] = "; ".join(candidates[:6])
        if len(candidates) == 1:
            person = candidates[0]
            span = people[person]["years"]
            outside = [y for y in years if span and not (min(span) - 2 <= y <= max(span) + 2)]
            if span and years and len(outside) > len(years) / 2:
                rows.append({**row, "kind": "date_conflict",
                             "reason": f"{person} is attested {min(span)}-{max(span)}, but this form appears "
                                       f"{years[0]}-{years[-1]}; probably a different person"})
            elif given:
                rows.append({**row, "suggestion": person, "kind": "given_and_surname",
                             "reason": f"first name and surname match {person}"})
            else:
                rows.append({**row, "suggestion": person, "kind": "only_person_with_surname",
                             "reason": f"the only dictionary person surnamed like {surname}"})
        elif len(candidates) > 1:
            meetings = Counter(m["segment_id"] for m in mentions)
            scored = []
            for p in candidates:
                together = sum(min(n, people[p]["meetings"][seg]) for seg, n in meetings.items() if people[p]["meetings"][seg])
                span = people[p]["years"]
                in_span = sum(1 for y in years if span and min(span) - 1 <= y <= max(span) + 1)
                scored.append((together, in_span, p))
            scored.sort(reverse=True)
            best, second = scored[0], scored[1]
            if best[0] >= 3 and best[0] >= 2 * max(1, second[0]):
                rows.append({**row, "suggestion": best[2], "kind": "context",
                             "reason": f"named in full in {best[0]} of the same meetings (next: {second[2]}, {second[0]})"})
            else:
                rows.append({**row, "kind": "ambiguous",
                             "reason": "several people share this surname: " + ", ".join(f"{p} ({t} shared meetings)" for t, _, p in scored[:4])})
        else:
            full = Counter(full_names.get(surname_shape(surname), Counter()))
            full.update(text_full_names(surname))
            if full:
                proposed, n = full.most_common(1)[0]
                standard = merged_new_name(standard_name(proposed))
                rows.append({**row, "suggestion": f"NEW: {standard}", "kind": "new_person",
                             "reason": f"not in the dictionary; named in full as '{proposed}' {n} time{'s' if n > 1 else ''}"})
            else:
                variant = next((n for n in new_names if same_surname(surname, n.split(" ", 1)[-1])), None)
                if variant:
                    rows.append({**row, "suggestion": f"NEW: {variant}", "kind": "new_person",
                                 "reason": f"spelling variant of the surname of proposed new person {variant}"})
                else:
                    rows.append({**row, "kind": "unknown", "reason": "no dictionary person or full name with this surname"})

    rows.sort(key=lambda r: -int(r["occurrences"]))
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({f: r.get(f, "") for f in FIELDS} for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    print(f"{len(rows)} name groups -> {args.output}: {dict(kinds.most_common())}")
    print(f"  suggested: {sum(1 for r in rows if r.get('suggestion'))} groups covering "
          f"{sum(int(r['occurrences']) for r in rows if r.get('suggestion'))} mentions")


def apply(path: Path, data: dict, dictionary_path: Path) -> None:
    added_groups, new_people = 0, 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            person = row["use_person"].strip() or (row["suggestion"] if row["confirm"].strip().lower() in {"y", "yes"} else "")
            if not person:
                continue
            if person.startswith("NEW: "):
                person = person[5:]
            entry = data["people"].get(person)
            if entry is None:
                entry = data["people"][person] = {"aliases": [person], "source": "curated",
                                                  "basis": f"Confirmed from frequent form '{row['name_group']}': {row['reason']}"}
                new_people += 1
            groups = entry.setdefault("name_groups", [])
            if row["name_group"] not in groups:
                groups.append(row["name_group"])
                added_groups += 1
    person_dictionary.save(dictionary_path, data)
    print(f"Added {added_groups} confirmed name groups ({new_people} new people) to {dictionary_path}. "
          "Rerun extend_person_alias_dictionary.py to append their spellings.")


if __name__ == "__main__":
    main()
