import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scan_place_candidates import Place, page_matches, untitled_alias_match  # noqa: E402
from territorial_titles import suggest_person, territorial_title_before  # noqa: E402


def title_class(text: str, place: str = "Southampton", signature: bool = False) -> str | None:
    match = territorial_title_before(text, text.casefold().rindex(place.casefold()), signature)
    return match.title_class if match else None


@pytest.mark.parametrize("text, expected", [
    ("And it was moued by my Lo: of Southampton", "lord"),
    ("the Ea: of Southampton tooke the chaire", "earl"),
    ("The Earle Southampton.", "earl"),
    ("The Ka: of Southampton also desired", "earl"),
    ("my Lord of\nSouthampton", "lord"),
    ("my L. of Southampton knew", "lord"),
    ("A LETTER TO THE HARL OF SOUTHAMPTON", "earl"),
    ("Vyscount of Southampton", "peer"),
])
def test_titles_before_southampton(text, expected):
    assert title_class(text) == expected


@pytest.mark.parametrize("text", [
    "proposed to the Counsell at Southampton howse",
    "put abourd the Southampton and cofiitted",
    "a free Schoole to be erected in Southampton",
])
def test_untitled_southampton(text):
    assert title_class(text) is None


def test_civic_and_episcopal_titles():
    assert title_class("directed to the Lord Maior of London", "London") == "mayor"
    assert title_class("y¢ Lo: Bishop of London", "London") == "bishop"
    assert title_class("Lo: Bishop: of London", "London") == "bishop"


def test_ocr_et_cetera_is_not_an_earl():
    assert title_class("Defender of the faith &e. of England", "England") is None


def test_signature_only_when_allowed():
    assert title_class("HENRY SOUTHAMPTON", "SOUTHAMPTON") is None
    assert title_class("HENRY SOUTHAMPTON", "SOUTHAMPTON", signature=True) == "signature"


def test_person_suggestions():
    assert suggest_person("OW-008", "earl", 2).person == "Henry Wriothesley"
    assert suggest_person("OW-006", "earl", 3).person == "Thomas Cecil, Earl of Exeter"
    assert suggest_person("OW-001", "bishop", 1).person == "John King, Bishop of London"
    assert suggest_person("OW-001", "bishop", 2) is None
    assert suggest_person("OW-001", "mayor", 1) is None


SOUTHAMPTON = Place("OW-008", "Southampton", "city/port", "Old World", ("Southampton",), "ambiguous_contextual")
HUNDRED = Place(
    "NW-032", "Southampton Hundred", "plantation/hundred", "New World",
    ("Southampton Hundred",), "high_confidence_alias",
)


def test_longest_alias_wins_across_line_break():
    matches = page_matches("erected in Southampton\nHundred (so", [SOUTHAMPTON, HUNDRED])
    assert [place.place_id for _, _, place, _ in matches] == ["NW-032"]


def test_person_queue_skips_titled_span():
    assert untitled_alias_match("Southampton", SOUTHAMPTON, "Lord of Southampton", "of Southampton") is None
    assert untitled_alias_match("Southampton", SOUTHAMPTON, "Southampton howse", "Southampton howse")
