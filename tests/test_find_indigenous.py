import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from find_indigenous import Lexicon, dehyphenate, reading_for, skeleton  # noqa: E402

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "configs/indigenous_authority.json").read_text(encoding="utf-8"))
LEXICON = Lexicon(CONFIG)


def test_dehyphenate_maps_back_to_original_offsets():
    text = "vppon the Tanx Pow- hatans and Opachan-\nkano"
    joined, mapping = dehyphenate(text)
    assert "Tanx Powhatans" in joined and "Opachankano" in joined
    start = joined.index("Opachankano")
    end = start + len("Opachankano")
    assert text[mapping[start]: mapping[end - 1] + 1] == "Opachan-\nkano"


def test_spelling_skeleton_ignores_early_modern_variation():
    assert skeleton("POWHATANES") == skeleton("Powhatan")
    assert skeleton("Pamaunkey") == skeleton("Pamaunkey's")


def test_fuzzy_catches_irregular_spellings():
    for word, expected in [("Opochancano", "IP-002"), ("Apochancono", "IP-002"),
                           ("Pomunckeys", "IG-003"), ("Manahockes", "IG-021"), ("Jtoyatin", "IP-003")]:
        entry, _, _ = LEXICON.match_word(word)
        assert entry["entity_id"] == expected, word


def test_short_or_english_looking_names_need_exact_spelling():
    for word in ["Chandos", "Tomkins", "Kemp", "Iapan", "Mocon"]:
        assert LEXICON.match_word(word) is None, word


def entry(entity_id):
    return next(e for e in LEXICON.entries if e["entity_id"] == entity_id)


def test_readings_separate_people_from_rivers_and_settlements():
    assert reading_for(entry("IG-003"), "Pamunkey", ["the"], ["River"]) == "place_homonym"
    assert reading_for(entry("IG-003"), "Pamunkeys", ["the"], ["and"]) == "group"
    assert reading_for(entry("IG-016"), "Patawomeck", ["Kinge", "of"], ["against"]) == "group"
    assert reading_for(entry("IG-007"), "Kiccowtan", ["of"], ["And"]) == "place_homonym"
