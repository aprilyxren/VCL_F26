"""Find people, places, and relations in the clerk's margin notes.

The Court Book margin notes summarize each entry (``A Patent for m‘ Swaine``,
``Capt Roger Smith to haue 30": & y* Comaund of 50 persons``). The re-OCR keeps
them in ``margin_notes`` on each page. This pass runs the same matching as
``build_ner.py`` on each note: authority aliases (people and places), the
title + name and given-name passes, and the relation rules, and dates each
note from its page.

Outputs one row per mention found in a note (``margin_note_mentions.csv``)
and the relations inside notes (``margin_note_relations.csv``).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import person_dictionary
from build_ner import (
    GIVEN_NAMES as GIVEN_FIRST_NAMES,
    GIVEN_SURNAME_RE,
    TITLE_NAME_RE,
    AuthorityMatcher,
    build_entries,
    load_places,
    looks_like_person,
)
from extract_relations import relate, sentence_around
from link_mentions import contextual_index, person_index, resolve_person


# Three or more "title Name." entries: a column of the attendance list that the
# layout split took for a margin note (``m' Steward. m1’ Binge. m' Tomlins.``).
ATTENDANCE_ENTRY_RE = re.compile(r"(?:^|[.\s])(?:m\W{0,2}|mr\.?|s\W|sir|capt\W*|lo\W|dr\W*|d\W)\s*[A-Z][\w’'*:]+")

MENTION_FIELDS = [
    "page_id", "note_index", "note_kind", "label", "entity_key", "entity_name", "observed_span", "span_start", "span_end",
    "date_start", "date_end", "date_confidence", "note_text",
]
RELATION_FIELDS = [
    "relation", "confidence", "subject", "subject_key", "object", "object_key", "cue", "page_id",
    "note_index", "date_start", "note_text",
]


def note_mentions(text: str, matcher: AuthorityMatcher, places: dict, index, excluded, contextual, volume: int) -> list[dict]:
    found = []
    for start, end, label, entity_id in matcher.find(text):
        if label == "NOT_ENTITY":
            continue
        if label == "PLACE":
            found.append({"label": "PLACE", "entity_key": entity_id, "entity_name": places[entity_id]["preferred_name"],
                          "span_start": start, "span_end": end})
        elif entity_id.startswith("CONTEXT:"):
            continue
        else:
            found.append({"label": "PERSON", "entity_key": entity_id, "entity_name": entity_id,
                          "span_start": start, "span_end": end})
    taken = [(m["span_start"], m["span_end"]) for m in found]
    for pattern, titled in ((TITLE_NAME_RE, True), (GIVEN_SURNAME_RE, False)):
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if any(s < end and start < e for s, e in taken):
                continue
            if not titled and match.group(1).casefold() not in GIVEN_FIRST_NAMES:
                continue
            observed = text[start:end]
            if not looks_like_person(observed, titled):
                continue
            person, method, _ = resolve_person({"observed_span": observed, "name_string": observed},
                                                index, excluded, contextual, volume)
            if method == "excluded":
                continue
            found.append({"label": "PERSON", "entity_key": person, "entity_name": person,
                          "span_start": start, "span_end": end})
            taken.append((start, end))
    for m in found:
        m["observed_span"] = text[m["span_start"]:m["span_end"]]
    return sorted(found, key=lambda m: m["span_start"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--place-resolutions", type=Path, required=True)
    parser.add_argument("--page-dates", type=Path, required=True)
    parser.add_argument("--mentions-output", type=Path, required=True)
    parser.add_argument("--relations-output", type=Path, required=True)
    args = parser.parse_args()

    dictionary = person_dictionary.load(args.dictionary)
    entries, _ = build_entries(dictionary, args.authority, args.place_resolutions)
    matcher = AuthorityMatcher(entries)
    places = load_places(args.authority)
    index, excluded = person_index(dictionary)
    contextual = contextual_index(dictionary)
    with args.page_dates.open(encoding="utf-8", newline="") as handle:
        dates = {r["page_id"]: r for r in csv.DictReader(handle)}

    mention_rows, relation_rows = [], []
    for page in json.loads(args.primary.read_text(encoding="utf-8")):
        date = dates.get(page["page_id"], {})
        for note_index, note in enumerate(page.get("margin_notes", [])):
            text = note["text"]
            kind = "attendance_fragment" if len(ATTENDANCE_ENTRY_RE.findall(text)) >= 3 else "summary"
            found = note_mentions(text, matcher, places, index, excluded, contextual, page["volume"])
            for m in found:
                mention_rows.append({
                    "page_id": page["page_id"], "note_index": note_index, "note_kind": kind, **m,
                    "date_start": date.get("start", ""), "date_end": date.get("end", ""),
                    "date_confidence": date.get("confidence", ""), "note_text": text,
                })
            # Relations between neighbouring mentions inside the note.
            rows = [{**m, "span_start": str(m["span_start"]), "span_end": str(m["span_end"])} for m in found]
            for a, b in zip(rows, rows[1:]):
                for subject, obj, relation, confidence, cue in relate(a, b, text):
                    relation_rows.append({
                        "relation": relation, "confidence": confidence,
                        "subject": subject["entity_name"] or subject["observed_span"], "subject_key": subject["entity_key"],
                        "object": obj["entity_name"] or obj["observed_span"], "object_key": obj["entity_key"],
                        "cue": cue, "page_id": page["page_id"], "note_index": note_index,
                        "date_start": date.get("start", ""), "note_text": text,
                    })

    for path, fields, rows in ((args.mentions_output, MENTION_FIELDS, mention_rows),
                               (args.relations_output, RELATION_FIELDS, relation_rows)):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows({f: r.get(f, "") for f in fields} for r in rows)
    labels = Counter(r["label"] for r in mention_rows)
    linked = sum(1 for r in mention_rows if r["label"] == "PERSON" and r["entity_key"])
    print(f"{len(mention_rows)} mentions in margin notes: {dict(labels)}; {linked} people linked; "
          f"by note kind: {dict(Counter(r['note_kind'] for r in mention_rows))}")
    print(f"{len(relation_rows)} relations in margin notes: {dict(Counter(r['relation'] for r in relation_rows))}")


if __name__ == "__main__":
    main()
