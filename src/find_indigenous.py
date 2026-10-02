"""Find Indigenous peoples, individuals, titles, and collective terms.

Matching works on a de-hyphenated view of each page (``Pow- hatans`` and
``Pow-\\nhatans`` become ``Powhatans``), with every match mapped back to the
original character offsets. Names are matched by exact seed spelling or, for
names of five or more letters, by similarity after early-modern spelling
normalization (u/v, i/j/y, doubled letters, ck/c/k, plural endings), so the
records' irregular spellings are caught and listed for review.

Names shared with a river, English settlement, or person (``homonym``) are
read as the people only in plural form, in ``King/weroance of X``, or as
``X Indians``; a following River/Riuer/Creek marks a place. Collective terms
(Indians, salvages, heathen, infidels, ...) skip known non-people uses
(``Indian corne``, ``East Indies``, ``natiues of England``).

Outputs: every hit (``indigenous_mentions.csv``) and a grouped spelling list
for review (``indigenous_spelling_review.csv``).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from link_mentions import Locator
from source_loader import load_primary_pages


FUZZY_MIN_LENGTH = 5
FUZZY_THRESHOLD = 0.82
# A fuzzy match must be close in length too (``Iapan`` is not ``Japazaws``).
LENGTH_RATIO = (0.8, 1.25)
PLACE_WORDS = {"river", "riuer", "ryver", "creek", "creeke", "bay", "riuers", "rivers"}
KING_WORDS = {"king", "kinge", "kings", "weroance", "werowance", "weroances", "queene", "queen"}
PEOPLE_WORDS = {"indians", "indyans", "salvages", "sauages", "savages", "people", "nation"}
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ’']+")
HYPHEN_BREAK_RE = re.compile(r"(?<=[A-Za-z])-[ \t]*\n?[ \t]*(?=[a-z])")

FIELDS = [
    "mention_id", "category", "entity_id", "entity_name", "reading", "match_type", "score",
    "observed_span", "matched_form", "page_id", "span_start", "span_end",
    "segment_id", "date_start", "date_end", "date_confidence", "page_role", "editorial_span",
    "usable_for_association", "context",
]
REVIEW_FIELDS = [
    "review_decision", "review_note", "entity_id", "entity_name", "category", "spelling",
    "match_type", "best_score", "occurrences", "readings", "sample_context",
]


def dehyphenate(text: str) -> tuple[str, list[int]]:
    """Join line-break hyphenation; return the joined text and, for each of
    its characters, the offset in the original text."""
    joined: list[str] = []
    mapping: list[int] = []
    position = 0
    for match in HYPHEN_BREAK_RE.finditer(text):
        for index in range(position, match.start()):
            joined.append(text[index])
            mapping.append(index)
        position = match.end()
    for index in range(position, len(text)):
        joined.append(text[index])
        mapping.append(index)
    mapping.append(len(text))
    return "".join(joined), mapping


def skeleton(word: str) -> str:
    """Spelling-insensitive key: Powhatanes / POWHATANS / Pouhatan -> powhatan."""
    value = re.sub(r"[^a-z]", "", word.casefold().replace("’s", "").replace("'s", ""))
    value = value.replace("v", "u").replace("j", "i").replace("y", "i").replace("w", "u")
    value = value.replace("ph", "f").replace("ck", "k").replace("q", "k")
    value = re.sub(r"c(?=[aoukl])", "k", value)
    value = re.sub(r"(.)\1+", r"\1", value)
    for suffix, replacement in (("ies", "i"), ("es", ""), ("s", "")):
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            return value[: -len(suffix)] + replacement
    return value


class Lexicon:
    def __init__(self, config: dict) -> None:
        self.entries: list[dict] = []  # one per seed spelling
        for category, section in (("group", "groups"), ("person", "people"), ("title", "titles")):
            for entity_id, entry in config[section].items():
                for seed in entry["seeds"]:
                    self.entries.append({
                        "category": category, "entity_id": entity_id,
                        "entity_name": entry.get("name", entity_id), "seed": seed,
                        "homonym": entry.get("homonym", ""), "words": len(seed.split()),
                        "skeleton": skeleton(seed), "plural_only": entry.get("plural_only", False),
                        "exact_only": entry.get("exact_only", False),
                    })
        self.single = [e for e in self.entries if e["words"] == 1]
        self.multi = [e for e in self.entries if e["words"] > 1]
        self.by_skeleton: dict[str, list[dict]] = defaultdict(list)
        for entry in self.single:
            self.by_skeleton[entry["skeleton"]].append(entry)
        self.collective = []
        for term, entry in config["collective_terms"].items():
            for seed in entry["seeds"]:
                self.collective.append((term, seed, entry["context_required"]))
        self.exclusions = [e.casefold() for e in config["collective_exclusions"]]

    def match_word(self, word: str) -> tuple[dict, str, float] | None:
        key = skeleton(word)
        if key in self.by_skeleton:
            candidates = self.by_skeleton[key]
            exact = [e for e in candidates if word.casefold() == e["seed"].casefold()]
            if exact:
                return exact[0], "exact", 1.0
            variants = [e for e in candidates if not e["exact_only"]]
            if variants:
                return variants[0], "spelling_variant", 1.0
            return None
        if len(key) < FUZZY_MIN_LENGTH:
            return None
        best, best_score = None, 0.0
        for entry in self.single:
            if entry["exact_only"] or len(entry["skeleton"]) < FUZZY_MIN_LENGTH or entry["skeleton"][0] != key[0]:
                continue
            ratio = len(key) / len(entry["skeleton"])
            if not LENGTH_RATIO[0] <= ratio <= LENGTH_RATIO[1]:
                continue
            score = SequenceMatcher(None, key, entry["skeleton"]).ratio()
            if score > best_score:
                best, best_score = entry, score
        if best and best_score >= FUZZY_THRESHOLD:
            return best, "fuzzy", round(best_score, 3)
        return None


def reading_for(entry: dict, word: str, before: list[str], after: list[str]) -> str:
    # ``Choapooks Creek``, ``Pamunkey River``: a following place word makes it a place.
    if entry["category"] in {"group", "person"} and after and after[0].casefold() in PLACE_WORDS:
        return "place_homonym"
    if entry["category"] != "group":
        return entry["category"]
    plural = re.search(r"(?i)(?:s|es|ies)$", word) and not re.search(r"(?i)(?:ss)$", word)
    if entry["plural_only"] and not plural:
        return "place_homonym"  # singular Kiccowtan / Warrascoyack is the English settlement
    if not entry["homonym"]:
        return "group"
    king = any(w.casefold() in KING_WORDS for w in before[-3:]) and "of" in [w.casefold() for w in before[-2:]]
    people = after and after[0].casefold() in PEOPLE_WORDS
    if plural or king or people:
        return "group"
    return "group_or_homonym"


def find(pages: list[dict], lexicon: Lexicon, locator: Locator) -> list[dict]:
    rows: list[dict] = []
    for page in pages:
        if locator.roles.get(page["page_id"], "record") != "record":
            continue
        text = page["text"]
        joined, mapping = dehyphenate(text)
        claimed: list[tuple[int, int]] = []

        def emit(j_start: int, j_end: int, **fields) -> None:
            if any(s < j_end and j_start < e for s, e in claimed):
                return
            claimed.append((j_start, j_end))
            start, end = mapping[j_start], mapping[j_end - 1] + 1
            row = {
                "page_id": page["page_id"], "span_start": start, "span_end": end,
                "observed_span": text[start:end],
                "context": " ".join(joined[max(0, j_start - 80): j_end + 80].split()),
                "mention_id": f"{page['page_id']}-I{start:05d}-{end:05d}",
                **fields,
            }
            row.update(locator.locate(page["page_id"], start, end))
            rows.append(row)

        # 1. Multi-word names, collective exclusions, then collective terms.
        for entry in lexicon.multi:
            pattern = r"(?i)(?<![A-Za-z])" + r"\s+".join(map(re.escape, entry["seed"].split())) + r"(?![A-Za-z])"
            for m in re.finditer(pattern, joined):
                emit(m.start(), m.end(), category=entry["category"], entity_id=entry["entity_id"],
                     entity_name=entry["entity_name"], reading=entry["category"], match_type="exact",
                     score=1.0, matched_form=entry["seed"])
        for exclusion in lexicon.exclusions:
            pattern = r"(?i)(?<![A-Za-z])" + r"\s+".join(map(re.escape, exclusion.split())) + r"(?![A-Za-z])"
            for m in re.finditer(pattern, joined):
                claimed.append((m.start(), m.end()))
        for term, seed, context_required in lexicon.collective:
            pattern = r"(?i)(?<![A-Za-z])" + r"\s+".join(map(re.escape, seed.split())) + r"(?![A-Za-z])"
            for m in re.finditer(pattern, joined):
                emit(m.start(), m.end(), category="collective", entity_id=term, entity_name=term,
                     reading="contextual" if context_required else "collective", match_type="exact",
                     score=1.0, matched_form=seed)

        # 2. Single-word names: exact, spelling variants, and fuzzy matches.
        words = list(WORD_RE.finditer(joined))
        tokens = [w.group(0) for w in words]
        for index, word_match in enumerate(words):
            word = word_match.group(0).strip("’'")
            if len(word) < 4 or not word[0].isupper():
                continue
            found = lexicon.match_word(word)
            if not found:
                continue
            entry, match_type, score = found
            if entry["category"] != "title" and not word[0].isupper():
                continue
            before, after = tokens[max(0, index - 3):index], tokens[index + 1:index + 2]
            emit(word_match.start(), word_match.start() + len(word), category=entry["category"],
                 entity_id=entry["entity_id"], entity_name=entry["entity_name"],
                 reading=reading_for(entry, word, before, after), match_type=match_type, score=score,
                 matched_form=entry["seed"])
        # Titles are often lowercase (``the weroance of``).
        for index, word_match in enumerate(words):
            word = word_match.group(0)
            found = lexicon.match_word(word) if word[:1].islower() else None
            if found and found[0]["category"] == "title" and found[2] >= 0.9:
                entry, match_type, score = found
                emit(word_match.start(), word_match.end(), category="title", entity_id=entry["entity_id"],
                     entity_name=entry["entity_name"], reading="title", match_type=match_type, score=score,
                     matched_form=entry["seed"])
    return rows


def spelling_review(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row["category"] == "collective":
            continue
        spelling = " ".join(row["observed_span"].split()).replace("- ", "").replace("-", "")
        key = (row["entity_id"], spelling.casefold())
        group = groups.setdefault(key, {
            "entity_id": row["entity_id"], "entity_name": row["entity_name"], "category": row["category"],
            "spelling": spelling, "match_type": row["match_type"], "best_score": row["score"],
            "occurrences": 0, "readings": Counter(), "sample_context": row["context"][:220],
        })
        group["occurrences"] += 1
        group["readings"][row["reading"]] += 1
        group["best_score"] = max(group["best_score"], row["score"])
    out = []
    for group in groups.values():
        group["readings"] = "|".join(f"{k}:{v}" for k, v in group["readings"].most_common())
        out.append({"review_decision": "", "review_note": "", **group})
    # Fuzzy spellings first (they need a decision), then by entity and frequency.
    out.sort(key=lambda g: (g["match_type"] != "fuzzy", g["entity_id"], -g["occurrences"]))
    return out


def write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--page-roles", type=Path, required=True)
    parser.add_argument("--editorial-spans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, required=True)
    args = parser.parse_args()

    lexicon = Lexicon(json.loads(args.authority.read_text(encoding="utf-8")))
    locator = Locator(args.segments, args.page_roles, args.editorial_spans)
    rows = find(load_primary_pages(args.source), lexicon, locator)
    rows.sort(key=lambda row: (row["page_id"], row["span_start"]))
    write(args.output, FIELDS, rows)
    review = spelling_review(rows)
    write(args.review_output, REVIEW_FIELDS, review)

    print(f"{len(rows)} mentions on record pages")
    print("  by category:", dict(Counter(r["category"] for r in rows)))
    print("  by reading:", dict(Counter(r["reading"] for r in rows)))
    print("  by match type:", dict(Counter(r["match_type"] for r in rows)))
    print(f"{len(review)} distinct spellings for review "
          f"({sum(1 for g in review if g['match_type'] == 'fuzzy')} fuzzy)")


if __name__ == "__main__":
    main()
