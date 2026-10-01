"""Discover place names in the primary text that the place authority lacks.

Two sources feed one grouped review queue:

* spaCy ``GPE``/``LOC``/``FAC`` entities, which ``extract_mentions.py`` discards;
* corpus-shaped geographic patterns such as ``Martins Hundred``, ``Isle of X``,
  ``X Riuer``, and ``Cape X``.

Nothing here edits the authority. Each candidate is grouped by a normalized
form so that spelling variants can be accepted, rejected, or merged into an
existing place in one row. Place aliases preceded by a peerage or civic title
(``Earle of Southampton``) are skipped because they name people.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import spacy

import person_dictionary
from filter_person_review_queue import load_place_aliases, normalized_phrase
from scan_place_candidates import sentence_context
from source_loader import load_primary_pages
from territorial_titles import territorial_title_before


SPACY_LABELS = {"GPE", "LOC", "FAC"}

NAME = r"[A-Z][A-Za-z'’]+"
SUFFIX_KINDS = (
    r"Hundred|Hund|Plantation|Plantaéon|Plantacon|River|Riuer|Ryver|Creeke|Creek|"
    r"Towne|Town|Citty|Cittie|City|Island|Iland|Isle|Ile|Bay|Point|Neck|Shire|Sheire|"
    r"Forte|Fort|Harbour|Harbor|Parish|Parishe"
)
PREFIX_KINDS = r"Isle|Ile|Iland|Island|Cape|Point|Fort|Forte|Port|County|Countie|Citty|Cittie|City|Bay|River|Riuer"

# A name token inside a suffix pattern may not itself be a place kind, so
# ``Martins Hundred and Mulberry Island`` yields two hits, not one.
NAME_NOT_KIND = rf"(?!(?i:{SUFFIX_KINDS})(?![\w])){NAME}"
SUFFIX_RE = re.compile(
    rf"(?<![\w'’])(?P<name>{NAME_NOT_KIND}(?:[ \t]+(?:and|&)[ \t]+{NAME_NOT_KIND}|[ \t\-]+{NAME_NOT_KIND}){{0,2}})"
    rf"\s+(?P<kind>(?i:{SUFFIX_KINDS}))(?![\w])"
)
PREFIX_OF_RE = re.compile(
    rf"(?<![\w'’])(?P<kind>{PREFIX_KINDS})\s+of\s+(?:the\s+)?(?P<name>{NAME}(?:[ \t]+{NAME})?)"
)
PREFIX_RE = re.compile(rf"(?<![\w'’])(?P<kind>Cape|Point|Fort|Forte|Port)\s+(?P<name>{NAME})")

# Leading words that make a pattern hit prose rather than a name. They are
# stripped; a hit with nothing left is discarded.
LEADING_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "his", "in", "into", "its",
    "my", "of", "said", "same", "that", "the", "their", "this", "to", "vpon",
    "upon", "whole", "within", "y", "yt", "ye", "our", "your", "theire",
}
NAME_STOPWORDS = {
    "company", "companie", "court", "counsell", "councell", "colony", "colonie",
    "generall", "virginia", "england", "london", "majesty", "ma", "lord", "lo",
    "sir", "captaine", "captain", "capt", "master", "mr", "same", "said",
    "first", "second", "third", "great", "new", "old", "north", "south", "east",
    "west", "upper", "nether", "see", "nowe", "sent", "alias", "for",
    "one", "two", "three", "foure", "four", "five", "fiue", "six", "sixe",
    "seven", "seauen", "eight", "nine", "tenn", "ten", "twenty", "hundred",
}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

FIELDS = [
    "review_decision",
    "preferred_name",
    "place_type",
    "world_region",
    "matching_policy",
    "merge_into_place_id",
    "aliases_to_add",
    "review_note",
    "confidence",
    "suggested_merge_place_id",
    "candidate_key",
    "display_form",
    "observed_forms",
    "occurrence_count",
    "page_count",
    "volumes",
    "sources",
    "pattern_kinds",
    "in_authority_place_id",
    "person_name_conflict",
    "surname_conflict",
    "common_word",
    "first_occurrence",
    "sample_contexts",
]


def leading_stopword_length(value: str) -> int:
    """Return how many characters of leading stopwords to drop."""
    offset = 0
    while True:
        token = re.match(r"(\S+)\s+", value[offset:])
        if not token or token.group(1).casefold().strip(".,:;") not in LEADING_STOPWORDS:
            return offset
        offset += token.end()


def pattern_hits(text: str) -> list[tuple[int, int, str, str]]:
    """Return ``(start, end, observed, kind)`` for geographic name shapes."""
    hits = []
    for match in SUFFIX_RE.finditer(text):
        start = match.start("name") + leading_stopword_length(match.group("name"))
        name = text[start:match.end("name")]
        if not name or all(
            token.casefold() in NAME_STOPWORDS | LEADING_STOPWORDS for token in name.split()
        ):
            continue
        hits.append((start, match.end(), text[start:match.end()], match.group("kind").casefold()))
    for pattern in (PREFIX_OF_RE, PREFIX_RE):
        for match in pattern.finditer(text):
            if match.group("name").casefold() in NAME_STOPWORDS:
                continue
            hits.append((match.start(), match.end(), match.group(0), match.group("kind").casefold()))
    return hits


def load_person_forms(path: Path | None) -> set[str]:
    if path is None:
        return set()
    forms = set()
    for person, aliases in person_dictionary.aliases_by_person(person_dictionary.load(path)).items():
        for value in [person, *aliases]:
            phrase, _ = normalized_phrase(value)
            if phrase:
                forms.add(phrase)
    return forms


def load_surnames(path: Path | None, minimum: int = 2) -> set[str]:
    """Surnames seen at least ``minimum`` times among definite people."""
    if path is None:
        return set()
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            phrase, _ = normalized_phrase(row.get("matched_last_name", ""))
            if phrase:
                counts[phrase] += 1
    return {name for name, count in counts.items() if count >= minimum}


def word_case_counts(pages: list[dict]) -> tuple[Counter[str], Counter[str]]:
    lower: Counter[str] = Counter()
    upper: Counter[str] = Counter()
    for page in pages:
        for word in re.findall(r"[A-Za-z]+", page["text"]):
            (upper if word[:1].isupper() else lower)[word.casefold()] += 1
    return lower, upper


def suggest_merge(key: str, place_forms: dict[str, set[tuple[str, str, str]]]) -> str:
    """Closest authority alias by spelling, for variants like ``Iames Citty``."""
    best_ratio, best_id = 0.0, ""
    for alias, hits in place_forms.items():
        if abs(len(alias) - len(key)) > 4:
            continue
        ratio = SequenceMatcher(None, key, alias).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, "|".join(sorted(place_id for place_id, _, _ in hits))
    return best_id if best_ratio >= 0.84 else ""


def confidence(row: dict[str, str]) -> str:
    if "pattern" in row["sources"]:
        return "high"
    if row["person_name_conflict"] or row["surname_conflict"] or row["common_word"]:
        return "low"
    return "medium" if int(row["occurrence_count"]) >= 2 else "low"


def discover(
    source: Path,
    authority: Path,
    output: Path,
    person_dictionary: Path | None = None,
    person_names: Path | None = None,
    model_name: str = "en_core_web_sm",
    min_count: int = 1,
) -> int:
    pages = load_primary_pages(source)
    place_forms = load_place_aliases(authority)
    person_forms = load_person_forms(person_dictionary)
    surnames = load_surnames(person_names)
    lower_counts, upper_counts = word_case_counts(pages)
    nlp = spacy.load(model_name, disable=["lemmatizer"])

    groups: dict[str, dict] = defaultdict(lambda: {
        "forms": Counter(), "pages": set(), "volumes": set(), "sources": Counter(),
        "kinds": Counter(), "contexts": [], "first": "", "count": 0,
    })

    texts = (page["text"] for page in pages)
    for page, doc in zip(pages, nlp.pipe(texts, batch_size=32)):
        text = page["text"]
        page_hits: dict[tuple[int, int], tuple[str, str]] = {}
        for ent in doc.ents:
            if ent.label_ in SPACY_LABELS:
                page_hits[(ent.start_char, ent.end_char)] = (f"spacy_{ent.label_}", "")
        for start, end, _, kind in pattern_hits(text):
            source_name, _ = page_hits.get((start, end), ("", ""))
            page_hits[(start, end)] = ("pattern" if not source_name else f"{source_name}+pattern", kind)

        for (start, end), (source_name, kind) in page_hits.items():
            observed = " ".join(text[start:end].split()).strip(" .,:;()[]")
            key, tokens = normalized_phrase(observed)
            if not key or len(key) < 3 or not tokens[0][:1].isalpha():
                continue
            if not observed[:1].isupper():
                continue
            if territorial_title_before(text, start) is not None:
                continue
            group = groups[key]
            group["count"] += 1
            group["forms"][observed] += 1
            group["pages"].add(page["page_id"])
            group["volumes"].add(page["volume"])
            group["sources"][source_name] += 1
            if kind:
                group["kinds"][kind] += 1
            if not group["first"]:
                group["first"] = f"{page['page_id']}:{start}"
            if len(group["contexts"]) < 3:
                group["contexts"].append(" ".join(sentence_context(text, start, end).split())[:240])

    rows = []
    for key, group in groups.items():
        if group["count"] < min_count:
            continue
        hits = place_forms.get(key, set())
        tokens = key.split()
        row = {
            "candidate_key": key,
            "display_form": group["forms"].most_common(1)[0][0],
            "observed_forms": "|".join(form for form, _ in group["forms"].most_common(8)),
            "occurrence_count": str(group["count"]),
            "page_count": str(len(group["pages"])),
            "volumes": ",".join(str(volume) for volume in sorted(group["volumes"])),
            "sources": "|".join(f"{name}:{n}" for name, n in group["sources"].most_common()),
            "pattern_kinds": "|".join(kind for kind, _ in group["kinds"].most_common()),
            "in_authority_place_id": "|".join(sorted(place_id for place_id, _, _ in hits)),
            "person_name_conflict": "yes" if key in person_forms else "",
            "surname_conflict": "yes" if len(tokens) == 1 and key in surnames else "",
            "common_word": "yes" if len(tokens) == 1 and lower_counts[key] >= upper_counts[key] else "",
            "suggested_merge_place_id": "" if hits else suggest_merge(key, place_forms),
            "first_occurrence": group["first"],
            "sample_contexts": " || ".join(group["contexts"]),
        }
        row["confidence"] = confidence(row)
        rows.append(row)
    # New names first, then by confidence and frequency.
    rows.sort(key=lambda row: (
        bool(row["in_authority_place_id"]),
        CONFIDENCE_ORDER[row["confidence"]],
        -int(row["occurrence_count"]),
        row["candidate_key"],
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--person-dictionary", type=Path)
    parser.add_argument("--person-names", type=Path, help="Definite-people CSV used to flag surnames.")
    parser.add_argument("--min-count", type=int, default=1)
    args = parser.parse_args()
    count = discover(args.source, args.authority, args.output, args.person_dictionary, args.person_names, min_count=args.min_count)
    print(f"Wrote {count} grouped place candidates")
