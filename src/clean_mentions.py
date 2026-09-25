"""Conservatively trim overlong person spans from the seed extraction."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][\w'’.-]*"
NAME_RE = re.compile(
    rf"^{NAME_TOKEN}(?:\s+(?:{NAME_TOKEN}|of|the|de|du|van|von)){{0,4}}"
)
TITLE_NAME_RE = re.compile(
    rf"^(?i:captain|captaine|capt\.?|sir|lady|lord|chief|king|queen|"
    rf"weroance|doctor|dr\.?|master|mistress|mr\.?|mrs\.?|governor|[mM][’'*ᵣ]+)"
    rf"[\s,;:\-—–]+{NAME_TOKEN}(?:\s+(?:{NAME_TOKEN}|of|the|de|du|van|von)){{0,4}}"
)


TITLES = {
    "captain", "captaine", "capt", "capt.", "sir", "lady", "lord",
    "chief", "king", "queen", "weroance", "doctor", "dr", "dr.",
    "master", "mistress", "mr", "mr.", "mrs", "mrs.", "governor",
    "m'", "m’", "m*", "m**", "mᵣ",
}

HISTORICAL_ROLE_LABELS = {
    "counsel": "ORG_OR_COMMITTEE",
    "counsell": "ORG_OR_COMMITTEE",
    "councell": "ORG_OR_COMMITTEE",
    "councel": "ORG_OR_COMMITTEE",
    "deputy": "TITLE_OR_ROLE",
    "deputie": "TITLE_OR_ROLE",
    "nos": "EDITORIAL_REFERENCE",
    "nos.": "EDITORIAL_REFERENCE",
    "treasurer": "TITLE_OR_ROLE",
    "majesty": "TITLE_OR_ROLE",
    "ibid": "NOT_ENTITY",
    "statutes": "NOT_ENTITY",
    "ma": "TITLE_OR_ROLE",
    "generall": "TITLE_OR_ROLE",
    "kinge": "TITLE_OR_ROLE",
    "nauie": "NOT_ENTITY",
    "labo": "NOT_ENTITY",
}


def parse_title_name(value: str) -> tuple[str | None, str]:
    match = re.match(r"(?i)^((?:[A-Za-z.]+|[mM][’'*ᵣ]+))[\s,;:\-—–]+(.+)$", value.strip())
    if not match or match.group(1).lower() not in TITLES:
        return None, value.strip()
    observed_title = match.group(1)
    normalized_title = "Mr" if observed_title.casefold() in {"m'", "m’", "m*", "m**", "mᵣ"} else observed_title
    return normalized_title, match.group(2).strip(" ,;:")


def trim_span(observed: str) -> str:
    value = observed.strip(" \t\n\r.,;:!?()[]{}\"“”")
    match = TITLE_NAME_RE.match(value) or NAME_RE.match(value)
    if not match:
        return value
    return match.group(0).rstrip(" .,:;!?)]}")


def clean_mentions(source: Path, output: Path) -> int:
    mentions = json.load(source.open(encoding="utf-8"))
    changed = 0
    for mention in mentions:
        original = mention["observed_span"]
        cleaned = trim_span(original)
        mention["raw_extracted_span"] = original
        mention["span_was_trimmed"] = cleaned != original
        if cleaned != original:
            changed += 1
            relative_start = original.find(cleaned)
            if relative_start < 0:
                raise ValueError(f"Cleaned span is not contained in original: {original!r}")
            mention["span_start"] += relative_start
            mention["observed_span"] = cleaned
            mention["span_end"] = mention["span_start"] + len(cleaned)
            mention["occurrence_id"] = (
                f"{mention['page_id']}-M{mention['span_start']:05d}-"
                f"{mention['span_end']:05d}"
            )
        title, name_string = parse_title_name(mention["observed_span"])
        mention["title"] = title
        mention["name_string"] = name_string
        mention["mention_kind"] = "title_surname" if title else "explicit_name"
        lexical_label = HISTORICAL_ROLE_LABELS.get(mention["observed_span"].strip().casefold())
        if lexical_label:
            mention["label"] = lexical_label
        elif len(name_string.split()) == 1:
            mention["label"] = "PERSON_REF"
            mention["mention_kind"] = "surname_only" if not title else "title_surname"
        else:
            mention["label"] = "PERSON_REF" if title else "PERSON_NAME"
        if lexical_label:
            mention["mention_kind"] = "role_reference" if lexical_label == "TITLE_OR_ROLE" else "relational_reference"
            if lexical_label in {"TITLE_OR_ROLE", "EDITORIAL_REFERENCE", "NOT_ENTITY"}:
                mention["title"] = "Deputy" if mention["observed_span"].strip().casefold() in {"deputy", "deputie"} else (mention["observed_span"] if lexical_label == "TITLE_OR_ROLE" else None)
                mention["name_string"] = None
        elif title and name_string.casefold() in {"the", "one", "of"}:
            mention["label"] = "TITLE_OR_ROLE"
            mention["mention_kind"] = "role_reference"
            mention["name_string"] = None
        mention["cleaning_status"] = "trimmed" if cleaned != original else "unchanged"

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(mentions, handle, ensure_ascii=False, indent=2)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"Trimmed {clean_mentions(args.source, args.output)} spans")


if __name__ == "__main__":
    main()
