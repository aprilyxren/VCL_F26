"""Build person <-> place / Indigenous-people associations from NER mentions.

Unit: a *meeting*. Segments from ``text_segments.csv`` are chained: a segment
with a heading starts a meeting, a ``continued_meeting`` segment joins the one
before it, and any other page is its own dated unit (most Volume III-IV
documents). Only mentions marked ``usable_for_association`` count, and only
people linked to a dictionary key (unidentified names are not nodes).

For each person and target (place, Indigenous group, or Indigenous people
referred to collectively) in the same meeting, the closest pair of mentions on
the same page gives a character distance and a proximity class:

* ``close``: within 300 characters (usually the same sentence or entry);
* ``near``: within 1,000 characters;
* ``same_meeting``: further apart, or on different pages of the meeting.

Virginia, England, and London are flagged ``background``: they co-occur with
almost everyone. ``lift`` compares how often a pair shares a meeting with
what their separate frequencies would predict (above 1 = more than chance).

Outputs: one row per pair (``person_place_links.csv``), one row per pair per
meeting with the closest text as evidence (``person_place_evidence.csv``), and
optionally one row per year, person, and target (``person_place_by_year.csv``).
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from source_loader import load_primary_pages


CLOSE, NEAR = 300, 1000
BACKGROUND = {"NW-001", "OW-011", "OW-001"}  # Virginia, England, London
TARGET_LABELS = {"PLACE": "place", "INDIGENOUS_GROUP": "indigenous_group",
                 "INDIGENOUS_COLLECTIVE": "indigenous_collective"}
COLLECTIVE_KEY = "INDIGENOUS (collective reference)"

LINK_FIELDS = [
    "person", "target_id", "target_name", "target_type", "background", "meetings", "close_meetings",
    "near_meetings", "min_distance", "lift", "first_date", "last_date", "volumes", "example",
]
BY_YEAR_FIELDS = [
    "year", "person", "target_name", "target_type", "background", "meetings", "close_meetings",
    "min_distance", "date_confidence", "first_date", "last_date", "example",
]
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3, "": 3}
EVIDENCE_FIELDS = [
    "person", "target_id", "target_name", "target_type", "meeting_id", "date_start", "date_end",
    "date_confidence", "proximity", "distance", "page_id", "person_span", "target_span", "snippet",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def meeting_ids(segments: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Map segment_id -> meeting record (id, dates, confidence)."""
    meetings: dict[str, dict[str, str]] = {}
    current = None
    current_volume = None
    for segment in segments:  # file order is page order, then position
        volume = segment["page_id"][:3]
        starts_new = (
            segment["heading"] or current is None or volume != current_volume
            or not segment["date_source"].startswith("continued_meeting")
        )
        if starts_new:
            current = {
                "meeting_id": f"M-{segment['segment_id']}",
                "date_start": segment["date_start"], "date_end": segment["date_end"],
                "date_confidence": segment["confidence"],
            }
            current_volume = volume
        meetings[segment["segment_id"]] = current
    return meetings


def distance(a: dict, b: dict) -> int | None:
    if a["page_id"] != b["page_id"]:
        return None
    a0, a1, b0, b1 = int(a["span_start"]), int(a["span_end"]), int(b["span_start"]), int(b["span_end"])
    return max(0, max(a0, b0) - min(a1, b1))


def build(mentions: list[dict], meetings: dict[str, dict], texts: dict[str, str]) -> tuple[list, list]:
    people: dict[str, list] = defaultdict(list)    # meeting -> person mentions
    targets: dict[str, list] = defaultdict(list)   # meeting -> target mentions
    for row in mentions:
        if row["usable_for_association"] != "yes" or row["segment_id"] not in meetings:
            continue
        meeting = meetings[row["segment_id"]]["meeting_id"]
        if row["label"] == "PERSON" and row["entity_key"]:
            people[meeting].append(row)
        elif row["label"] in TARGET_LABELS:
            row = dict(row)
            row["target_type"] = TARGET_LABELS[row["label"]]
            if row["target_type"] == "indigenous_collective":
                row["entity_key"], row["entity_name"] = COLLECTIVE_KEY, COLLECTIVE_KEY
            targets[meeting].append(row)
    meeting_info = {m["meeting_id"]: m for m in meetings.values()}

    evidence = []
    for meeting, person_rows in people.items():
        by_person: dict[str, list] = defaultdict(list)
        for row in person_rows:
            by_person[row["entity_key"]].append(row)
        by_target: dict[str, list] = defaultdict(list)
        for row in targets.get(meeting, []):
            by_target[row["entity_key"]].append(row)
        info = meeting_info[meeting]
        for person, p_rows in by_person.items():
            for target, t_rows in by_target.items():
                best = None
                for p in p_rows:
                    for t in t_rows:
                        d = distance(p, t)
                        if d is not None and (best is None or d < best[0]):
                            best = (d, p, t)
                if best is None:
                    d, p, t = None, p_rows[0], t_rows[0]
                else:
                    d, p, t = best
                proximity = "same_meeting" if d is None or d > NEAR else "near" if d > CLOSE else "close"
                snippet = ""
                if d is not None:
                    text = texts[p["page_id"]]
                    lo = min(int(p["span_start"]), int(t["span_start"]))
                    hi = max(int(p["span_end"]), int(t["span_end"]))
                    snippet = " ".join(text[max(0, lo - 60): min(hi + 60, lo + 700)].split())
                evidence.append({
                    "person": person, "target_id": target, "target_name": t["entity_name"],
                    "target_type": t["target_type"], "meeting_id": meeting,
                    "date_start": info["date_start"], "date_end": info["date_end"],
                    "date_confidence": info["date_confidence"], "proximity": proximity,
                    "distance": "" if d is None else d, "page_id": p["page_id"],
                    "person_span": " ".join(p["observed_span"].split()),
                    "target_span": " ".join(t["observed_span"].split()), "snippet": snippet,
                })

    # Aggregate per pair, with lift over meeting frequencies.
    total_meetings = len(set(people) | set(targets))
    person_meetings: dict[str, set] = defaultdict(set)
    target_meetings: dict[str, set] = defaultdict(set)
    for meeting, rows in people.items():
        for row in rows:
            person_meetings[row["entity_key"]].add(meeting)
    for meeting, rows in targets.items():
        for row in rows:
            target_meetings[row["entity_key"]].add(meeting)
    pairs: dict[tuple[str, str], list] = defaultdict(list)
    for row in evidence:
        pairs[(row["person"], row["target_id"])].append(row)
    links = []
    for (person, target), rows in pairs.items():
        meetings_together = len({r["meeting_id"] for r in rows})
        expected = len(person_meetings[person]) * len(target_meetings[target]) / max(1, total_meetings)
        distances = [r["distance"] for r in rows if r["distance"] != ""]
        closest = min(rows, key=lambda r: r["distance"] if r["distance"] != "" else 10 ** 9)
        dates = sorted(r["date_start"] for r in rows if r["date_start"])
        links.append({
            "person": person, "target_id": target, "target_name": rows[0]["target_name"],
            "target_type": rows[0]["target_type"], "background": "yes" if target in BACKGROUND else "",
            "meetings": meetings_together,
            "close_meetings": len({r["meeting_id"] for r in rows if r["proximity"] == "close"}),
            "near_meetings": len({r["meeting_id"] for r in rows if r["proximity"] in {"close", "near"}}),
            "min_distance": min(distances) if distances else "",
            "lift": round(meetings_together / expected, 2) if expected else "",
            "first_date": dates[0] if dates else "", "last_date": dates[-1] if dates else "",
            "volumes": ",".join(sorted({r["page_id"][1:3].lstrip("0") for r in rows})),
            "example": closest["snippet"][:300],
        })
    links.sort(key=lambda r: (-r["close_meetings"], -r["meetings"], r["person"], r["target_name"]))
    evidence.sort(key=lambda r: (r["person"], r["target_name"], r["date_start"], r["meeting_id"]))
    return links, evidence


def by_year(evidence: list[dict]) -> list[dict]:
    """One row per (year, person, target). A meeting's year is its start date's
    year; ``date_confidence`` is the best confidence among that year's meetings."""
    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in evidence:
        if row["date_start"]:
            groups[(row["date_start"][:4], row["person"], row["target_id"])].append(row)
    out = []
    for (year, person, _), rows in groups.items():
        distances = [r["distance"] for r in rows if r["distance"] != ""]
        closest = min(rows, key=lambda r: r["distance"] if r["distance"] != "" else 10 ** 9)
        dates = sorted(r["date_start"] for r in rows)
        out.append({
            "year": year, "person": person, "target_name": rows[0]["target_name"],
            "target_type": rows[0]["target_type"],
            "background": "yes" if rows[0]["target_id"] in BACKGROUND else "",
            "meetings": len({r["meeting_id"] for r in rows}),
            "close_meetings": len({r["meeting_id"] for r in rows if r["proximity"] == "close"}),
            "min_distance": min(distances) if distances else "",
            "date_confidence": min((r["date_confidence"] for r in rows), key=lambda c: CONFIDENCE_RANK.get(c, 3)),
            "first_date": dates[0], "last_date": dates[-1], "example": closest["snippet"][:300],
        })
    out.sort(key=lambda r: (r["year"], -r["close_meetings"], -r["meetings"], r["person"], r["target_name"]))
    return out


def write(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--links-output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--by-year-output", type=Path, help="One row per year, person, and target.")
    args = parser.parse_args()

    texts = {page["page_id"]: page["text"] for page in load_primary_pages(args.source)}
    meetings = meeting_ids(read_csv(args.segments))
    links, evidence = build(read_csv(args.mentions), meetings, texts)
    write(args.links_output, LINK_FIELDS, links)
    write(args.evidence_output, EVIDENCE_FIELDS, evidence)
    if args.by_year_output:
        yearly = by_year(evidence)
        write(args.by_year_output, BY_YEAR_FIELDS, yearly)
        print(f"{len(yearly)} year-level rows in {args.by_year_output}")
    print(f"{len({m['meeting_id'] for m in meetings.values()})} meetings/units; "
          f"{len(links)} person-target links from {len(evidence)} meeting-level co-occurrences")
    for kind in ("place", "indigenous_group", "indigenous_collective"):
        subset = [r for r in links if r["target_type"] == kind]
        print(f"  {kind}: {len(subset)} links, {sum(1 for r in subset if r['close_meetings'])} with a close co-mention")


if __name__ == "__main__":
    main()
