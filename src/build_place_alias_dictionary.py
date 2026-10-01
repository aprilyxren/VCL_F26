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
from pathlib import Path

from filter_person_review_queue import normalized_phrase


LOG_FIELDS = [
    "candidate_key", "display_form", "occurrence_count", "action",
    "place_key", "place_type", "world_region", "aliases_added", "basis",
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


def build(authority: Path, queue: Path, resolutions: Path, output: Path, log_output: Path) -> None:
    dictionary: dict[str, list[str]] = {}
    with authority.open(encoding="utf-8-sig", newline="") as handle:
        for place in csv.DictReader(handle):
            dictionary[place["preferred_name"]] = []
            append_unique(dictionary[place["preferred_name"]], place["aliases"].split("|"))

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
            if section == "new" and place_key in dictionary:
                raise KeyError(f"{place_key!r} already exists; list it under 'existing'")
            aliases = dictionary.setdefault(place_key, [])
            if section == "new":
                append_unique(aliases, [place_key])
            for form in entry.get("forms", []):
                row = queue_row(form)
                added = append_unique(aliases, row["observed_forms"].split("|"))
                log.append({
                    "candidate_key": row["candidate_key"],
                    "display_form": row["display_form"],
                    "occurrence_count": row["occurrence_count"],
                    "action": f"{section}_key",
                    "place_key": place_key,
                    "place_type": entry.get("place_type", ""),
                    "world_region": entry.get("world_region", ""),
                    "aliases_added": "|".join(added),
                    "basis": entry.get("basis", ""),
                })
            extra = append_unique(aliases, entry.get("extra_aliases", []))
            if extra:
                log.append({
                    "action": f"{section}_key_extra_alias", "place_key": place_key,
                    "aliases_added": "|".join(extra), "basis": entry.get("basis", ""),
                })

    for form, reason in config["excluded"].items():
        row = queue_row(form)
        log.append({
            "candidate_key": row["candidate_key"], "display_form": row["display_form"],
            "occurrence_count": row["occurrence_count"], "action": "excluded", "basis": reason,
        })

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
    args = parser.parse_args()
    build(args.authority, args.queue, args.resolutions, args.output, args.log_output)
