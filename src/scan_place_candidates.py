"""Scan primary text and person-review rows for authority-seeded place candidates.

This is deliberately a candidate generator, not an entity resolver.  A place
match is recorded with its observed form and source location.  Ambiguous names
such as ``London`` or ``Virginia`` remain reviewable and are not removed from
the person queue automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from territorial_titles import signature_allowed, suggest_person, territorial_title_before, UNRESOLVED_NOTES


PLACE_FIELDS = [
    "review_decision",
    "manual_place_name",
    "place_type_override",
    "world_region_override",
    "review_note",
    "place_occurrence_id",
    "place_id",
    "preferred_name",
    "matched_alias",
    "observed_span",
    "place_type",
    "world_region",
    "matching_policy",
    "label",
    "resolution_status",
    "page_id",
    "section_id",
    "source_id",
    "span_start",
    "span_end",
    "sentence_text",
]

PERSON_PLACE_FIELDS = [
    "review_decision",
    "manual_place_name",
    "review_note",
    "place_id",
    "preferred_name",
    "matched_alias",
    "person_occurrence_id",
    "observed_span",
    "name_string",
    "label",
    "mention_kind",
    "page_id",
    "section_id",
    "source_id",
    "span_start",
    "span_end",
    "sentence_text",
    "place_match_scope",
    "matching_policy",
]


TITLED_PERSON_FIELDS = [
    "review_decision",
    "manual_person_name",
    "review_note",
    "occurrence_id",
    "observed_span",
    "title",
    "title_class",
    "territory_place_id",
    "territory_name",
    "matched_alias",
    "label",
    "mention_kind",
    "suggested_person",
    "resolution_status",
    "resolution_rule_id",
    "rule_note",
    "page_id",
    "section_id",
    "source_id",
    "volume",
    "span_start",
    "span_end",
    "sentence_text",
]


@dataclass(frozen=True)
class Place:
    place_id: str
    preferred_name: str
    place_type: str
    world_region: str
    aliases: tuple[str, ...]
    matching_policy: str


def read_places(path: Path) -> list[Place]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Place(
                place_id=row["place_id"],
                preferred_name=row["preferred_name"],
                place_type=row["place_type"],
                world_region=row["world_region"],
                aliases=tuple(alias.strip() for alias in row["aliases"].split("|") if alias.strip()),
                matching_policy=row["matching_policy"],
            )
            for row in csv.DictReader(handle)
        ]


def alias_pattern(alias: str) -> re.Pattern[str]:
    # Word boundaries do not behave well with apostrophes, OCR punctuation, or
    # aliases ending in a letter.  These guards avoid matching inside words.
    # Multi-word aliases may be split across OCR line breaks.
    body = r"\s+".join(re.escape(part) for part in alias.split())
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


def sentence_context(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n\n", 0, start), text.rfind(".", 0, start), text.rfind(";", 0, start))
    right_candidates = [value for value in (text.find("\n\n", end), text.find(".", end), text.find(";", end)) if value >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right + 1].strip()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def page_matches(text: str, places: list[Place]) -> list[tuple[int, int, Place, str]]:
    """Return alias matches, keeping only the longest of nested matches."""
    found = []
    for place in places:
        for alias in place.aliases:
            for match in alias_pattern(alias).finditer(text):
                found.append((match.start(), match.end(), place, alias))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, Place, str]] = []
    for start, end, place, alias in found:
        if any(k_start <= start and end <= k_end for k_start, k_end, _, _ in kept):
            continue
        kept.append((start, end, place, alias))
    return kept


def titled_person_row(
    page: dict, text: str, start: int, end: int, place: Place, alias: str, title
) -> dict[str, str]:
    rule = suggest_person(place.place_id, title.title_class, page.get("volume"))
    if rule is not None:
        status, person, rule_id, note = "suggested", rule.person, rule.rule_id, rule.note
    else:
        status, person, rule_id = "unresolved", "", ""
        note = UNRESOLVED_NOTES.get((place.place_id, title.title_class), "")
    return {
        "occurrence_id": f"{page['page_id']}-T{title.start:05d}-{end:05d}",
        "observed_span": text[title.start:end],
        "title": title.title,
        "title_class": title.title_class,
        "territory_place_id": place.place_id,
        "territory_name": place.preferred_name,
        "matched_alias": alias,
        "label": "PERSON_REF",
        "mention_kind": "role_reference" if title.title_class == "mayor" else "territorial_title",
        "suggested_person": person,
        "resolution_status": status,
        "resolution_rule_id": rule_id,
        "rule_note": note,
        "page_id": page["page_id"],
        "section_id": page["section_id"],
        "source_id": page["source_id"],
        "volume": str(page.get("volume", "")),
        "span_start": str(title.start),
        "span_end": str(end),
        "sentence_text": sentence_context(text, title.start, end),
    }


def scan_primary(source: Path, places: list[Place]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    pages = json.loads(source.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    titled_rows: list[dict[str, str]] = []
    for page in pages:
        text = page.get("text", "")
        for start, end, place, alias in page_matches(text, places):
            title = territorial_title_before(text, start, signature_allowed(place.place_id))
            if title is not None:
                titled_rows.append(titled_person_row(page, text, start, end, place, alias, title))
                continue
            rows.append({
                "place_occurrence_id": f"{page['page_id']}-L{start:05d}-{end:05d}",
                "place_id": place.place_id,
                "preferred_name": place.preferred_name,
                "matched_alias": alias,
                "observed_span": text[start:end],
                "place_type": place.place_type,
                "world_region": place.world_region,
                "matching_policy": place.matching_policy,
                "label": "PLACE_CANDIDATE",
                "resolution_status": "unreviewed",
                "page_id": page["page_id"],
                "section_id": page["section_id"],
                "source_id": page["source_id"],
                "span_start": str(start),
                "span_end": str(end),
                "sentence_text": sentence_context(text, start, end),
            })
    rows.sort(key=lambda row: (row["page_id"], int(row["span_start"]), row["place_id"], row["matched_alias"]))
    titled_rows.sort(key=lambda row: (row["page_id"], int(row["span_start"])))
    return rows, titled_rows


def untitled_alias_match(alias: str, place: Place, observed: str, name_string: str):
    """Find a place alias that is not part of a title such as ``Lord of X``.

    ``name_string`` is only searched when the observed span has no match, so a
    parsed remainder like ``of Southampton`` cannot re-flag a titled span.
    """
    pattern = alias_pattern(alias)
    allow_signature = signature_allowed(place.place_id)
    observed_matches = list(pattern.finditer(observed))
    for match in observed_matches:
        if territorial_title_before(observed, match.start(), allow_signature) is None:
            return match
    if observed_matches:
        return None
    return pattern.search(name_string)


def scan_person_queue(source: Path, places: list[Place]) -> list[dict[str, str]]:
    with source.open(encoding="utf-8", newline="") as handle:
        person_rows = list(csv.DictReader(handle))
    rows: list[dict[str, str]] = []
    for person in person_rows:
        observed = person.get("observed_span", "")
        for place in places:
            for alias in place.aliases:
                match = untitled_alias_match(alias, place, observed, person.get("name_string", ""))
                if not match:
                    continue
                rows.append({
                    "review_decision": "",
                    "manual_place_name": "",
                    "review_note": "",
                    "place_id": place.place_id,
                    "preferred_name": place.preferred_name,
                    "matched_alias": match.group(0),
                    "person_occurrence_id": person.get("occurrence_id", ""),
                    "observed_span": person.get("observed_span", ""),
                    "name_string": person.get("name_string", ""),
                    "label": person.get("label", ""),
                    "mention_kind": person.get("mention_kind", ""),
                    "page_id": person.get("page_id", ""),
                    "section_id": person.get("section_id", ""),
                    "source_id": person.get("source_id", ""),
                    "span_start": person.get("span_start", ""),
                    "span_end": person.get("span_end", ""),
                    "sentence_text": person.get("sentence_text", ""),
                    "place_match_scope": "observed_span_or_name_string",
                    "matching_policy": place.matching_policy,
                })
                break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--person-queue", type=Path, required=True)
    parser.add_argument("--place-output", type=Path, required=True)
    parser.add_argument("--person-place-output", type=Path, required=True)
    parser.add_argument("--titled-person-output", type=Path, required=True)
    args = parser.parse_args()

    places = read_places(args.authority)
    place_rows, titled_rows = scan_primary(args.source, places)
    person_place_rows = scan_person_queue(args.person_queue, places)
    write_csv(args.place_output, PLACE_FIELDS, place_rows)
    write_csv(args.person_place_output, PERSON_PLACE_FIELDS, person_place_rows)
    write_csv(args.titled_person_output, TITLED_PERSON_FIELDS, titled_rows)
    print(f"Wrote {len(place_rows)} primary-text place candidates")
    print(f"Wrote {len(person_place_rows)} person-queue place candidates")
    print(f"Wrote {len(titled_rows)} titled person mentions (e.g. Earle of Southampton)")


if __name__ == "__main__":
    main()
