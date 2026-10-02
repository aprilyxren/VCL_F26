"""Recognize place names used inside peerage, episcopal, and civic titles.

``Earle of Southampton`` and ``my Lo: of Southampton`` name a person, not the
port. A place alias immediately preceded by one of these titles is treated as a
person reference whose territory is the place. Person suggestions come only
from the explicit rules below and remain suggestions for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


LOOKBEHIND_CHARS = 40

# Each pattern is anchored to the end of the text preceding a place alias.
# Order matters only for readability; the anchors keep the classes disjoint.
TITLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mayor", re.compile(
        r"(?i)(?:\blord\s+)?\b(?:mayor|maior|maiour)[:.]?\s+of\s+(?:the\s+)?$"
    )),
    ("bishop", re.compile(
        r"(?i)(?:\b(?:lord|lo)\s*[:.]?\s+)?\bbishop\s*[:.]?\s+of\s+$"
    )),
    ("earl", re.compile(
        r"(?i)(?:\b(?:earle|earl|erle|karle|farle|harle)\s+(?:of\s+)?"
        r"|\b(?:ea|ka|ha)\s*[:.]\s*(?:of\s+)?"
        r"|(?<![\w.&])e\s*[:.]\s*(?:of\s+)?"
        r"|\b(?:ea|e)\s+of\s+"
        # OCR-damaged headings such as ``EARN OR`` and ``[HARU OR``.
        r"|\b(?:earn|kart|karl|harl|haru)\s+o[fr]\s+)$"
    )),
    ("lord", re.compile(
        r"(?i)(?:(?:\bmy\s+)?\b(?:lord|lo|lp)\s*[:.]?\s+(?:of\s+)?"
        r"|\bmy\s+l\.\s*(?:of\s+)?|\bl\.\s*of\s+)$"
    )),
    ("peer", re.compile(
        r"(?i)\b(?:duke|marquess|marquesse|marques|viscount|vyscount|"
        r"countess|countesse|baron|barron|baronesse)\s+(?:of\s+)?$"
    )),
)

# Given-name signatures such as ``HENRY SOUTHAMPTON``; only used where a rule
# explicitly allows the ``signature`` class.
SIGNATURE_PATTERN = re.compile(r"(?i)\b(?:henry|hen\s*[:.]|h\.)\s*$")


@dataclass(frozen=True)
class TitleMatch:
    title: str
    title_class: str
    start: int


@dataclass(frozen=True)
class PersonRule:
    rule_id: str
    place_id: str
    title_classes: frozenset[str]
    person: str
    volumes: frozenset[int] | None = None
    note: str = ""


# Historical identifications for the Virginia Company records (1606-1626).
PERSON_RULES: tuple[PersonRule, ...] = (
    PersonRule(
        "southampton_peerage", "OW-008",
        frozenset({"earl", "lord", "peer", "signature"}),
        "Henry Wriothesley",
        note="Henry Wriothesley, 3rd Earl of Southampton; Company treasurer 1620-1624.",
    ),
    PersonRule(
        "exeter_peerage", "OW-006", frozenset({"earl"}),
        "Thomas Cecil, Earl of Exeter",
        note="Thomas Cecil, 1st Earl of Exeter (earl 1605-1623); named in the 1609 charter.",
    ),
    PersonRule(
        "london_bishop_v1", "OW-001", frozenset({"bishop", "lord"}),
        "John King, Bishop of London", volumes=frozenset({1}),
        note="John King was Bishop of London 1611-March 1621, the span of Volume I.",
    ),
)

UNRESOLVED_NOTES = {
    ("OW-001", "bishop"): "Bishop of London outside Volume I; George Montaigne held the see from 1621. Verify date.",
    ("OW-001", "lord"): "Lord of London outside Volume I; likely the Bishop. Verify date.",
}


def territorial_title_before(
    text: str,
    start: int,
    allow_signature: bool = False,
) -> TitleMatch | None:
    """Return the title immediately preceding ``start``, if any."""
    window_start = max(0, start - LOOKBEHIND_CHARS)
    window = text[window_start:start]
    for title_class, pattern in TITLE_PATTERNS:
        match = pattern.search(window)
        if match:
            return TitleMatch(match.group(0).strip(), title_class, window_start + match.start())
    if allow_signature:
        match = SIGNATURE_PATTERN.search(window)
        if match:
            return TitleMatch(match.group(0).strip(), "signature", window_start + match.start())
    return None


def signature_allowed(place_id: str) -> bool:
    return any(
        rule.place_id == place_id and "signature" in rule.title_classes
        for rule in PERSON_RULES
    )


def suggest_person(place_id: str, title_class: str, volume: int | None) -> PersonRule | None:
    for rule in PERSON_RULES:
        if rule.place_id != place_id or title_class not in rule.title_classes:
            continue
        if rule.volumes is not None and volume not in rule.volumes:
            continue
        return rule
    return None
