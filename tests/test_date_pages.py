import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from date_pages import date_pages, heading_starts, meeting_segments, parse_dates  # noqa: E402

VOL1 = (1619, 1622)
VOL2 = (1622, 1624)


@pytest.mark.parametrize("text, years, expected", [
    ("JULY 16, 1621 515", VOL1, (date(1621, 7, 16), date(1621, 7, 16))),
    ("NOVEMBER 4, 1623, TO MAY 24, 1624 373", (1623, 1626), (date(1623, 11, 4), date(1624, 5, 24))),
    ("MARCI! 30, 1624 473", (1623, 1626), (date(1624, 3, 30), date(1624, 3, 30))),
    ("MAY I7, 1620 349", VOL1, (date(1620, 5, 17), date(1620, 5, 17))),
    ("NOVEMBER, 1619 269", VOL1, (date(1619, 11, 1), date(1619, 11, 30))),
    ("MARCH 3, 1623/4", (1623, 1626), (date(1624, 3, 3), date(1624, 3, 3))),
])
def test_running_heads(text, years, expected):
    parsed = parse_dates(text, years)
    assert (parsed.start, parsed.end) == expected


@pytest.mark.parametrize("text, expected", [
    ("JUNE 19, 1692 55", date(1622, 6, 19)),    # 2 read as 9
    ("JANUARY 99, 1623 191", date(1623, 1, 29)),
    ("NOVEMBER 87, 1622", date(1622, 11, 27)),  # 2 read as 8
])
def test_ocr_digit_repairs_are_flagged(text, expected):
    parsed = parse_dates(text, VOL2, prefer=date(1622, 6, 1))
    assert parsed.start == expected
    assert parsed.corrected


@pytest.mark.parametrize("text, expected", [
    ("VIRGINIA THE 21 oF JANUARY 1621", date(1622, 1, 21)),      # Old Style year
    ("HOUSE THE XXVIJ™ OF IvNE 1620", date(1620, 6, 27)),        # roman day, u/v
    ("IN THE AFTERNOONE THE Xj™ OF IvNE 1621.", date(1621, 6, 11)),
    ("NOUEMBER THE THIRD 1619:", date(1619, 11, 3)),             # ordinal after month
    ("THE i3™ oF Marc# 1621", date(1622, 3, 13)),
    ("ON WEDENSDAY THE 4™ DECEMBER 162]", date(1621, 12, 4)),
    ("Y* LAST OF JANUARY 1619", date(1620, 1, 31)),
])
def test_meeting_headings(text, expected):
    assert parse_dates(text, VOL1, old_style=True).start == expected


def test_prose_is_not_a_date():
    assert parse_dates("the jury 12 1620", VOL1) is None


def test_present_list_marks_a_meeting_but_prose_does_not():
    text = "JUNE 14, 1619 229\n\nbusiness of the last court.\n\nIUNE THE 14 1619\n\nPRESENT\n\nS' Edwin Sandys.\nwho shalbe present.\n"
    starts = heading_starts(text)
    assert [text[s:s + 16] for s in starts] == ["IUNE THE 14 1619"]


def test_court_book_old_style_head_moves_forward_a_year():
    pages = [
        {"page_id": "V01-P0001", "volume": 1, "text": "DECEMBER 13, 1620 429\n"},
        {"page_id": "V01-P0002", "volume": 1, "text": "JANUARY 29, 1620 435\n"},
        {"page_id": "V01-P0003", "volume": 1, "text": "APRIL 12, 1621 445\n"},
    ]
    rows = {row["page_id"]: row for row in date_pages(pages)}
    assert rows["V01-P0002"]["start"] == date(1621, 1, 29)
    assert rows["V01-P0002"]["source"] == "running_header_old_style"


def test_bare_year_reads_as_the_whole_year_only_when_asked():
    assert parse_dates("1622 537", (1606, 1622)) is None
    parsed = parse_dates("1622 537", (1606, 1622), year_only=True)
    assert (parsed.start, parsed.end) == (date(1622, 1, 1), date(1622, 12, 31))
    assert parse_dates("MARCH 4, 1622/3 1622", (1606, 1626), year_only=True).end == date(1623, 3, 4)


def test_document_heading_dates_the_document_across_pages():
    pages = [
        {"page_id": "V03-P0001", "volume": 3, "text": (
            "OCTOBER 24, 1621 510\n\nXIV. Privy Councit. ORDER TO THE VIRGINIA COMPANY\n\n"
            "OcToBER 24, 1621\n\nPrivy Council Register\nList of Records No. 280\n\nAtt Whitehall the 24 of October\n")},
        {"page_id": "V03-P0002", "volume": 3, "text": (
            "1622 511\n\nthe rest of the order.\n\nXV. Epwarp WATERHOUSE. A RELATION OF THE\nBARBAROUS MASSACRE 1622\n\n"
            "1622\n\nPrinted.\n\nList of Records Nos. 293, 292\n\nthe Indians fell vpon them\n")},
        {"page_id": "V03-P0003", "volume": 3, "text": "1622 512\n\nmore of the relation\n"},
        # A numbered article is not a document: no source note follows.
        {"page_id": "V03-P0004", "volume": 3, "text": "1622 513\n\nXVI. It shall be lawfull at a general Court\n"},
    ]
    overrides = {"XV": {"start": "1622-03-22", "end": "1622-12-31"}}
    segments = meeting_segments(pages, date_pages(pages), overrides)
    dated = [(s["page_id"], s["date_source"], s["date_start"]) for s in segments]
    assert ("V03-P0001", "document_heading", date(1621, 10, 24)) in dated
    assert ("V03-P0002", "continued_document", date(1621, 10, 24)) not in dated
    assert ("V03-P0002", "document_heading", date(1622, 3, 22)) in dated
    assert ("V03-P0003", "continued_document", date(1622, 3, 22)) in dated
    assert ("V03-P0004", "continued_document", date(1622, 3, 22)) in dated
