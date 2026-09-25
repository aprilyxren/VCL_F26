"""Filter the person review queue to definite-looking two-part names.

This does not resolve people or alter the source queue. It selects rows whose
observed span is exactly ``First Last`` or ``m'/M'/s'/S' First Last``.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’.-]*"
TITLE_TOKEN = r"(?i:captain|captaine|capt\.?|treasurer|deputie|deputy|sir|lady|lord|chief|king|queen|weroance|doctor|dr\.?|master|mistress|mr\.?|mrs\.?|governor|[mMsS][\'’*]+)"
NON_NAME_INITIALS = {
    "captain", "captaine", "capt", "sir", "lady", "lord", "chief",
    "king", "queen", "weroance", "doctor", "dr", "master", "mistress",
    "mr", "mrs", "miss", "governor", "deputy", "deputie", "treasurer",
    "counsel", "counsell", "councell", "councel",
}
TITLE_NORMALIZATION = {
    "captain": "Captain", "captaine": "Captain", "capt": "Captain", "capt.": "Captain",
    "treasurer": "Treasurer", "deputie": "Deputy", "deputy": "Deputy",
    "sir": "Sir", "lady": "Lady", "lord": "Lord", "chief": "Chief",
    "king": "King", "queen": "Queen", "weroance": "Weroance",
    "doctor": "Doctor", "dr": "Doctor", "dr.": "Doctor",
    "master": "Master", "mistress": "Mistress", "mr": "Mr", "mr.": "Mr",
    "mrs": "Mrs", "mrs.": "Mrs", "governor": "Governor",
}
NON_NAME_LAST_TOKENS = {
    "m", "s", "mr", "sir", "capt", "captain", "captaine", "lady", "lord",
}
NAME_PATTERN = re.compile(
    rf"\s*(?:(?P<title>{TITLE_TOKEN})\s+)?"
    rf"(?P<first>{NAME_TOKEN})\s+(?P<last>{NAME_TOKEN})\s*"
)


def filter_queue(source: Path, output: Path, remainder_output: Path | None = None) -> int:
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        extra_fields = [
            "matched_title",
            "matched_title_normalized",
            "matched_first_name",
            "matched_last_name",
            "filter_reason",
            "person_pattern_status",
        ]
        output_fields = fieldnames + [field for field in extra_fields if field not in fieldnames]
        matches = []
        remainder = []
        for row in reader:
            span = row.get("observed_span", "")
            candidates = [
                candidate for candidate in NAME_PATTERN.finditer(span)
                if candidate.group("last").casefold().rstrip(".,:;") not in NON_NAME_LAST_TOKENS
                and "\n\n" not in candidate.group(0)
                and (
                    candidate.group("title")
                    or (
                    candidate.group("first").casefold().rstrip(".,:;") not in NON_NAME_INITIALS
                    )
                )
            ]
            match = None
            if candidates:
                # Prefer a title-bearing candidate when it has the same two
                # name tokens as a later untitled candidate. Otherwise use the
                # last valid candidate inside the overlong span.
                for candidate in candidates:
                    if not candidate.group("title"):
                        continue
                    for other in candidates:
                        if (
                            other.group("first").casefold() == candidate.group("first").casefold()
                            and other.group("last").casefold() == candidate.group("last").casefold()
                        ):
                            match = candidate
                            break
                    if match is not None:
                        break
                if match is None:
                    match = candidates[-1]
            if match is None:
                remainder.append(row)
                continue
            title = match.group("title") or ""
            raw_match = match.group(0)
            leading_space = len(raw_match) - len(raw_match.lstrip())
            matched_text = raw_match.strip()
            matched_text = matched_text.rstrip(" .,:;!?)]}")
            relative_start = match.start() + leading_space
            row["span_start"] = str(int(row.get("span_start", 0)) + relative_start)
            row["span_end"] = str(int(row["span_start"]) + len(matched_text))
            row["observed_span"] = matched_text
            row["occurrence_id"] = (
                f"{row['page_id']}-M{int(row['span_start']):05d}-"
                f"{int(row['span_end']):05d}"
            )
            row["name_string"] = f"{match.group('first')} {match.group('last')}"
            row["matched_title"] = title
            title_key = title.casefold()
            if title_key.startswith("m") and title_key not in TITLE_NORMALIZATION:
                normalized_title = "Mr"
            elif title_key.startswith("s") and title_key not in TITLE_NORMALIZATION:
                normalized_title = "Sir"
            else:
                normalized_title = TITLE_NORMALIZATION.get(title_key, title)
            row["matched_title_normalized"] = normalized_title
            row["matched_first_name"] = match.group("first")
            row["matched_last_name"] = match.group("last")
            row["filter_reason"] = (
                "definite_titled_two_part_name" if title else "definite_untitled_two_part_name"
            )
            row["person_pattern_status"] = "definite_person"
            matches.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=output_fields)
            writer.writeheader()
            writer.writerows(rows)

    write_rows(output, matches)
    if remainder_output is not None:
        write_rows(remainder_output, remainder)
    return len(matches)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--remainder-output", type=Path)
    args = parser.parse_args()
    print(f"Exported {filter_queue(args.source, args.output, args.remainder_output)} filtered rows")
