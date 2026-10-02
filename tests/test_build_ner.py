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


def test_indigenous_merge_replaces_candidates_but_respects_links(tmp_path):
    import csv
    from build_ner import merge_indigenous

    fields = ["mention_id", "category", "entity_id", "entity_name", "reading", "match_type", "matched_form",
              "observed_span", "page_id", "span_start", "span_end", "segment_id", "date_start", "date_end",
              "date_confidence", "page_role", "editorial_span", "usable_for_association", "context"]
    hits = [
        {"mention_id": "a", "category": "person", "entity_id": "IP-002", "entity_name": "Opechancanough",
         "reading": "person", "observed_span": "Opochancano", "span_start": "10", "span_end": "21"},
        {"mention_id": "b", "category": "group", "entity_id": "IG-003", "entity_name": "Pamunkey",
         "reading": "group_or_homonym", "observed_span": "Pamunkey", "span_start": "40", "span_end": "48"},
        {"mention_id": "c", "category": "group", "entity_id": "IG-003", "entity_name": "Pamunkey",
         "reading": "place_homonym", "observed_span": "Pamunkey", "span_start": "60", "span_end": "68"},
    ]
    path = tmp_path / "indigenous.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({f: h.get(f, "V02-P0001" if f == "page_id" else "") for f in fields} for h in hits)
    rows = [
        {"page_id": "V02-P0001", "span_start": 10, "span_end": 21, "label": "PERSON_CANDIDATE", "entity_key": ""},
        {"page_id": "V02-P0001", "span_start": 40, "span_end": 48, "label": "PLACE", "entity_key": "NW-X"},
    ]
    merge_indigenous(rows, path)
    labels = sorted((r["span_start"], r["label"]) for r in rows)
    assert labels == [(10, "PERSON"), (40, "PLACE")]  # candidate replaced; link kept; homonym skipped


def test_offices_commodities_and_initials_are_not_people():
    for observed, titled in [("m* Atturney", True), ("lord Presiden", True), ("PRESENT Lo", False),
                             ("Silke Coddes", False), ("Madder Crop", False), ("T. Ro", False)]:
        assert not looks_like_person(observed, titled), observed


def test_truncated_names_are_extended():
    from build_ner import extend_truncated_name

    for text, cut, expected in [
        ("m’ Nich? ffarrar Dpt.", "m’ Nich", "m’ Nich? ffarrar"),
        ("m’ Dan: Peeker Gookin", "m’ Dan", "m’ Dan: Peeker"),
        ("Sir Humfry May knighte", "Sir Humfry", "Sir Humfry May"),
        ("of 8\" William Throk- mortun y® rest", "William Throk-", "William Throk- mortun"),
    ]:
        start = text.index(cut)
        end = extend_truncated_name(text, start, start + len(cut), cut.startswith(("m’", "Sir")))
        assert text[start:end] == expected


def test_company_and_court_uses_of_a_place():
    from build_ner import COMPANY_AFTER_RE, COMPANY_BEFORE_RE

    assert COMPANY_AFTER_RE.search(" Court desired")
    assert COMPANY_AFTER_RE.search(" Companie; and")
    assert COMPANY_BEFORE_RE.search("the two Companies of Virginia and the ")
    assert COMPANY_BEFORE_RE.search("The Companie of ")
    assert not COMPANY_BEFORE_RE.search("whole Company of Adventurers here in ")


def test_royal_style_variants_and_regnal_years():
    matcher = AuthorityMatcher([("PLACE", "OW-S", "Scotland"), ("PLACE", "OW-I", "Ireland"), ("PLACE", "OW-E", "England")])
    text = ("Kinge of England Scotland firaunce and Ireland Defendor ... raigne of England ffraunce and Ireland "
            "the nyneteenth and of Scotland the fiue and fiftith ... of Greate Brittaine Fraunce & Ireland ... "
            "of England Scot- land ffrance and Ireland kinge ... and of Scotland the luj'* Betwene ... "
            "stayed in Ireland")
    places = [text[s:e] for s, e, label, _ in matcher.find(text) if label == "PLACE"]
    assert places == ["Ireland"]  # only the real place at the end
