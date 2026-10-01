"""Link person and place mentions to authority keys, dates, and meetings.

Each mention gets:

* ``entity_key``: a person-dictionary key or a place ID. Persons resolve only
  by an exact (normalized) alias match to exactly one person, or by the
  territorial-title rules (``Earle of Southampton``); ambiguous, excluded, and
  unmatched names are kept with that status rather than guessed.
* ``segment_id`` and dates from ``text_segments.csv`` (the meeting the
  mention falls in).
* ``usable_for_association``: false on index pages and inside running heads
  or editorial metadata lines.

Inputs are left unchanged; two linked CSVs are written.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from collections import defaultdict
from pathlib import Path

import person_dictionary
from filter_person_review_queue import normalized_phrase


FIELDS = [
    "mention_id", "entity_type", "entity_key", "entity_name", "link_method", "link_note",
    "observed_span", "page_id", "span_start", "span_end",
    "segment_id", "date_start", "date_end", "date_source", "date_confidence",
    "page_role", "editorial_span", "usable_for_association", "sentence_text",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class Locator:
    """Find the meeting segment, page role, and editorial span for a position."""

    def __init__(self, segments: Path, roles: Path, editorial: Path) -> None:
        self.segments: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(segments):
            self.segments[row["page_id"]].append(row)
        self.starts = {
            page_id: [int(row["char_start"]) for row in rows] for page_id, rows in self.segments.items()
        }
        self.roles = {row["page_id"]: row["role"] for row in read_csv(roles)}
        self.editorial: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for row in read_csv(editorial):
            self.editorial[row["page_id"]].append((int(row["char_start"]), int(row["char_end"]), row["kind"]))

    def locate(self, page_id: str, start: int, end: int) -> dict[str, str]:
        segment = {}
        if page_id in self.starts:
            index = max(0, bisect.bisect_right(self.starts[page_id], start) - 1)
            segment = self.segments[page_id][index]
        kind = next((k for s, e, k in self.editorial.get(page_id, []) if s < end and start < e), "")
        role = self.roles.get(page_id, "record")
        return {
            "segment_id": segment.get("segment_id", ""),
            "date_start": segment.get("date_start", ""),
            "date_end": segment.get("date_end", ""),
            "date_source": segment.get("date_source", ""),
            "date_confidence": segment.get("confidence", ""),
            "page_role": role,
            "editorial_span": kind,
            "usable_for_association": "yes" if role == "record" and not kind else "no",
        }


# Given-name spellings and abbreviations that differ only orthographically.
GIVEN_NAMES = {
    "iohn": "john", "jhon": "john", "ihon": "john", "jo": "john", "io": "john",
    "frauncis": "francis", "fraunces": "francis", "fra": "francis",
    "robt": "robert", "robte": "robert", "rob": "robert", "ro": "robert",
    "willm": "william", "wm": "william", "wiltm": "william", "wilm": "william", "will": "william",
    "tho": "thomas", "thos": "thomas", "thom": "thomas",
    "edw": "edward", "ed": "edward", "geo": "george", "georg": "george",
    "nich": "nicholas", "nicho": "nicholas", "nicolas": "nicholas",
    "rich": "richard", "richd": "richard", "hen": "henry", "henrie": "henry",
    "sam": "samuel", "samuell": "samuel", "nath": "nathaniel", "nathaniell": "nathaniel",
    "danyell": "daniel", "daniell": "daniel", "gabriell": "gabriel", "mathew": "matthew",
    "chr": "christopher", "xpofer": "christopher", "xfopher": "christopher",
    "humfrey": "humphrey", "humphry": "humphrey", "iames": "james", "jam": "james",
    "maurice": "morris", "morrice": "morris",
}


def match_key(value: str) -> str:
    """Normalized phrase with standardized given names and trailing junk dropped
    (``Lord Cauendish. The`` -> ``lord cauendish``)."""
    head = value.split(". ")[0] if ". " in value and len(value.split(". ")[0].split()) >= 2 else value
    phrase, tokens = normalized_phrase(head)
    return " ".join(GIVEN_NAMES.get(token, token) for token in tokens) if phrase else ""


def person_index(dictionary: dict) -> tuple[dict[str, set[str]], set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for name, entry in dictionary["people"].items():
        for alias in [name, *entry.get("aliases", [])]:
            key = match_key(alias)
            if key:
                index[key].add(name)
    excluded = {match_key(name) for name in dictionary["excluded"]}
    return index, excluded


def resolve_person(row: dict[str, str], index: dict[str, set[str]], excluded: set[str]) -> tuple[str, str, str]:
    """Return ``(person, method, note)``.

    A titled span (``Captaine John Smith``) is tried first; an excluded bare
    name (``John Smith``) then stops resolution before the bare name is tried.
    """
    observed = match_key(row.get("observed_span", ""))
    name = match_key(row.get("name_string", ""))
    for field, key in (("observed_span", observed), ("name_string", name)):
        if not key:
            continue
        if key in excluded:
            return "", "excluded", ""
        matches = index.get(key, set())
        if len(matches) == 1:
            return next(iter(matches)), f"exact_alias:{field}", ""
        if len(matches) > 1:
            return "", "ambiguous", "; ".join(sorted(matches))
    return "", "unresolved", ""


def link_people(
    people_csv: Path, titled_csv: Path, dictionary: dict, locator: Locator,
) -> list[dict[str, str]]:
    index, excluded = person_index(dictionary)
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, int, int]] = set()
    for row in read_csv(people_csv):
        start, end = int(row["span_start"]), int(row["span_end"])
        key = (row["page_id"], start, end)
        if key in seen:  # exact duplicates awaiting reconciliation upstream
            continue
        seen.add(key)
        person, method, note = resolve_person(row, index, excluded)
        rows.append({
            "mention_id": row["occurrence_id"], "entity_type": "person",
            "entity_key": person, "entity_name": person, "link_method": method, "link_note": note,
            "observed_span": row["observed_span"], "page_id": row["page_id"],
            "span_start": start, "span_end": end, "sentence_text": " ".join(row.get("sentence_text", "").split()),
            **locator.locate(row["page_id"], start, end),
        })

    # Titled mentions (``Ea: of Southampton``) are added unless a resolved
    # person mention already covers the same text.
    resolved_spans: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in rows:
        if row["entity_key"]:
            resolved_spans[row["page_id"]].append((row["span_start"], row["span_end"], row["entity_key"]))
    for row in read_csv(titled_csv):
        start, end = int(row["span_start"]), int(row["span_end"])
        person = row["suggested_person"]
        if any(s < end and start < e and (not person or p == person)
               for s, e, p in resolved_spans.get(row["page_id"], [])):
            continue
        rows.append({
            "mention_id": row["occurrence_id"], "entity_type": "person",
            "entity_key": person, "entity_name": person,
            "link_method": f"territorial_title:{row['resolution_rule_id']}" if person else "unresolved_title",
            "link_note": row.get("rule_note", ""),
            "observed_span": row["observed_span"], "page_id": row["page_id"],
            "span_start": start, "span_end": end, "sentence_text": " ".join(row.get("sentence_text", "").split()),
            **locator.locate(row["page_id"], start, end),
        })
    return rows


def link_places(places_csv: Path, locator: Locator) -> list[dict[str, str]]:
    rows = []
    for row in read_csv(places_csv):
        start, end = int(row["span_start"]), int(row["span_end"])
        rows.append({
            "mention_id": row["place_occurrence_id"], "entity_type": "place",
            "entity_key": row["place_id"], "entity_name": row["preferred_name"],
            "link_method": f"authority_alias:{row['matching_policy']}", "link_note": row["matched_alias"],
            "observed_span": row["observed_span"], "page_id": row["page_id"],
            "span_start": start, "span_end": end, "sentence_text": " ".join(row.get("sentence_text", "").split()),
            **locator.locate(row["page_id"], start, end),
        })
    return rows


def write(path: Path, rows: list[dict[str, str]]) -> None:
    rows.sort(key=lambda row: (row["page_id"], int(row["span_start"]), row["mention_id"]))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--people", type=Path, required=True)
    parser.add_argument("--titled-people", type=Path, required=True)
    parser.add_argument("--places", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--page-roles", type=Path, required=True)
    parser.add_argument("--editorial-spans", type=Path, required=True)
    parser.add_argument("--people-output", type=Path, required=True)
    parser.add_argument("--places-output", type=Path, required=True)
    args = parser.parse_args()

    locator = Locator(args.segments, args.page_roles, args.editorial_spans)
    people = link_people(args.people, args.titled_people, person_dictionary.load(args.dictionary), locator)
    places = link_places(args.places, locator)
    write(args.people_output, people)
    write(args.places_output, places)

    for label, rows in (("person", people), ("place", places)):
        usable = [row for row in rows if row["usable_for_association"] == "yes"]
        linked = [row for row in usable if row["entity_key"]]
        print(f"{label}: {len(rows)} mentions, {len(usable)} usable, {len(linked)} usable and linked "
              f"({len({row['entity_key'] for row in linked})} distinct)")
    methods: dict[str, int] = defaultdict(int)
    for row in people:
        methods[row["link_method"].split(":")[0]] += 1
    print(f"person link methods: {dict(sorted(methods.items(), key=lambda item: -item[1]))}")


if __name__ == "__main__":
    main()
