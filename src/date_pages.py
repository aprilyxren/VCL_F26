"""Assign dates to primary-text pages and to the meetings within them.

The printed edition puts an editorial date in the running head of most
recto pages (``JULY 16, 1621 515``); verso pages read ``576 RECORDS OF THE
VIRGINIA COMPANY``. That running head is far more regular than the OCR of
the meeting headings, so dates are layered from most to least reliable:

1. ``running_header``: parsed from the first lines of the page, tolerating
   OCR damage (fuzzy month names, ``80`` for ``30``, Old Style ``1623/4``).
2. Order check: the records are chronological within a volume, so a header
   that breaks the order of its neighbours is downgraded to ``low``.
3. ``inherited`` / ``between_headers``: undated pages take the date of the
   surrounding dated pages, or the range between them when they differ.
4. Meeting segments: headings such as ``A COURT HELD ... 28 APRILL 1619``
   split a page; a heading date is used only when it falls within the page's
   header range (with a small tolerance), otherwise the page date is kept.

Outputs are reviewable CSVs; observed text is never altered.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import get_close_matches
from pathlib import Path

from source_loader import load_primary_pages


MONTH_FORMS = {
    1: ["january", "ianuary", "januarie", "ianuarie", "jan", "ian"],
    2: ["february", "februarie", "febraury", "ffebruary", "ffebruarie", "feb", "ffeb"],
    3: ["march", "marche", "mar"],
    4: ["april", "aprill", "aprile", "apr"],
    5: ["may", "maie"],
    6: ["june", "iune", "jun", "iun"],
    7: ["july", "iuly", "julie", "iulie", "jul", "iul"],
    8: ["august", "auguste", "aug"],
    9: ["september", "septembre", "sept", "sep"],
    10: ["october", "octobre", "octob", "oct"],
    11: ["november", "nouember", "novemb", "nouemb", "nov", "nou"],
    12: ["december", "decembre", "decemb", "dec"],
}
# Latin genitive forms used in clerks' headings (``THE 12 IUNIJ 1620``).
LATIN_MONTH_FORMS = {
    1: ["ianuarii", "ianuarij", "januarii"], 2: ["februarii", "februarij", "ffebruarii"],
    3: ["martii", "martij"], 4: ["aprilis"], 5: ["maii", "maij"], 6: ["iunii", "iunij", "iunis"],
    7: ["iulii", "iulij"], 8: ["augusti"], 9: ["septembris"], 10: ["octobris"],
    11: ["nouembris", "novembris"], 12: ["decembris"],
}
for _month, _forms in LATIN_MONTH_FORMS.items():
    MONTH_FORMS[_month].extend(_forms)
FORM_TO_MONTH = {form.replace("v", "u"): month for month, forms in MONTH_FORMS.items() for form in forms}
FUZZY_FORMS = [form for form in FORM_TO_MONTH if len(form) >= 4]

ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fowerth": 4, "fifth": 5, "fift": 5,
    "sixth": 6, "sixt": 6, "seventh": 7, "seaventh": 7, "eighth": 8, "eight": 8, "ninth": 9,
    "tenth": 10, "eleventh": 11, "twelfth": 12, "twelueth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "twentith": 20,
}
YEAR_MIN, YEAR_MAX = 1580, 1630
# A heading date may differ slightly from the page's running head (a meeting
# that starts on the page before the head's date changes).
HEADING_TOLERANCE = timedelta(days=45)
# The Court Book volumes are strictly chronological, so a step back there is
# evidence of an Old Style head; Volumes III-IV are document collections in
# rough order with range heads, where that inference does not hold.
OLD_STYLE_HEAD_VOLUMES = {1, 2}
# How far back a running head may step before it is treated as Old Style.
SHIFT_SLACK = timedelta(days=30)

# A digit run may carry OCR junk (``1}``); the junk marks the day as uncertain.
TOKEN_RE = re.compile(r"[A-Za-z!|]{2,}\.?|\d{1,5}(?:/\d{1,2})?[}\]|!]?|[,;]|\bTO\b", re.IGNORECASE)
ROMAN_RE = re.compile(r"(?i)[ivxj]{1,7}")
ROMAN_VALUES = {"i": 1, "j": 1, "v": 5, "x": 10}
LAST_DAY_WORDS = {"last", "vltimo", "ultimo"}
RECORDS_HEAD_RE = re.compile(r"(?i)RECORDS\s+OF\s+T[HI]E\s+VIRGINIA")
MEETING_HEADING_RE = re.compile(
    r"(?i)\b(?:court|courte|comittee|committee|counsell|councell|assemblie|assembly|"
    r"meetinge|meeting)[^\n]{0,40}?\b(?:held|helde|holden|houlden)\b"
)
# A court's attendance list: ``PRESENT``, ``|| PRESENT||``, ``THER BEINGE PRESENT``.
# Case-sensitive: lowercase "present" is prose ("shalbe present.").
PRESENT_RE = re.compile(r"(?m)^[^\n]{0,40}\b(?:PRESENT|Present)\b\W*$")
HEADING_LOOKBACK = 300


@dataclass(frozen=True)
class ParsedDate:
    start: date
    end: date
    corrected: bool


def month_of(token: str) -> int | None:
    folded = token.casefold().replace("i!", "h").replace("i|", "h")  # MARCI! = MARCH
    word = re.sub(r"[^a-z]", "", folded.replace("!", "l").replace("|", "l"))
    word = word.replace("v", "u")  # u/v were interchangeable (IVNE = IUNE)
    if len(word) < 3:
        return None
    if word in FORM_TO_MONTH:
        return FORM_TO_MONTH[word]
    # 0.8 accepts IVULY -> iuly but rejects jury -> july.
    match = get_close_matches(word, FUZZY_FORMS, n=1, cutoff=0.8)
    return FORM_TO_MONTH[match[0]] if match else None


# Years each volume can contain; used to repair OCR-damaged years (1692 -> 1622).
VOLUME_YEARS = {1: (1619, 1622), 2: (1622, 1624), 3: (1606, 1622), 4: (1623, 1626)}
# OCR digit confusions seen in the running heads, applied to the year's last two digits.
DIGIT_REPAIRS = {"9": "2", "8": "23", "0": "2", "3": "8"}


def year_candidates(token: str) -> list[tuple[int, bool]]:
    """Return ``(year, repaired)`` readings of a year token, raw reading first.

    ``1623/4`` is Old Style dual dating: the later year is the historical year.
    A five-digit run like ``16823`` is usually ``1622/3`` with the slash misread.
    """
    match = re.fullmatch(r"(\d{4,5})(?:/(\d{1,2}))?", token)
    if not match:
        return []
    digits, tail = match.group(1), match.group(2)
    readings = []
    if len(digits) == 4:
        readings.append((digits, tail, False))
    else:
        readings.append((digits[:3] + digits[4], None, True))
        readings.append((digits[:4], None, True))
    out: list[tuple[int, bool]] = []
    for value, slash_tail, repaired in readings:
        variants = {(value, repaired)}
        for position in (2, 3):
            for wrong, rights in DIGIT_REPAIRS.items():
                if value[position] == wrong:
                    for right in rights:
                        variants.add((value[:position] + right + value[position + 1:], True))
        for variant, was_repaired in sorted(variants, key=lambda item: item[1]):
            year = int(variant)
            if slash_tail:
                year = int(variant[: 4 - len(slash_tail)] + slash_tail)
            if YEAR_MIN <= year <= YEAR_MAX and (year, was_repaired) not in out:
                out.append((year, was_repaired))
    return out


def day_of(token: str) -> tuple[int | None, bool]:
    if token[-1:] in "}]|!" and token[:-1].isdigit():
        day, _ = day_of(token[:-1])
        return day, day is not None
    if not token.isdigit() or len(token) > 3:
        return None, False
    day = int(token)
    if 1 <= day <= 31:
        return day, False
    # OCR reads 3 and 2 as 8, and 2 as 9 (``JULY 80``, ``NOVEMBER 87``, ``JANUARY 99``).
    if len(token) == 2:
        for wrong, right in (("8", "3"), ("8", "2"), ("9", "2")):
            fixed = int(token.replace(wrong, right, 1))
            if 1 <= fixed <= 31:
                return fixed, True
    # A superscript ``th`` is often read as 7 (``297`` = 29th, ``47`` = 4th).
    if token.endswith("7") and 1 <= int(token[:-1] or 0) <= 31:
        return int(token[:-1]), True
    return None, False


def roman_day(token: str) -> int | None:
    """Read ``XXVIJ`` / ``Xj`` (j is a final i) as a day of the month."""
    if re.fullmatch(r"(?i)[ivxjl]{2,7}", token) and re.search(r"(?i)[vxj]", token):
        token = re.sub(r"(?i)l", "i", token)  # VILJ = VIIJ (8)
    if not ROMAN_RE.fullmatch(token):
        return None
    values = [ROMAN_VALUES[char] for char in token.casefold()]
    total = sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    return total if 1 <= total <= 31 else None


def old_style_to_new(value: date) -> date:
    """English civil years began on 25 March: Old Style 21 Jan 1621 is 21 Jan 1622."""
    if value.month < 3 or (value.month == 3 and value.day <= 24):
        return safe_date(value.year + 1, value.month, value.day) or value
    return value


def safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def month_end(year: int, month: int) -> date:
    return (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)


def parse_dates(
    text: str,
    year_range: tuple[int, int] = (YEAR_MIN, YEAR_MAX),
    prefer: date | None = None,
    old_style: bool = False,
) -> ParsedDate | None:
    """Parse month-first (``JULY 30, 31, 1619``, ``NOVEMBER 4, 1623, TO MAY 24,
    1624``), day-first (``28 APRILL 1619``, ``THE FIRST OF IVLY 1623``) and
    month-only (``NOVEMBER, 1619``) dates.

    A year must fall in ``year_range``; an OCR-repaired reading is used only
    when the raw one does not, choosing the reading closest to ``prefer``.
    With ``old_style`` (original document dates), 1 Jan-24 Mar moves to the
    next year unless the year was written dual-dated (``1621/2``).
    """
    text = re.sub(r"(?<![A-Za-z])[iIl](?=\d)", "1", text)  # I7, i3 -> 17, 13
    text = re.sub(r"(?<![A-Za-z0-9])[iIl](?=6\d\d)", "1", text)  # i621 -> 1621
    text = re.sub(r"(?<=16\d)[\]|!lI](?![A-Za-z0-9])", "1", text)  # 162] -> 1621
    year_range = (year_range[0] - (1 if old_style else 0), year_range[1])
    last_day = False
    tokens = TOKEN_RE.findall(text)
    found: list[tuple[date, date]] = []
    corrected = False
    month: int | None = None
    pending: list[tuple[int, int]] = []
    orphan_day: int | None = None  # a day seen before its month (day-first)
    for token in tokens:
        if token in {",", ";"} or token.upper() == "TO":
            continue
        candidates = [(y, r) for y, r in year_candidates(token) if year_range[0] <= y <= year_range[1]]
        if candidates:
            raw = [c for c in candidates if not c[1]]
            if raw:
                year, repaired = raw[0]
            else:
                target = prefer.year if prefer else candidates[0][0]
                year, repaired = min(candidates, key=lambda c: abs(c[0] - target))
            corrected = corrected or repaired
            dual_dated = "/" in token
            days = [d for m, day in pending if (d := safe_date(year, m, day))]
            if not days and month is not None and last_day:
                days = [month_end(year, month)]
            if days:
                if old_style and not dual_dated:
                    days = [old_style_to_new(d) for d in days]
                found.extend((d, d) for d in days)
            elif month is not None and orphan_day is None:
                found.append((date(year, month, 1), month_end(year, month)))
            pending, orphan_day, month, last_day = [], None, None, False
            continue
        if re.fullmatch(r"\d{4,5}(?:/\d{1,2})?", token):
            pending, orphan_day, month = [], None, None  # a year outside the range
            continue
        word = token.casefold().rstrip(".")
        if word in ORDINALS:
            if month is not None:
                pending.append((month, ORDINALS[word]))  # NOUEMBER THE THIRD
            else:
                orphan_day = ORDINALS[word]
            continue
        if word in LAST_DAY_WORDS:
            last_day = True
            continue
        roman = roman_day(word)
        if roman is not None:
            if month is not None:
                pending.append((month, roman))
            else:
                orphan_day = roman
            continue
        candidate = month_of(token) if not token.isdigit() else None
        if candidate is not None:
            month = candidate
            if orphan_day is not None:
                pending.append((month, orphan_day))
                orphan_day = None
            continue
        day, fixed = day_of(token)
        if day is None:
            continue
        corrected = corrected or fixed
        if month is not None:
            pending.append((month, day))
        else:
            orphan_day = day
    if not found:
        return None
    return ParsedDate(min(start for start, _ in found), max(end for _, end in found), corrected)


def first_line(page_text: str) -> str:
    return next((line.strip() for line in page_text.split("\n") if line.strip()), "")


def date_pages(pages: list[dict]) -> list[dict]:
    rows = []
    for page in pages:
        first = first_line(page["text"])
        is_records_head = bool(RECORDS_HEAD_RE.search(first))
        year_range = VOLUME_YEARS.get(page["volume"], (YEAR_MIN, YEAR_MAX))
        parsed = None if is_records_head else parse_dates(first, year_range)
        rows.append({
            "page_id": page["page_id"], "volume": page["volume"], "header_line": first[:120],
            "start": parsed.start if parsed else None, "end": parsed.end if parsed else None,
            "source": "running_header" if parsed else "", "corrected": bool(parsed and parsed.corrected),
            "_header": "" if is_records_head else first, "_years": year_range,
        })

    for volume in sorted({row["volume"] for row in rows}):
        volume_rows = [row for row in rows if row["volume"] == volume]
        anchors = [i for i, row in enumerate(volume_rows) if row["start"] and not row["corrected"]]

        def neighbours(index: int) -> tuple[date | None, date | None]:
            before = max((i for i in anchors if i < index), default=None)
            after = min((i for i in anchors if i > index), default=None)
            return (volume_rows[before]["end"] if before is not None else None,
                    volume_rows[after]["start"] if after is not None else None)

        # Re-read repaired headers, preferring the reading nearest the clean
        # neighbouring heads (``1628`` between two 1622 pages becomes 1622).
        for index, row in enumerate(volume_rows):
            if row["corrected"] and row["_header"]:
                previous, following = neighbours(index)
                reparsed = parse_dates(row["_header"], row["_years"], prefer=previous or following)
                if reparsed:
                    row.update(start=reparsed.start, end=reparsed.end)

        # In the strictly chronological Court Book, a running head that steps
        # back about a year is an Old Style head (``JANUARY 29, 1620`` after
        # ``DECEMBER 13, 1620``) or a year misread; adding a year fixes both
        # when it restores the order.
        latest = None
        for row in volume_rows if volume in OLD_STYLE_HEAD_VOLUMES else []:
            if not row["start"]:
                continue
            if latest is not None and row["start"] < latest - SHIFT_SLACK:
                shifted = safe_date(row["start"].year + 1, row["start"].month, row["start"].day)
                if shifted and latest - SHIFT_SLACK <= shifted <= latest + timedelta(days=120):
                    end = safe_date(row["end"].year + 1, row["end"].month, row["end"].day) or shifted
                    kind = "old_style" if old_style_to_new(row["start"]) != row["start"] else "year_fixed"
                    row.update(start=shifted, end=end, source=f"running_header_{kind}")
            latest = max(latest, row["start"]) if latest else row["start"]

        # Order check: a header outside its consistent neighbours is low.
        for index, row in enumerate(volume_rows):
            if not row["start"]:
                continue
            previous, following = neighbours(index)
            out_of_order = (
                previous is not None and following is not None and previous <= following
                and not previous <= row["start"] <= following
            )
            row["confidence"] = "low" if out_of_order or row["corrected"] else "high"

        # Fill undated pages from the nearest trusted neighbours.
        trusted = [i for i, row in enumerate(volume_rows) if row["start"] and row["confidence"] == "high"]
        for index, row in enumerate(volume_rows):
            if row["start"]:
                continue
            before = max((i for i in trusted if i < index), default=None)
            after = min((i for i in trusted if i > index), default=None)
            if before is None and after is None:
                row.update(source="undated", confidence="none")
                continue
            low = volume_rows[before]["end"] if before is not None else volume_rows[after]["start"]
            high = volume_rows[after]["start"] if after is not None else volume_rows[before]["end"]
            if low > high:
                low, high = high, low
            same = low == high
            row.update(
                start=low, end=high,
                source="inherited" if same else "between_headers",
                confidence="medium" if same or (high - low).days <= 31 else "low",
            )
    return rows


def is_heading(text: str, line_start: int, match: re.Match) -> bool:
    """A real meeting heading starts its line and is set in capitals, or reads
    ``At a (Quarter/Great and Generall/...) Court held for/at/on``."""
    prefix = text[line_start:match.start()]
    if len(prefix) > 30:
        return False
    line_end = text.find("\n", match.start())
    line = text[line_start: line_end if line_end >= 0 else len(text)]
    letters = [char for char in line[:60] if char.isalpha()]
    upper = sum(char.isupper() for char in letters) / max(1, len(letters))
    styled = re.search(
        r"(?i)^\W*(?:at\s+)?a\s+(?:great\s+and\s+generall\s+|quarter\s+|preparatiue\s+|"
        r"extraordinar\w*\s+|generall\s+)?(?:court|courte)\s+(?:held|holden|houlden)\b", line)
    return upper >= 0.5 or bool(styled)


def heading_starts(text: str) -> list[int]:
    """Line offsets where a meeting begins.

    A meeting begins at a ``COURT HELD`` heading line, or at the date line(s)
    just above a ``PRESENT`` list (many courts are headed only
    ``MAY THE 26 1619``). The page's first line, the running head, is never
    part of a heading. Starts within one heading block are merged.
    """
    first_line_end = text.find("\n") + 1 if text.strip() else 0
    starts = []
    for match in MEETING_HEADING_RE.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if line_start >= first_line_end and is_heading(text, line_start, match):
            starts.append(line_start)
    for match in PRESENT_RE.finditer(text):
        floor = max(first_line_end, match.start() - HEADING_LOOKBACK)
        block_start = match.start()
        # Walk back over non-empty lines while they carry a date or heading.
        cursor = match.start()
        for _ in range(4):
            previous_end = text.rfind("\n", 0, max(0, cursor - 1))
            line_start = previous_end + 1
            if line_start < floor:
                break
            line = text[line_start:cursor]
            if line.strip() and (parse_dates(line, (YEAR_MIN, YEAR_MAX)) or MEETING_HEADING_RE.search(line)
                                 or month_line(line)):
                block_start = line_start
            elif line.strip():
                break
            cursor = line_start
        starts.append(block_start)
    merged: list[int] = []
    for start in sorted(set(starts)):
        if merged and start - merged[-1] < HEADING_LOOKBACK and not PRESENT_RE.search(text[merged[-1]:start]):
            continue
        merged.append(start)
    return merged


def month_line(line: str) -> bool:
    """True for a short date line without a year (``DECEMBER—THE XV``)."""
    words = re.findall(r"[A-Za-z]{3,}", line)
    return len(line.strip()) <= 40 and any(month_of(word) for word in words)


def meeting_segments(pages: list[dict], page_rows: list[dict]) -> list[dict]:
    by_id = {row["page_id"]: row for row in page_rows}
    segments = []
    current = None  # (start, end, source, confidence) of the meeting in progress
    current_volume = None
    last_meeting: date | None = None
    for page in pages:
        text = page["text"]
        page_row = by_id[page["page_id"]]
        if page["volume"] != current_volume:
            current, current_volume, last_meeting = None, page["volume"], None
        year_range = VOLUME_YEARS.get(page["volume"], (YEAR_MIN, YEAR_MAX))
        cuts = {0: ("", None)}
        for line_start in heading_starts(text):
            present = PRESENT_RE.search(text, line_start)
            block_end = present.end() if present and present.start() - line_start < HEADING_LOOKBACK + 200 \
                else min(len(text), line_start + 260)
            block = text[line_start:block_end]
            # Clerks mostly wrote Old Style years but not always: keep both
            # readings and let the page chronology choose.
            readings = [
                reading for style in (True, False)
                if (reading := parse_dates(block, year_range, prefer=page_row["start"], old_style=style))
            ]
            cuts[line_start] = (" ".join(block.split())[:160], readings)
        ordered = sorted(cuts)
        page_date = (page_row["start"], page_row["end"], page_row["source"], page_row.get("confidence", "none"))
        for index, start in enumerate(ordered):
            end = ordered[index + 1] if index + 1 < len(ordered) else len(text)
            heading, readings = cuts[start]
            parsed = None
            if heading and readings and page_row["start"]:
                lo = page_row["start"] - HEADING_TOLERANCE
                hi = page_row["end"] + HEADING_TOLERANCE
                fits = [r for r in readings if lo <= r.start <= hi]
                # In the chronological Court Book, a doubtful running head
                # defers to the previous meeting date.
                if page_row.get("confidence") != "high" and last_meeting \
                        and page["volume"] in OLD_STYLE_HEAD_VOLUMES:
                    follows = [r for r in readings
                               if last_meeting - SHIFT_SLACK <= r.start <= last_meeting + timedelta(days=120)]
                    fits = follows or fits
                parsed = fits[0] if fits else None
            if heading and readings and page_row["start"]:
                if parsed:
                    current = (parsed.start, parsed.end, "meeting_heading",
                               "medium" if parsed.corrected else "high")
                    value = current
                    last_meeting = parsed.start
                else:
                    current = None
                    value = (*page_date[:2], f"{page_date[2]}; heading_date_rejected", page_date[3])
            elif heading:
                current = None
                value = (*page_date[:2], f"{page_date[2]}; heading_undated", page_date[3])
            elif start == 0 and current and page_row["start"] and (
                page_row["start"] - HEADING_TOLERANCE <= current[0] <= page_row["end"] + HEADING_TOLERANCE
            ):
                value = (*current[:2], "continued_meeting", current[3])
            else:
                value = page_date
            segments.append({
                "segment_id": f"{page['page_id']}-S{index + 1:02d}",
                "page_id": page["page_id"], "char_start": start, "char_end": end,
                "heading": heading, "date_start": value[0], "date_end": value[1],
                "date_source": value[2], "confidence": value[3],
            })
    return segments


def iso(value) -> str:
    return value.isoformat() if value else ""


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: iso(row.get(field)) if isinstance(row.get(field), date) else row.get(field, "")
                             for field in fields})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--page-output", type=Path, required=True)
    parser.add_argument("--segment-output", type=Path, required=True)
    args = parser.parse_args()
    pages = load_primary_pages(args.source)
    page_rows = date_pages(pages)
    segments = meeting_segments(pages, page_rows)
    write_csv(args.page_output, ["page_id", "volume", "start", "end", "source", "confidence", "header_line"],
              page_rows)
    write_csv(args.segment_output, ["segment_id", "page_id", "char_start", "char_end", "date_start", "date_end",
                                    "date_source", "confidence", "heading"], segments)
    sources = {}
    for row in page_rows:
        key = f"{row['source']}/{row.get('confidence', '')}"
        sources[key] = sources.get(key, 0) + 1
    print(f"Dated {len(page_rows)} pages: {dict(sorted(sources.items()))}")
    print(f"Wrote {len(segments)} segments "
          f"({sum(1 for s in segments if s['date_source'] == 'meeting_heading')} dated by meeting heading)")


if __name__ == "__main__":
    main()
