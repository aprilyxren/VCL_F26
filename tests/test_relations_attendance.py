import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from attendance import attendance_blocks, is_list_line  # noqa: E402
from extract_relations import extract, has_boundary  # noqa: E402


def test_attendance_list_ends_at_prose_and_continues_across_pages():
    page1 = ("A COURT HELD FOR VIRGINIA\nPRESENT.\nm’ Deputy Ferrar. m'’ Bromefeild. m’ Bartham.\n"
             "m’ Brooke. m’ Riseley.\n\nm’ Barnard.\n\nThe Court haveinge made request vnto m’ Chamberlyne to peure them")
    blocks = attendance_blocks([{"page_id": "V01-P0001", "volume": 1, "text": page1}])["V01-P0001"]
    start, end = blocks[0]
    assert "m’ Barnard." in page1[start:end] and "The Court" not in page1[start:end]
    assert is_list_line("S' Robert Killigrew kn‘. S' Tho: Grantham kn‘.")
    assert not is_list_line("The Court haveinge made request vnto m’ Chamberlyne to peure them")
    pages = [
        {"page_id": "V01-P0001", "volume": 1, "text": "PRESENT\nS' Edwin Sandis kn’.\nS' Thomas Roe kn’."},
        {"page_id": "V01-P0002", "volume": 1, "text": "262 RECORDS OF THE VIRGINIA COMPANY\nm’ Iohn Ferrar.\n\nIt was ordered that"},
    ]
    blocks = attendance_blocks(pages)
    assert blocks["V01-P0002"] and "m’ Iohn Ferrar." in pages[1]["text"][slice(*blocks["V01-P0002"][0])]


def test_sentence_boundaries_respect_abbreviations_and_lists():
    assert not has_boundary(" Capt. ")
    assert not has_boundary(" S' Tho: ")
    assert has_boundary(" was ordered. The ")
    assert has_boundary(" Court.\n\nIt ")


def mention(text, span, label, key="", role="mentioned"):
    start = text.index(span)
    return {"page_id": "V04-P0001", "segment_id": "V04-P0001-S01", "date_start": "1622-04-18",
            "date_confidence": "high", "label": label, "entity_key": key, "entity_name": key,
            "observed_span": span, "span_start": str(start), "span_end": str(start + len(span)),
            "usable_for_association": "yes", "mention_role": role}


def relations_for(text, spans):
    rows = [mention(text, *s) for s in spans]
    return {(r["subject"], r["relation"], r["object"]) for r in extract([{"page_id": "V04-P0001", "text": text}], rows)}


def test_office_possession_and_kin_relations():
    text = ("Sir Francis Wyatt knight Governor and Captaine generall of Virginia. "
            "At Weynoack of Sir George Yeardley his people. Thomas Tracy his wife Mary Tracy. "
            "Robert Iordan, his wife, Richard Rowles. Richard Sandys, son of Edwin Sandys, and William Oxentridge.")
    found = relations_for(text, [
        ("Sir Francis Wyatt", "PERSON", "Francis Wyatt"), ("Virginia", "PLACE", "NW-001"),
        ("Weynoack", "PLACE", "NW-W"), ("Sir George Yeardley", "PERSON", "George Yeardley"),
        ("Thomas Tracy", "PERSON", "Thomas Tracy"), ("Mary Tracy", "PERSON", "Mary Tracy"),
        ("Robert Iordan", "PERSON", "Robert Jordan"), ("Richard Rowles", "PERSON", "Richard Rowles"),
        ("Richard Sandys", "PERSON", "Richard Sandys"), ("Edwin Sandys", "PERSON", "Edwin Sandys"),
        ("William Oxentridge", "PERSON", "William Oxentridge"),
    ])
    assert ("Francis Wyatt", "office_in", "NW-001") in found
    assert ("George Yeardley", "people_at", "NW-W") in found
    assert ("Mary Tracy", "kin_of", "Thomas Tracy") in found
    assert not any(s == "Richard Rowles" or o == "Richard Rowles" for s, _, o in found)
    assert ("Richard Sandys", "kin_of", "Edwin Sandys") in found
    assert ("Richard Sandys", "kin_of", "William Oxentridge") not in found


def test_present_mentions_are_skipped():
    text = "PRESENT. m’ Bull. Sir Francis Wyatt Governor of Virginia."
    found = relations_for(text, [("Sir Francis Wyatt", "PERSON", "Francis Wyatt", "present"),
                                 ("Virginia", "PLACE", "NW-001")])
    assert not found
