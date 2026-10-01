import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_ner import AuthorityMatcher, looks_like_person  # noqa: E402

MATCHER = AuthorityMatcher([
    ("PERSON", "John Martin", "Capt. John Martin’s"),
    ("PERSON", "Richard Holland", "Richard Holland"),
    ("PLACE", "NW-X", "John Martin’s Plantation"),
    ("PLACE", "NW-020", "Somer Iland"),
    ("PLACE", "OW-H", "Holland"),
    ("PLACE", "OW-E", "England"),
    ("NOT_ENTITY", "", "Cape Marchant"),
])


def labels(text):
    return [(text[s:e], label) for s, e, label, _ in MATCHER.find(text)]


def test_ocr_junk_after_a_name_still_matches():
    assert labels("the Company of the Somer Iland¢ doth") == [("Somer Iland", "PLACE")]


def test_line_breaks_inside_a_name():
    assert labels("sent to Somer\nIland") == [("Somer\nIland", "PLACE")]


def test_longest_overlapping_match_wins():
    assert labels("at Capt. John Martin’s Plantation") == [("John Martin’s Plantation", "PLACE")]
    assert labels("m' Richard Holland said") == [("Richard Holland", "PERSON")]


def test_royal_style_blocks_its_countries():
    found = labels("by the grace of God King of England Scotland ffraunce and Ireland Defender")
    assert found == [("England Scotland ffraunce and Ireland", "NOT_ENTITY")]


def test_non_entities_are_claimed():
    assert labels("the Cape Marchant reported") == [("Cape Marchant", "NOT_ENTITY")]


def test_person_shape_gate():
    assert looks_like_person("Zachary Jones", False)
    assert looks_like_person("m’ Meuerell", True)
    assert not looks_like_person("Worke", False)
    assert not looks_like_person("the Common", False)
