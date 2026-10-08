"""Extract typed relations between people, places, and Indigenous peoples.

Works on ``ner_mentions.csv``. Two neighbouring mentions on a page are
related when (1) no sentence boundary separates them and (2) the text between
them (sometimes just before or after) contains a cue phrase, e.g.
``Sir Francis Wyatt knight Governor and Captaine generall of Virginia``
-> ``office_in``.

Early-modern text needs care:

* A full stop is often not a sentence end: ``m' Tho: Gibbes. m' Bull.`` is a
  list, and ``Capt.``, ``S'``, ``Tho:``, ``Wm.`` are abbreviations. A boundary
  is a blank line, or a full stop/``?``/``!`` before a capital where the word
  before it is not an abbreviation, initial, title, numeral, or name list.
  Colons, semicolons, and ``&`` do not end sentences (clauses run on).
* Spellings vary: vnto/unto, vppon/upon, att/at, y° = the, w™ = with,
  gouernor/governor; line-break hyphens are joined before matching.
* ``his``/``her`` marks possession: ``At Weynoack of Sir George Yeardley his
  people`` (Yeardley's people at Weyanoke), ``X his wife``.

People in attendance lists (``mention_role=present``) are skipped: being
next to each other in a list says nothing about a relationship.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from source_loader import load_primary_pages


MAX_GAP = 160
PERSON = "person"
PLACE = "place"
GROUP = "indigenous_group"
TYPE_OF = {"PERSON": PERSON, "PLACE": PLACE, "INDIGENOUS_GROUP": GROUP}

ABBREVIATIONS = {
    "m", "mr", "mrs", "s", "st", "sr", "sir", "capt", "cap", "captaine", "dr", "d", "lo", "kn", "knt", "esq",
    "tho", "thos", "wm", "willm", "geo", "nich", "nicho", "ed", "edw", "jo", "io", "rich", "sam", "hen",
    "fra", "chr", "nath", "rob", "robt", "ro", "jas", "ja", "ald", "viz", "vizt", "no", "nos", "&c", "etc",
    "ibid", "ma", "mat", "matie", "lp", "lps", "secr", "dep", "dpt", "gent", "co", "comp", "ea", "e",
}
BOUNDARY_RE = re.compile(r"([A-Za-z’'*™&]+)?\s*[.!?]\s+(?=[A-Z])")
SUBSTITUTIONS = [
    (re.compile(r"(?<=[a-z])-\s*\n\s*(?=[a-z])"), ""),          # line-break hyphen
    (re.compile(r"\bw[™*'’]+(?:h|t)?\b|\bwth\b|\bw'h\b"), "with"),
    (re.compile(r"\by[°®*e]\b|\by°"), "the"),
    (re.compile(r"\byt\b"), "that"),
    (re.compile(r"[™*§|°®¢€]+"), ""),
    (re.compile(r"\s+"), " "),
]


def normalize(text: str) -> str:
    value = text.lower()
    for pattern, replacement in SUBSTITUTIONS:
        value = pattern.sub(replacement, value)
    return value.strip()


def has_boundary(gap: str) -> bool:
    if re.search(r"\n[ \t]*\n", gap):
        return True
    for match in BOUNDARY_RE.finditer(gap):
        word = (match.group(1) or "").casefold().strip("’'*™")
        if not word or word in ABBREVIATIONS or len(word) <= 2 or re.fullmatch(r"[ivxlcdm]+|\d+", word):
            continue
        return True
    return False


OFFICE = (r"(?:governo\w*|gouerno\w*|gouernour|captaine generall|treasur\w*|deputy|deputie|marshall|"
          r"admirall|vice-?admirall|commander|comaunder|minister|preacher|bishop|warden|alderman|mayor|maior|"
          r"burgess\w*|secretar\w*|recorder|cape ?m[ae]r?chant|lieutenant|lieuten\w*|liuetenn\w*|fellowe?|"
          r"master|president|chiefe|agent|factor|surveyor|physition|phisition|steward|clerke|clarke)")
SEAT = r"(?:house|howse|plantation|plantacon|plantaéon|divident|dividend|land|lande|lands|seat|seate|fort|store|parish)"
NUM = r"(?:#|\d+|[ivxl]+|one|two|three|foure|four|fiue|five|six|seauen|seven|ten|twenty|hundred|thousand)"
KIN = (r"(?:wife|wyfe|widdow\w*|widow\w*|relict|sonne|son|daughter|daughte\w*|brother|sister|father|mother|"
       r"vncle|uncle|nephew|neece|cosen|cousin|kinsman|kinswoman|heire|heyre|executrix)")
AGENT = (r"(?:servant\w*|seruant\w*|man|men|apprentice|factor|agent|deputy|deputie|attorney|atturney|"
         r"executor|executo\w*|administrator|assigne\w*|partner|associate|tenant\w*)")

# (subject type, object type, relation, confidence, regex on the normalized gap)
GAP_RULES: list[tuple[str, str, str, str, re.Pattern[str]]] = [
    (PERSON, PLACE, "office_in", "high", re.compile(
        rf"\b{OFFICE}\b[^;]{{0,50}}?\b(?:of|for|at|in|ouer|over)\s*(?:the\s+(?:company|colony|colonie|plantation|"
        rf"cittie|citty|city|corporation|burrough|towne|church)\s+(?:of|for|at|in)\s*)?(?:the\s+)?$")),
    (PERSON, PLACE, "commands", "high", re.compile(
        r"\b(?:command|comand|comaund|charge|gouernment|government)\b[^;]{0,30}?\b(?:of|at|ouer|over)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "property_at", "high", re.compile(
        rf"\b(?:his|her|their)\s+(?:\w+\s+)?{SEAT}\b[^;]{{0,25}}?\b(?:at|att|in|of|vppon|vpon|upon|neere|near)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "land_at", "high", re.compile(
        rf"\b(?:{NUM}\s*(?:acres?|shares?)|acres?|divident|dividend|patent|pattent)\b[^;]{{0,50}}?\b(?:at|att|in|of|"
        rf"vppon|vpon|upon|neere|near|adioyning|adjoyning|bordering|called)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "killed_at", "high", re.compile(
        r"\b(?:slaine|slayne|slain|killed|kild|massacred|murthered|murdered)\b[^;]{0,30}?\b(?:at|att|in)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "travel_to", "medium", re.compile(
        r"\b(?:sent|sente|send|sendinge|went|goe|goinge|gone|came|come|cominge|arrived|arriued|arriving|sailed|sayled|"
        r"saile|sayle|shipped|shipt|transported|transport\w*|bound|voyage|voyadge|passage|remoued|removed|"
        r"imployed|employed)\b[^;]{0,45}?\b(?:to|vnto|unto|into|for|towards?|at|att)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "travel_from", "medium", re.compile(
        r"(?:\b(?:came|come|cominge|arrived|arriued|returned|retourned|sent|sett saile|set sayle|sailed|sayled|fled)\b"
        r"[^;]{0,40}?)?\b(?:from|out of)\s*(?:the\s+)?$")),
    (PERSON, PLACE, "resident_of", "medium", re.compile(
        r"^[,\s]*(?:(?:late|nowe?|lately)\s+)?(?:(?:dwelling|dwellinge|liuing|liuinge|living|inhabitant|inhabitants|"
        r"planter|planters|resident|seated|planted|settled|abiding|borne)\s+)?(?:of|in|at|att)\s*(?:the\s+(?:cittie|citty|city|"
        r"county|countie|parish|towne|town|isle|iland|island)\s+of\s*)?(?:the\s+)?$")),
    (PERSON, GROUP, "action_against", "high", re.compile(
        r"\b(?:vppon|vpon|upon|against|agaynst|agst|ouer|over|march\w*|expedition\w*)\s*(?:the\s+)?$")),
    (PERSON, GROUP, "trade_with", "high", re.compile(
        r"\b(?:trade|trad\w*|trucke|truck|traffique|traffick\w*|deale|dealt|corne)\b[^;]{0,30}?\b(?:with)\s*(?:the\s+)?$")),
    (PERSON, GROUP, "peace_with", "high", re.compile(
        r"\b(?:peace|league|treaty|treatie|amity|amitie|friendship|frendship)\b[^;]{0,25}?\b(?:with)\s*(?:the\s+)?$")),
    (PERSON, PERSON, "kin_of", "high", re.compile(
        rf"^[,\s]*(?:the\s+)?{KIN}\s+(?:of|to|vnto|unto)\s*(?:the\s+(?:said|late)\s+|his\s+|her\s+)?$")),
    (PERSON, PERSON, "agent_of", "high", re.compile(
        rf"^[,\s]*(?:the\s+)?(?:\w+\s+)?{AGENT}\s+(?:of|to|vnto|unto|for)\s*(?:the\s+said\s+|his\s+)?$")),
    (PERSON, PERSON, "letter_to", "high", re.compile(
        r"\b(?:letter|lerrer|letrer|lettre|lre|tre|letters|lres|tres|wrote|writ|written)s?\b[^;]{0,20}?\b(?:to|vnto|unto)\s*$")),
    (PERSON, PERSON, "complaint_against", "high", re.compile(
        r"\b(?:against|agaynst|agst|complain\w*|complayn\w*|accus\w*|suit|sute)\b[^;]{0,20}?\s*(?:the\s+said\s+)?$")),
    (PERSON, PERSON, "transfer_to", "high", re.compile(
        r"\b(?:assigned|assigne|sold|solde|passed|pass|graunted|granted|surrendred|surrendered|bequeathed|given|giuen|"
        r"made ouer|transferred|alienated|conveyed)\b[^;]{0,45}?\b(?:to|vnto|unto)\s*$")),
    (PERSON, PERSON, "married", "high", re.compile(
        r"\b(?:married|marryed|maried|marrying|marriage)\b[^;]{0,20}?\b(?:to|with|vnto|unto)\s*$")),
]
# Rules that also need the text after the object, or that read object-first.
# ``Thomas Tracy his wife Mary Tracy``. A comma after ``wife`` is a list entry
# (``Robert Iordan, his wife, Richard Rowles``: Jordan's unnamed wife, then
# the next person), so it does not relate the two names.
POSSESSIVE_KIN_RE = re.compile(rf"^[,\s]*(?:his|her)\s+{KIN}\s*$")
POSSESSIVE_AGENT_RE = re.compile(rf"^[,\s]*(?:his|her)\s+{AGENT}\s*$")
PEOPLE_AT_GAP_RE = re.compile(r"^\s*(?:of|belonging to|belonginge to)\s*$")       # At PLACE of PERSON his people
PEOPLE_AT_AFTER_RE = re.compile(r"^\W*(?:his|her|their)\s+(?:people|men|servants|seruants|company|family|familie)")
OFFICE_BEFORE_RE = re.compile(rf"\b{OFFICE}\b[^;]{{0,30}}?\b(?:of|for)\s*(?:the\s+)?$")  # Governor of PLACE, Sir X
SHARES_AFTER_RE = re.compile(rf"^\W*for\s+{NUM}\s*(?:shares?|acres?)")            # A to B for 3 shares
LETTER_HEAD_RE = re.compile(r"^\W*(?:a\s+)?(?:letter|lerrer|letrer|lettre|lre|tre)s?\s+(?:to|vnto|unto)\s*$")

FIELDS = [
    "relation", "confidence", "subject", "subject_key", "subject_type", "object", "object_key", "object_type",
    "cue", "page_id", "segment_id", "date_start", "date_confidence", "subject_span", "object_span", "sentence",
]


def entity(row: dict) -> tuple[str, str, str]:
    name = row["entity_name"] or " ".join(row["observed_span"].split())
    return name, row["entity_key"], TYPE_OF[row["label"]]


def sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n\n", 0, start), 0)
    for match in BOUNDARY_RE.finditer(text, max(0, start - 400), start):
        if has_boundary(text[match.start():match.end() + 1]):
            left = max(left, match.end())
    right = text.find("\n\n", end)
    right = len(text) if right < 0 else right
    for match in BOUNDARY_RE.finditer(text, end, min(len(text), end + 400)):
        if has_boundary(text[match.start():match.end() + 1]):
            right = min(right, match.start() + 1)
            break
    return " ".join(text[left:right].split())[:500]


def relate(a: dict, b: dict, text: str) -> list[tuple[dict, dict, str, str, str]]:
    """Relations between neighbours ``a`` (earlier) and ``b`` (later)."""
    gap_raw = text[int(a["span_end"]):int(b["span_start"])]
    letter_heading = LETTER_HEAD_RE.match(normalize(gap_raw.split(".", 1)[-1])) if "." in gap_raw else None
    if len(gap_raw) > MAX_GAP or (has_boundary(gap_raw) and not letter_heading):
        return []
    gap = normalize(gap_raw)
    before = normalize(text[max(0, int(a["span_start"]) - 60):int(a["span_start"])])
    after = normalize(text[int(b["span_end"]):int(b["span_end"]) + 40])
    ta, tb = TYPE_OF[a["label"]], TYPE_OF[b["label"]]
    found = []
    if ta == PERSON and tb == PERSON:
        if letter_heading or LETTER_HEAD_RE.match(gap):
            found.append((a, b, "letter_to", "high", gap))
        elif POSSESSIVE_KIN_RE.match(gap):
            found.append((b, a, "kin_of", "high", gap))
        elif POSSESSIVE_AGENT_RE.match(gap):
            found.append((b, a, "agent_of", "high", gap))
        elif re.fullmatch(r"(?:to|vnto|unto)", gap) and SHARES_AFTER_RE.match(after):
            found.append((a, b, "transfer_to", "high", f"{gap} {SHARES_AFTER_RE.match(after).group(0)}"))
    if ta == PLACE and tb == PERSON:
        if PEOPLE_AT_GAP_RE.match(gap) and PEOPLE_AT_AFTER_RE.match(after):
            found.append((b, a, "people_at", "high", f"{gap} … {PEOPLE_AT_AFTER_RE.match(after).group(0)}"))
        elif re.fullmatch(r"[,:]?", gap) and OFFICE_BEFORE_RE.search(before):
            found.append((b, a, "office_in", "high", OFFICE_BEFORE_RE.search(before).group(0)))
    if not found:
        for subject_type, object_type, relation, confidence, rule in GAP_RULES:
            if (ta, tb) == (subject_type, object_type) and rule.search(gap):
                cue = rule.search(gap).group(0)
                if relation == "travel_from" and re.fullmatch(r"(?:from|out of)(?:\s+the)?", cue.strip()):
                    confidence = "low"  # a bare "from" is often a letter's origin, not a journey
                found.append((a, b, relation, confidence, cue))
                break
    return found


def extract(pages: list[dict], mentions: list[dict]) -> list[dict]:
    texts = {page["page_id"]: page["text"] for page in pages}
    by_page: dict[str, list[dict]] = defaultdict(list)
    for row in mentions:
        if (row["usable_for_association"] == "yes" and row["label"] in TYPE_OF
                and row.get("mention_role", "mentioned") != "present"):
            by_page[row["page_id"]].append(row)
    relations = []
    for page_id, rows in by_page.items():
        rows.sort(key=lambda r: int(r["span_start"]))
        text = texts[page_id]
        for index in range(len(rows) - 1):
            a, b = rows[index], rows[index + 1]
            for subject, obj, relation, confidence, cue in relate(a, b, text):
                # Coordination: ``vppon the Nansamums, & Wariscoyacks`` relates the next object too.
                targets = [obj]
                follow = index + 2
                # Only person -> place/people relations spread over a list: ``son of A, and B``
                # does not make the subject B's son too.
                spreads = TYPE_OF[b["label"]] in {PLACE, GROUP}
                while spreads and follow < len(rows) and obj is b and rows[follow]["label"] == b["label"]:
                    link = normalize(text[int(rows[follow - 1]["span_end"]):int(rows[follow]["span_start"])])
                    if not re.fullmatch(r"[,\s]*(?:and|&)?\s*(?:the\s+)?", link):
                        break
                    targets.append(rows[follow])
                    follow += 1
                for target in targets:
                    s_name, s_key, s_type = entity(subject)
                    o_name, o_key, o_type = entity(target)
                    if s_name == o_name:
                        continue
                    lo = min(int(subject["span_start"]), int(target["span_start"]))
                    hi = max(int(subject["span_end"]), int(target["span_end"]))
                    relations.append({
                        "relation": relation, "confidence": confidence,
                        "subject": s_name, "subject_key": s_key, "subject_type": s_type,
                        "object": o_name, "object_key": o_key, "object_type": o_type, "cue": cue,
                        "page_id": page_id, "segment_id": subject["segment_id"],
                        "date_start": subject["date_start"], "date_confidence": subject["date_confidence"],
                        "subject_span": " ".join(subject["observed_span"].split()),
                        "object_span": " ".join(target["observed_span"].split()),
                        "sentence": sentence_around(text, lo, hi),
                    })
    return relations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.mentions.open(encoding="utf-8", newline="") as handle:
        mentions = list(csv.DictReader(handle))
    relations = extract(load_primary_pages(args.source), mentions)
    relations.sort(key=lambda r: (r["relation"], r["subject"], r["object"], r["date_start"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(relations)
    print(f"{len(relations)} relations -> {args.output}")
    print("  by relation:", dict(Counter(r["relation"] for r in relations).most_common()))


if __name__ == "__main__":
    main()
