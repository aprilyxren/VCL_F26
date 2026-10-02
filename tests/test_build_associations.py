import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_associations import build, meeting_ids  # noqa: E402


def segment(sid, page, heading="", source="running_header", date="1621-06-18"):
    return {"segment_id": sid, "page_id": page, "heading": heading, "date_source": source,
            "date_start": date, "date_end": date, "confidence": "high"}


def mention(page, sid, label, key, name, start, end, usable="yes"):
    return {"page_id": page, "segment_id": sid, "label": label, "entity_key": key, "entity_name": name,
            "span_start": str(start), "span_end": str(end), "observed_span": name, "usable_for_association": usable}


def test_continued_segments_join_the_meeting_and_headings_split_it():
    meetings = meeting_ids([
        segment("V01-P0001-S01", "V01-P0001", heading="A COURT HELD"),
        segment("V01-P0002-S01", "V01-P0002", source="continued_meeting"),
        segment("V01-P0002-S02", "V01-P0002", heading="A COURT HELD", source="meeting_heading"),
    ])
    assert meetings["V01-P0001-S01"]["meeting_id"] == meetings["V01-P0002-S01"]["meeting_id"]
    assert meetings["V01-P0002-S02"]["meeting_id"] != meetings["V01-P0002-S01"]["meeting_id"]


def test_proximity_background_and_unlinked_people():
    meetings = meeting_ids([segment("V04-P0001-S01", "V04-P0001", heading="x")])
    text = "Sir Francis Wyatt Governor at James Citty" + " " * 2000 + "and Virginia"
    rows = [
        mention("V04-P0001", "V04-P0001-S01", "PERSON", "Francis Wyatt", "Francis Wyatt", 4, 17),
        mention("V04-P0001", "V04-P0001-S01", "PERSON", "", "m' Nobody", 20, 29),          # unlinked: ignored
        mention("V04-P0001", "V04-P0001-S01", "PLACE", "NW-003", "James City", 30, 41),
        mention("V04-P0001", "V04-P0001-S01", "PLACE", "NW-001", "Virginia", 2046, 2054),
        mention("V04-P0001", "V04-P0001-S01", "PLACE", "NW-004", "James River", 0, 3, usable="no"),
    ]
    links, evidence = build(rows, meetings, {"V04-P0001": text})
    by_target = {r["target_name"]: r for r in links}
    assert set(by_target) == {"James City", "Virginia"}
    assert by_target["James City"]["close_meetings"] == 1 and not by_target["James City"]["background"]
    assert by_target["Virginia"]["close_meetings"] == 0 and by_target["Virginia"]["background"] == "yes"
    assert {e["person"] for e in evidence} == {"Francis Wyatt"}
