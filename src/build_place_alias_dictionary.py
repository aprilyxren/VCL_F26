"""Build a place alias dictionary keyed by standardized place name.

Mirrors ``list_of_records_person_alias_dictionary.json``: each key is a
standardized place name and its value lists observed spellings. Keys start
from the place authority seed (``preferred_name`` plus ``aliases``); curated
resolutions in ``configs/place_alias_resolutions.json`` then attach discovery
queue rows to an existing key or create a new one. Every curated row is
recorded in a resolution log with its basis.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from filter_person_review_queue import normalized_phrase


LOG_FIELDS = [
    "candidate_key", "display_form", "occurrence_count", "action",
    "place_key", "place_type", "world_region", "aliases_attached", "basis",
]


def append_unique(values: list[str], new_values: list[str]) -> list[str]:
    known = {value.casefold() for value in values}
    added = []
    for value in new_values:
        value = " ".join(value.split())
        if value and value.casefold() not in known:
            known.add(value.casefold())
            values.append(value)
            added.append(value)
    return added


REGION_PREFIX = {"new world": "NW", "old world": "OW"}
ALWAYS_QUOTED = {"aliases", "notes"}
DISCOVERY_SOURCE = "The Records of the Virginia Company of London (corpus discovery)"


def format_cell(field: str, value: str) -> str:
    """Match the seed file's style: aliases and notes are always quoted."""
    if field in ALWAYS_QUOTED or any(char in value for char in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def next_place_id(places: list[dict[str, str]], prefix: str) -> str:
    numbers = [
        int(match.group(1))
        for place in places
        if (match := re.fullmatch(rf"{prefix}-(\d+)", place["place_id"]))
    ]
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def default_policy(aliases: list[str]) -> str:
    """Single-word aliases can be surnames or common words, so need context."""
    return "high_confidence_alias" if all(len(alias.split()) > 1 for alias in aliases) else "contextual_alias"


def build(
    authority: Path, queue: Path, resolutions: Path, output: Path, log_output: Path,
    update_authority: bool = False,
) -> None:
    dictionary: dict[str, list[str]] = {}
    with authority.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        authority_fields = list(reader.fieldnames or [])
        places = list(reader)
    for place in places:
        dictionary[place["preferred_name"]] = []
        append_unique(dictionary[place["preferred_name"]], place["aliases"].split("|"))
    in_authority = set(dictionary)

    with queue.open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["candidate_key"]: row for row in csv.DictReader(handle)}

    def queue_row(form: str) -> dict[str, str]:
        key, _ = normalized_phrase(form)
        if key not in rows:
            raise KeyError(f"{form!r} is not in the discovery queue")
        return rows[key]

    config = json.loads(resolutions.read_text(encoding="utf-8"))
    log = []
    for section in ("existing", "new"):
        for place_key, entry in config[section].items():
            if section == "existing" and place_key not in dictionary:
                raise KeyError(f"{place_key!r} is not an authority preferred_name")
            if section == "new" and place_key in dictionary and place_key not in in_authority:
                raise KeyError(f"{place_key!r} is listed twice")
            aliases = dictionary.setdefault(place_key, [])
            # A standardized name becomes an alias only when it is multi-word;
            # bare keys like "Salisbury" or "Middlesex" usually name a peer.
            if section == "new" and len(place_key.split()) > 1:
                append_unique(aliases, [place_key])
            for form in entry.get("forms", []):
                row = queue_row(form)
                observed = row["observed_forms"].split("|")
                append_unique(aliases, observed)
                log.append({
                    "candidate_key": row["candidate_key"],
                    "display_form": row["display_form"],
                    "occurrence_count": row["occurrence_count"],
                    "action": f"{section}_key",
                    "place_key": place_key,
                    "place_type": entry.get("place_type", ""),
                    "world_region": entry.get("world_region", ""),
                    "aliases_attached": "|".join(observed),
                    "basis": entry.get("basis", ""),
                })
            extra = entry.get("extra_aliases", [])
            append_unique(aliases, extra)
            if extra:
                log.append({
                    "action": f"{section}_key_extra_alias", "place_key": place_key,
                    "aliases_attached": "|".join(extra), "basis": entry.get("basis", ""),
                })

    for form, reason in config["excluded"].items():
        row = queue_row(form)
        log.append({
            "candidate_key": row["candidate_key"], "display_form": row["display_form"],
            "occurrence_count": row["occurrence_count"], "action": "excluded", "basis": reason,
        })

    if update_authority:
        by_name = {place["preferred_name"]: place for place in places}
        added_places = 0
        for place_key, entry in config["new"].items():
            if place_key in by_name:
                continue
            region = entry.get("world_region", "")
            place = {field: "" for field in authority_fields}
            place.update({
                "place_id": next_place_id(places, REGION_PREFIX.get(region.casefold(), "OT")),
                "preferred_name": place_key,
                "place_type": entry.get("place_type", ""),
                "world_region": region,
                "period_relevance": "1606-1624",
                "matching_policy": entry.get("matching_policy") or default_policy(dictionary[place_key]),
                "source_title": DISCOVERY_SOURCE,
                "notes": entry.get("basis", ""),
            })
            places.append(place)
            by_name[place_key] = place
            added_places += 1
        for name, place in by_name.items():
            place["aliases"] = "|".join(dictionary[name])
        with authority.open("w", encoding="utf-8", newline="") as handle:
            handle.write(",".join(authority_fields) + "\n")
            for place in places:
                handle.write(",".join(format_cell(field, place.get(field, "")) for field in authority_fields) + "\n")
        print(f"Updated {authority}: {added_places} new places, aliases refreshed for all {len(places)}")

    output.write_text(json.dumps(dictionary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with log_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in LOG_FIELDS} for row in log)

    new_keys = len(config["new"])
    print(f"Wrote {len(dictionary)} place keys ({len(dictionary) - new_keys} from authority, {new_keys} new)")
    print(f"Logged {len(log)} resolution rows ({len(config['excluded'])} excluded)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument(
        "--update-authority", action="store_true",
        help="Also write new places and merged aliases into the authority CSV.",
    )
    args = parser.parse_args()
    build(args.authority, args.queue, args.resolutions, args.output, args.log_output, args.update_authority)
