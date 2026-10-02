"""Rebuild NER over the primary text using the curated authorities.

Pipeline (spaCy ``en_core_web_sm`` plus an authority matcher placed before ``ner``):

1. Person aliases from the person dictionary become ``PERSON`` entities whose
   ``kb_id`` is the person key; place aliases from the place authority become
   ``PLACE`` entities whose ``kb_id`` is the place ID. Matching is a regex
   (OCR-tolerant whitespace and boundaries, as in ``scan_place_candidates``)
   rather than token phrases, so ``Somer Iland¢`` still matches. Because it
   runs first, the statistical model only labels text the authorities did
   not claim.
2. Known non-entities (ships, offices, archive names, the royal style
   ``England, Scotland, France and Ireland``, cape merchant, ...) become
   ``NOT_ENTITY`` patterns: they block the model and are dropped from output.
3. Post-processing: a ``PLACE`` preceded by a peerage/civic title becomes a
   person (``Earle of Southampton``); single-word places that are also
   surnames (Holland, Downes) need geographic context; model ``PERSON`` spans
   take an adjacent title and are linked by dictionary alias when possible.
4. Index pages are skipped; mentions in running heads and editorial lines
   are kept but marked not usable for association.
5. Each mention carries its meeting segment and date.
6. Optionally, Indigenous groups, individuals, titles, and collective terms
   from ``find_indigenous.py`` are merged in (``INDIGENOUS_GROUP``, ``PERSON``,
   ``INDIGENOUS_TITLE``, ``INDIGENOUS_COLLECTIVE``).

Line breaks are read as spaces (same length, so offsets are unchanged) so
names split across OCR lines still match.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import spacy
from spacy.language import Language
from spacy.util import filter_spans

import person_dictionary
from filter_person_review_queue import (
    NAME_PATTERN,
    NON_NAME_LAST_TOKENS,
    has_geographic_sentence_context,
    is_clear_non_person_phrase,
    normalized_phrase,
    requires_contextual_person_review,
)
from link_mentions import Locator, contextual_index, person_index, resolve_person
from source_loader import load_primary_pages
from territorial_titles import UNRESOLVED_NOTES, signature_allowed, suggest_person, territorial_title_before


FIELDS = [
    "mention_id", "label", "entity_key", "entity_name", "source", "link_note",
    "observed_span", "page_id", "span_start", "span_end",
    "segment_id", "date_start", "date_end", "date_source", "date_confidence",
    "page_role", "editorial_span", "usable_for_association", "sentence_text",
]
MODEL_PLACE_LABELS = {"GPE", "LOC", "FAC"}
# Single-word person aliases that are distinctive enough to match alone.
SINGLE_WORD_PEOPLE = {"pocahontas", "matoaka", "amonute"}
# Exclusion reasons that mean "not a person at all" (vs. "a person we can't identify").
NON_ENTITY_REASON_RE = re.compile(
    r"(?i)^(?:place|ship|office|archive|modern|document|institution|heading|feast|phrase|legal|"
    r"meeting|bod|law|object|latin|royal|placeholder|'your|'his|'excellent|fragment|title only|"
    r"truncated|compound|descriptive|theological|'every|'main|directional|osnaburg|ocr of)"
)
GENERIC_NON_ENTITIES = [
    "Counsell", "Councell", "Counsel", "Councel", "Deputy", "Deputie", "Treasurer", "Treasuror",
    "Majesty", "Maiestie", "Ibid", "Statutes", "Cape Merchant", "Cape Marchant", "Cape Mercht",
]
ROYAL_STYLE_FORMS = {
    "england": ["england", "englande", "ingland", "eneland", "mngland"],
    "scotland": ["scotland", "scottland", "seotland"],
    "france": ["france", "fraunce", "ffrance", "ffraunce", "firaunce", "firance", "frannce"],
    "ireland": ["ireland", "irland"],
}
# ``dno Henrico Marten milite`` is Latin for "to Sir Henry Marten", not Henrico;
# a place alias right after a Latin form of address names a person.
LATIN_ADDRESS_RE = re.compile(r"(?i)\b(?:dno|dño|domino|dominum|dominus|dom)\.?\s*$")
# ``Isle of Wight being a new ship``: a place name used as a ship's name.
SHIP_AFTER_RE = re.compile(r"(?i)^\W{0,3}(?:being\s+a\s+(?:new\s+)?(?:ship|shipp|pinnace)|ship|shipp)\b")
TITLE_BEFORE_PERSON_RE = re.compile(
    r"(?i)(?:^|[^\w])((?:captain|captaine|capt\.?|sir|lady|lord|chief|king|queen|weroance|doctor|dr\.?|"
    r"master|mistress|mr\.?|mrs\.?|governor|[mMsS][’'*ᵣ]+))\s*[,;:\-—–]?\s*$"
)


# Words that make a model PERSON span an office, heading, commodity, or
# fragment rather than a name (found in the hand review: ``m* Atturney``,
# ``lord Presiden``, ``PRESENT Lo``, ``Silke Coddes``, ``Madder Crop``).
NON_NAME_WORDS = {
    "attorney", "atturney", "attourney", "attorny", "president", "presiden", "presidente",
    "treasuror", "treasurer", "deputy", "deputie", "auditor", "auditors", "recorder", "chancellor",
    "keeper", "secretary", "secretarie", "present", "item", "juris", "gd", "the", "king", "kinge",
    "majesty", "maiestie", "silke", "silk", "madder", "crop", "coddes", "corne", "tobacco",
    "sassafras", "wine", "pitch", "tarre", "sope", "potashes", "iron", "dated", "datum",
    "january", "ianuary", "february", "march", "april", "aprill", "iune", "june", "iuly", "july",
    "august", "september", "october", "november", "nouember", "december",
    "account", "accompt", "accounts", "accompts", "bill", "debt", "debts", "receipt",
}
# Given names (with OCR and Latin forms) that make ``Given Surname`` a name
# even when the model and the title pass both miss it.
GIVEN_NAMES = {
    "john", "iohn", "tohn", "thon", "jhon", "ihon", "johannes", "iohannes", "thomas", "thome", "tho", "william",
    "willm", "wm", "willelmus", "richard", "ricardus", "robert", "robt", "henry", "henrie", "george", "edward",
    "james", "iames", "francis", "frauncis", "nicholas", "samuel", "samuell", "christopher", "anthony",
    "anthonie", "anthonium", "humphrey", "humfrey", "humphry", "daniel", "daniell", "peter", "walter", "ralph",
    "raphe", "roger", "arthur", "edmund", "edmond", "hugh", "gabriel", "gabriell", "matthew", "mathew", "philip",
    "phillip", "michael", "stephen", "lawrence", "laurence", "maurice", "morris", "nathaniel", "nathaniell",
    "abraham", "isaac", "isack", "jeremy", "josias", "martin", "miles", "oliver", "leonard", "simon", "benjamin",
    "charles", "clement", "giles", "gilbert", "joseph", "toby", "zachary", "allen", "alexander", "andrew",
    "elizabeth", "mary", "anne", "ann", "jane", "joan", "susan", "margaret", "alice", "katherine", "ellen",
}
PLACE_OR_ORG_WORDS = {
    "citty", "cittie", "city", "towne", "town", "river", "riuer", "hundred", "island", "iland", "fort", "creek",
    "creeke", "bay", "point", "plantation", "company", "companie", "court", "courte", "counsell", "councell",
}
GIVEN_SURNAME_RE = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{1,10})[ \t]+([A-Z][a-z’']{2,}|ff[a-z]{2,})(?![A-Za-z])")
# ``the feast of St Michaell``: a saint's day, not a person.
FEAST_BEFORE_RE = re.compile(r"(?i)\bfeast\s+of\s+(?:the\s+)?$")
# Truncated names: ``m’ Nich? ffarrar``, ``m’ Dan: Peeker``, ``William Throk- mortun``.
ABBREVIATED_GIVEN_RE = re.compile(
    r"(?i)\b(?:nich|nicho|ed|edw|dan|tho|io|jo|wm|geo|ro|rob|rich|sam|hen|fra|chr|nath|will|willm)$")
CONTINUATION_RE = re.compile(r"^[?:.’']*\s*((?:ff|[A-Z])[A-Za-z’']{2,})")
HYPHEN_CONTINUATION_RE = re.compile(r"^\s+([a-z]{2,})")
SURNAME_AFTER_RE = re.compile(r"^\s+([A-Z][a-z]{2,})\b")
# A place used inside a company or court name: ``the Virginia Court``,
# ``the Companies of Virginia and the Sumer Ilands``.
COMPANY_AFTER_RE = re.compile(r"(?i)^\W{0,4}(?:court|courte|co\b|comp|compa|company|companie|companies|societie|society)")
COMPANY_BEFORE_RE = re.compile(
    r"(?i)\bcompan(?:y|ie|ies)\s+of\s+(?:adventurers\s+(?:for|to|of)\s+)?(?:the\s+)?"
    r"(?:[\w-]+\s+and\s+(?:the\s+)?)?$"
)


# List entries run into leaders, amounts, or the next paragraph:
# ``John Whittingham___.-.__-``, ``Henry Spranger_.__----- 12``.
SPAN_CUT_RE = re.compile(r"_{1,}|\s*\n\s*\n|\s+\d|[-.~]{3,}|\s*[-—–]{2,}")
TITLE_START_RE = re.compile(
    r"(?i)^(?:captain|captaine|captayne|capt\.?|cap[’'*:.]+|sir|st\.?|s[’'*]|lady|lord|lo:|doctor|dr\.?|"
    r"d[’'*]|master|mistress|mr\.?|mrs\.?|m[’'*ᵣ]+)\s")
# Title + name pass carried over from the old extraction: ``M’ Baldwin``,
# ``Cap’ Bargraue``, ``m’ Iohn Porter``. Names may be ff-initial (``m' ffarrar``).
NAME_WORD = r"(?:ff[a-z]{2,}|[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’]+)"
TITLE_NAME_RE = re.compile(
    r"(?<![A-Za-z])(?i:captain|captaine|captayne|capt\.?|cap[’'*:.]+|sir|s[’'*]|lady|doctor|dr\.?|d[’'*]|"
    r"master|mistress|mr\.?|mrs\.?|m[’'*ᵣ]+)[ \t]*[,;:\-—–]?[ \t]*(" + NAME_WORD
    + r"(?:[:.?]?[ \t]+" + NAME_WORD + r")?)"  # ``m’ Tho: Gibbes``, ``m’ Ald: Iohnson``
)
# Capitalized prose words a two-word title match can swallow (``M' Pory Concerning``).
PROSE_WORDS = {
    "concerning", "touching", "whereas", "wheras", "wherevpon", "whervppon", "who", "which", "being", "beinge",
    "and", "that", "the", "for", "to", "of", "in", "with", "by", "his", "her", "their", "was", "were", "is",
    "hath", "had", "did", "said", "also", "allso", "moued", "moved", "signified", "desired", "acquainted",
    "deputy", "treasuror", "treasurer", "knight", "esquire", "esq", "gent", "present",
}
# A surname broken across a line: ``John Swineho-\nwe``.
SURNAME_HYPHEN_RE = re.compile(r"^-[ \t]*\n?[ \t]*([a-z]{2,})")
LATIN_HENRY_RE = re.compile(r"^\s+([A-Z][a-z]{2,})\b")


def trim_model_span(text: str, start: int, end: int) -> int:
    cut = SPAN_CUT_RE.search(text, start, end)
    if cut and cut.start() > start:
        end = cut.start()
    while end > start and text[end - 1] in " .,:;'’":
        end -= 1
    return end


def extend_truncated_name(text: str, start: int, end: int, has_title: bool) -> int:
    """Return a new end offset when the model cut a name short."""
    observed = text[start:end].rstrip()
    after = text[end:end + 40]
    if observed.endswith("-"):
        match = HYPHEN_CONTINUATION_RE.match(after)
        return end + match.end(1) if match else end
    if ABBREVIATED_GIVEN_RE.search(observed):
        match = CONTINUATION_RE.match(after)
        if match and match.group(1).casefold() not in NON_NAME_WORDS:
            return end + match.end(1)
    name_tokens = re.findall(r"[A-Za-z’']{2,}", observed)
    if has_title and len(name_tokens) == 2:  # ``Sir Humfry`` -> ``Sir Humfry May``
        match = SURNAME_AFTER_RE.match(after)
        if match and match.group(1).casefold() not in NON_NAME_WORDS and "\n\n" not in after[:match.end()]:
            return end + match.end(1)
    return end


def looks_like_person(observed: str, has_title: bool) -> bool:
    """Shape gate for model-only PERSON spans, using the person filter's rules:
    a title plus a capitalized name, or a clean two-part capitalized name."""
    flat = " ".join(observed.split())
    words = re.findall(r"[A-Za-z’']+", flat)
    if any(word.casefold().strip("’'") in NON_NAME_WORDS for word in words[1 if has_title else 0:]):
        return False
    if all(len(word) <= 2 for word in words[1 if has_title else 0:]):  # initials only: ``T. Ro``
        return False
    if has_title:
        return bool(re.search(r"\s[A-ZÀ-ÖØ-Þ][\w'’.-]+", flat))
    match = NAME_PATTERN.fullmatch(flat.rstrip(" .,:;"))
    if not match or match.group("title"):
        return False
    pair = f"{match.group('first')} {match.group('last')}"
    return (match.group("last").casefold().rstrip(".,:;") not in NON_NAME_LAST_TOKENS
            and not is_clear_non_person_phrase(pair)
            and not requires_contextual_person_review(pair))


def form_regex(form: str) -> str:
    """OCR-tolerant regex for one alias: any whitespace between words, and a
    letter/digit boundary so trailing OCR junk (``Iland¢``) does not block it."""
    return r"\s+".join(re.escape(part) for part in form.split())


_ROYAL_COUNTRIES = "|".join(
    [r"gr[e]?a[e]?te?\s+brit+[ae]?[iy]?n+e?",  # Great Britain (post-1604 style)
     r"s[ce]ot-\s*t?land", r"eng-\s*[lj]and", r"ire-\s*land"]  # split across a line
    + sorted({f for forms in ROYAL_STYLE_FORMS.values() for f in forms}, key=len, reverse=True)
)
ROYAL_STYLE_RE = re.compile(
    r"(?i)(?<![^\W_])(?:{c})(?:[\s,;.&]+(?:and\s+)?(?:{c})){{2,3}}(?![^\W_])".format(c=_ROYAL_COUNTRIES)
)
# Regnal years split the style: ``...and Ireland the nyneteenth and of
# Scotland the fiue and fiftith``.
REGNAL_RE = re.compile(
    r"(?i)(?<![^\W_])(?:and\s+)?of\s+(?:{c})\s+the\s+(?:[a-z]+[\s-]+){{0,4}}?"
    r"(?:[a-z]*(?:th|first|second|third|tieth|ith)|\d+(?:th|st|nd|rd)?|[ivxlhuyj]{{2,6}}[’'*™]*)(?![^\W_])"
    .format(c=_ROYAL_COUNTRIES)
)


class AuthorityMatcher:
    """One combined, longest-first regex over every authority form."""

    PRIORITY = {"PERSON": 0, "PLACE": 0, "NOT_ENTITY": 1}

    def __init__(self, entries: list[tuple[str, str, str]]) -> None:
        self.lookup: dict[str, tuple[str, str]] = {}
        for label, entity_id, form in entries:
            key = " ".join(form.split()).casefold()
            current = self.lookup.get(key)
            if current is None or self.PRIORITY[label] < self.PRIORITY[current[0]]:
                self.lookup[key] = (label, entity_id)
        # One regex per label, so a person match cannot hide an overlapping,
        # longer place match that starts later (``Capt. John Martin's`` vs
        # ``John Martin's Plantation``).
        self.regexes = {}
        for label in self.PRIORITY:
            forms = sorted((f for f, (l, _) in self.lookup.items() if l == label), key=len, reverse=True)
            if forms:
                self.regexes[label] = re.compile(
                    r"(?<![^\W_])(?:" + "|".join(form_regex(form) for form in forms) + r")(?![^\W_])",
                    re.IGNORECASE,
                )

    def find(self, text: str) -> list[tuple[int, int, str, str]]:
        """Return non-overlapping ``(start, end, label, id)``: the royal style
        first, then the longest match wins."""
        claimed: list[tuple[int, int, str, str]] = [
            (m.start(), m.end(), "NOT_ENTITY", "royal_style") for m in ROYAL_STYLE_RE.finditer(text)
        ]
        claimed += [
            (m.start(), m.end(), "NOT_ENTITY", "regnal_year") for m in REGNAL_RE.finditer(text)
            if not any(s < m.end() and m.start() < e for s, e, _, _ in claimed)
        ]
        candidates = []
        for regex in self.regexes.values():
            for match in regex.finditer(text):
                label, entity_id = self.lookup[" ".join(match.group(0).split()).casefold()]
                candidates.append((match.start(), match.end(), label, entity_id))
        candidates.sort(key=lambda c: (-(c[1] - c[0]), self.PRIORITY[c[2]], c[0]))
        for start, end, label, entity_id in candidates:
            if not any(s < end and start < e for s, e, _, _ in claimed):
                claimed.append((start, end, label, entity_id))
        return sorted(claimed)


def build_entries(dictionary: dict, authority: Path, place_resolutions: Path) -> tuple[list, Counter]:
    entries: list[tuple[str, str, str]] = []
    stats: Counter = Counter()
    # Contextual aliases go first so they win over a plain alias of the same spelling.
    for number, rule in enumerate(dictionary.get("contextual_aliases", [])):
        for form in rule["forms"]:
            entries.append(("PERSON", f"CONTEXT:{number}", " ".join(form.split())))
            stats["person_contextual"] += 1
    for person, entry in dictionary["people"].items():
        for alias in {" ".join(a.split()) for a in [person, *entry.get("aliases", [])]}:
            if len(alias.split()) == 1 and alias.casefold() not in SINGLE_WORD_PEOPLE:
                stats["person_single_word_skipped"] += 1
                continue
            entries.append(("PERSON", person, alias))
            stats["person"] += 1
    with authority.open(encoding="utf-8-sig", newline="") as handle:
        for place in csv.DictReader(handle):
            for alias in {" ".join(a.split()) for a in place["aliases"].split("|") if a.strip()}:
                entries.append(("PLACE", place["place_id"], alias))
                stats["place"] += 1
    non_entities = set(GENERIC_NON_ENTITIES)
    non_entities |= {name for name, reason in dictionary["excluded"].items() if NON_ENTITY_REASON_RE.match(reason)}
    # Place exclusions say "not a new place"; compounds of two real places
    # (``Henrico and Charles Citty``) must not hide those places in the text.
    non_entities |= {
        form for form, reason in json.loads(place_resolutions.read_text(encoding="utf-8"))["excluded"].items()
        if not reason.casefold().startswith("compound")
    }
    for form in sorted(non_entities):
        entries.append(("NOT_ENTITY", "", " ".join(form.split())))
        stats["not_entity"] += 1
    return entries, stats


AUTHORITY: AuthorityMatcher | None = None


@Language.component("authority_ruler")
def authority_ruler(doc):
    """Fix authority matches as entities before the statistical NER runs."""
    spans, exact = [], {}
    for start, end, label, entity_id in AUTHORITY.find(doc.text):
        span = doc.char_span(start, end, label=label, kb_id=entity_id, alignment_mode="expand")
        if span is not None:
            spans.append(span)
            exact[span.start] = (start, end)
    doc.ents = filter_spans(spans)
    doc.user_data["authority_offsets"] = exact
    return doc


def load_places(authority: Path) -> dict[str, dict[str, str]]:
    with authority.open(encoding="utf-8-sig", newline="") as handle:
        return {row["place_id"]: row for row in csv.DictReader(handle)}


def surname_set(dictionary: dict) -> set[str]:
    surnames = set()
    for person, entry in dictionary["people"].items():
        for alias in [person, *entry.get("aliases", [])]:
            _, tokens = normalized_phrase(alias.split(",")[0])
            # ``Bishop of London`` / ``Earle of Southampton`` name a see or title, not a surname.
            if len(tokens) >= 2 and "of" not in tokens:
                surnames.add(tokens[-1])
    return surnames


def extract(
    pages: list[dict], nlp, dictionary: dict, places: dict, locator: Locator, roles: dict[str, str],
) -> tuple[list[dict], Counter]:
    index, excluded = person_index(dictionary)
    contextual = contextual_index(dictionary)
    surnames = surname_set(dictionary)
    stats: Counter = Counter()
    blocked: Counter = Counter()
    dropped: Counter = Counter()
    rows: list[dict] = []
    record_pages = [page for page in pages if roles.get(page["page_id"], "record") == "record"]
    stats["index_pages_skipped"] = len(pages) - len(record_pages)
    texts = (page["text"].replace("\n", " ") for page in record_pages)
    for page, doc in zip(record_pages, nlp.pipe(texts, batch_size=16)):
        text = page["text"]
        page_start = len(rows)
        offsets = doc.user_data.get("authority_offsets", {})
        for ent in doc.ents:
            start, end = offsets.get(ent.start, (ent.start_char, ent.end_char)) if ent.kb_id_ or \
                ent.label_ == "NOT_ENTITY" else (ent.start_char, ent.end_char)
            sentence = " ".join(ent.sent.text.split())
            row = {"page_id": page["page_id"], "span_start": start, "span_end": end,
                   "observed_span": text[start:end], "sentence_text": sentence, "link_note": ""}
            if ent.label_ == "NOT_ENTITY":
                stats["blocked_not_entity"] += 1
                blocked[" ".join(ent.text.split()).casefold()] += 1
                continue
            if ent.label_ == "PLACE":
                place = places[ent.kb_id_]
                title = territorial_title_before(text, start, signature_allowed(place["place_id"]))
                if title is not None:
                    rule = suggest_person(place["place_id"], title.title_class, page["volume"])
                    row.update(
                        span_start=title.start, observed_span=text[title.start:end], label="PERSON",
                        entity_key=rule.person if rule else "", entity_name=rule.person if rule else "",
                        source=f"territorial_title:{title.title_class}",
                        link_note=rule.note if rule else UNRESOLVED_NOTES.get((place["place_id"], title.title_class), ""),
                    )
                    stats["place_to_titled_person"] += 1
                elif COMPANY_AFTER_RE.search(text[end:end + 12]) or COMPANY_BEFORE_RE.search(
                        " ".join(text[max(0, start - 60):start].split()) + " "):
                    stats["place_dropped_company_use"] += 1
                    continue
                elif LATIN_ADDRESS_RE.search(text[max(0, start - 12):start]):
                    stats["place_dropped_latin_personal_name"] += 1
                    continue
                elif ent.text.casefold() in {"henrico", "henricus"} and LATIN_HENRY_RE.match(text[end:end + 20]) \
                        and LATIN_HENRY_RE.match(text[end:end + 20]).group(1).casefold() not in {
                            "river", "riuer", "citty", "cittie", "city", "island", "iland", "college", "colledge"}:
                    # Latin ``Henrico Cromewell`` is Henry Cromwell, not the town.
                    end = end + LATIN_HENRY_RE.match(text[end:end + 20]).end(1)
                    observed = text[start:end]
                    person, method, note = resolve_person(
                        {"observed_span": observed, "name_string": observed}, index, excluded, contextual, page["volume"])
                    row.update(span_end=end, observed_span=observed, label="PERSON", entity_key=person,
                               entity_name=person, source=f"latin_given_name:{method}", link_note=note)
                    stats["place_to_latin_person"] += 1
                elif SHIP_AFTER_RE.search(text[end:end + 30]):
                    stats["place_dropped_ship_name"] += 1
                    continue
                else:
                    single = len(ent.text.split()) == 1
                    if (single and place["matching_policy"] == "ambiguous_contextual"
                            and ent.text.casefold() in surnames
                            and not has_geographic_sentence_context(ent.text, sentence)):
                        stats["place_dropped_surname_without_context"] += 1
                        dropped[place["preferred_name"]] += 1
                        continue
                    row.update(label="PLACE", entity_key=place["place_id"], entity_name=place["preferred_name"],
                               source=f"ruler_place:{place['matching_policy']}")
            elif ent.label_ == "PERSON" and ent.kb_id_.startswith("CONTEXT:"):
                rule = dictionary["contextual_aliases"][int(ent.kb_id_.split(":")[1])]
                person = rule["by_volume"].get(str(page["volume"]), "")
                row.update(label="PERSON", entity_key=person, entity_name=person,
                           source="contextual_alias", link_note=f"volume {page['volume']}")
            elif ent.label_ == "PERSON" and ent.kb_id_:
                row.update(label="PERSON", entity_key=ent.kb_id_, entity_name=ent.kb_id_, source="ruler_person")
            elif ent.label_ == "PERSON":
                # Model PERSON: take an adjacent title, then try the dictionary.
                title = TITLE_BEFORE_PERSON_RE.search(text[max(0, start - 24):start])
                if title:
                    start = start - (len(text[max(0, start - 24):start]) - title.start(1))
                end = trim_model_span(text, start, end)
                title = title or TITLE_START_RE.match(text[start:end])
                end = extend_truncated_name(text, start, end, bool(title) or bool(
                    re.match(r"(?i)(?:sir|s['’*]|m['’*]+|mr\.?|capt\w*|lord|lo:?)\s", text[start:end])))
                row["span_end"] = end
                observed = text[start:end]
                if FEAST_BEFORE_RE.search(text[max(0, start - 20):start]):
                    row.update(span_start=start, observed_span=observed, label="PERSON_CANDIDATE",
                               entity_key="", entity_name="", source="model_person:feast_day", link_note="")
                    row.update(locator.locate(page["page_id"], row["span_start"], row["span_end"]))
                    row["mention_id"] = f"{page['page_id']}-N{row['span_start']:05d}-{row['span_end']:05d}"
                    rows.append(row)
                    stats["model_person"] += 1
                    continue
                person, method, note = resolve_person(
                    {"observed_span": observed, "name_string": ent.text}, index, excluded,
                    contextual, page["volume"])
                shaped = bool(person) or looks_like_person(observed, bool(title))
                row.update(span_start=start, observed_span=observed,
                           label="PERSON" if shaped else "PERSON_CANDIDATE", entity_key=person,
                           entity_name=person, source=f"model_person:{method}", link_note=note)
            elif ent.label_ in MODEL_PLACE_LABELS:
                row.update(label="PLACE_CANDIDATE", entity_key="", entity_name="",
                           source=f"model_{ent.label_}")
            else:
                continue
            row.update(locator.locate(page["page_id"], row["span_start"], row["span_end"]))
            row["mention_id"] = f"{page['page_id']}-N{row['span_start']:05d}-{row['span_end']:05d}"
            rows.append(row)
            stats[row["source"].split(":")[0]] += 1
        # Title + name forms the model missed (``M’ Baldwin``, ``Cap’ Bargraue``).
        taken = [(r["span_start"], r["span_end"]) for r in rows[page_start:]]
        taken += [(ent.start_char, ent.end_char) for ent in doc.ents]
        for match in TITLE_NAME_RE.finditer(text):
            start, end = match.start(), match.end()
            words = re.findall(r"[A-Za-z’']+", match.group(1))
            if len(words) == 2 and words[1].casefold() in PROSE_WORDS:
                end = match.start(1) + match.group(1).find(words[0]) + len(words[0])
            hyphen = SURNAME_HYPHEN_RE.match(text[end:end + 30])
            if hyphen:
                end += hyphen.end(1)
            if any(s < end and start < e for s, e in taken):
                continue
            observed = text[start:end]
            if not looks_like_person(observed, True):
                continue
            person, method, note = resolve_person(
                {"observed_span": observed, "name_string": match.group(1)}, index, excluded, contextual, page["volume"])
            if method == "excluded":
                continue
            row = {"page_id": page["page_id"], "span_start": start, "span_end": end, "observed_span": observed,
                   "sentence_text": " ".join(text[max(0, start - 120):end + 120].split()),
                   "label": "PERSON", "entity_key": person, "entity_name": person,
                   "source": f"title_name:{method}", "link_note": note}
            row.update(locator.locate(page["page_id"], start, end))
            row["mention_id"] = f"{page['page_id']}-N{start:05d}-{end:05d}"
            rows.append(row)
            taken.append((start, end))
            stats["title_name"] += 1
        for match in GIVEN_SURNAME_RE.finditer(text):
            given, surname = match.group(1), match.group(2)
            if given.casefold() not in GIVEN_NAMES or surname.casefold().strip("’'") in PLACE_OR_ORG_WORDS:
                continue
            start, end = match.start(), match.end()
            hyphen = SURNAME_HYPHEN_RE.match(text[end:end + 30])
            if hyphen:
                end += hyphen.end(1)
            if any(s < end and start < e for s, e in taken):
                continue
            observed = text[start:end]
            if not looks_like_person(observed, False):
                continue
            person, method, note = resolve_person(
                {"observed_span": observed, "name_string": observed}, index, excluded, contextual, page["volume"])
            if method == "excluded":
                continue
            row = {"page_id": page["page_id"], "span_start": start, "span_end": end, "observed_span": observed,
                   "sentence_text": " ".join(text[max(0, start - 120):end + 120].split()),
                   "label": "PERSON", "entity_key": person, "entity_name": person,
                   "source": f"given_surname:{method}", "link_note": note}
            row.update(locator.locate(page["page_id"], start, end))
            row["mention_id"] = f"{page['page_id']}-N{start:05d}-{end:05d}"
            rows.append(row)
            taken.append((start, end))
            stats["given_surname"] += 1
    stats["top_blocked"] = dict(blocked.most_common(12))
    stats["dropped_places"] = dict(dropped)
    return rows, stats


INDIGENOUS_LABELS = {
    "group": "INDIGENOUS_GROUP", "group_or_homonym": "INDIGENOUS_GROUP", "person": "PERSON",
    "collective": "INDIGENOUS_COLLECTIVE", "contextual": "INDIGENOUS_COLLECTIVE", "title": "INDIGENOUS_TITLE",
}
STRONG_INDIGENOUS_READINGS = {"group", "person", "collective", "title"}


def merge_indigenous(rows: list[dict], indigenous_csv: Path) -> Counter:
    """Merge ``find_indigenous.py`` output into the NER rows.

    An Indigenous match replaces overlapping candidate or unlinked rows; a row
    already linked to a person/place is kept unless the Indigenous reading is
    unambiguous. River/settlement homonyms are left to the place matcher.
    """
    stats: Counter = Counter()
    by_page: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_page[row["page_id"]].append(row)
    with indigenous_csv.open(encoding="utf-8", newline="") as handle:
        indigenous = list(csv.DictReader(handle))
    removed: set[int] = set()
    added: list[dict] = []
    for hit in indigenous:
        label = INDIGENOUS_LABELS.get(hit["reading"])
        if label is None:
            stats["indigenous_place_homonym_skipped"] += 1
            continue
        start, end = int(hit["span_start"]), int(hit["span_end"])
        overlaps = [r for r in by_page[hit["page_id"]]
                    if r["span_start"] < end and start < r["span_end"] and id(r) not in removed]
        linked = [r for r in overlaps if r.get("entity_key") and r["label"] in {"PERSON", "PLACE"}]
        if linked and hit["reading"] not in STRONG_INDIGENOUS_READINGS:
            stats["indigenous_kept_existing_link"] += 1
            continue
        for r in overlaps:
            removed.add(id(r))
        stats[f"indigenous_{label.lower()}"] += 1
        added.append({
            "mention_id": hit["mention_id"], "label": label,
            "entity_key": hit["entity_name"] if hit["category"] == "person" else hit["entity_id"],
            "entity_name": hit["entity_name"],
            "source": f"indigenous:{hit['match_type']}:{hit['reading']}", "link_note": hit["matched_form"],
            "observed_span": hit["observed_span"], "page_id": hit["page_id"],
            "span_start": start, "span_end": end,
            "segment_id": hit["segment_id"], "date_start": hit["date_start"], "date_end": hit["date_end"],
            "date_source": "", "date_confidence": hit["date_confidence"], "page_role": hit["page_role"],
            "editorial_span": hit["editorial_span"], "usable_for_association": hit["usable_for_association"],
            "sentence_text": hit["context"],
        })
    stats["indigenous_replaced_rows"] = len(removed)
    rows[:] = [r for r in rows if id(r) not in removed] + added
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--place-resolutions", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--page-roles", type=Path, required=True)
    parser.add_argument("--editorial-spans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--indigenous", type=Path, help="Output of find_indigenous.py to merge in.")
    parser.add_argument("--model", default="en_core_web_sm")
    args = parser.parse_args()

    global AUTHORITY
    dictionary = person_dictionary.load(args.dictionary)
    entries, pattern_stats = build_entries(dictionary, args.authority, args.place_resolutions)
    AUTHORITY = AuthorityMatcher(entries)
    nlp = spacy.load(args.model, disable=["lemmatizer"])
    nlp.add_pipe("authority_ruler", before="ner")
    print(f"Authority forms: {dict(pattern_stats)}")

    locator = Locator(args.segments, args.page_roles, args.editorial_spans)
    pages = load_primary_pages(args.source)
    rows, stats = extract(pages, nlp, dictionary, load_places(args.authority), locator, locator.roles)
    if args.indigenous:
        stats.update(merge_indigenous(rows, args.indigenous))
    rows.sort(key=lambda row: (row["page_id"], row["span_start"]))
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)

    print(f"Extraction: {dict(stats)}")
    by_label = defaultdict(lambda: Counter())
    for row in rows:
        by_label[row["label"]]["total"] += 1
        if row["usable_for_association"] == "yes":
            by_label[row["label"]]["usable"] += 1
            if row["entity_key"]:
                by_label[row["label"]]["usable_linked"] += 1
    for label, counts in sorted(by_label.items()):
        distinct = len({r["entity_key"] for r in rows if r["label"] == label and r["entity_key"]})
        print(f"  {label}: {dict(counts)}, distinct linked {distinct}")


if __name__ == "__main__":
    main()
