import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from date_pages import date_pages, heading_starts, parse_dates  # noqa: E402

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
