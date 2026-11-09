"""Build the three-part analysis page: plantations over time, Indigenous
peoples before and after the 1622 attack, and the network of people.

Inputs (all pipeline outputs):

* ``person_place_evidence.csv``: one row per person, target, and meeting, with
  the proximity tier and any extracted relation. Plantations and Indigenous
  links come from here; ``present_only`` rows (people only in the attendance
  list) are left out, since attending a court says nothing about a place.
* ``place_authority_seed.csv``: which targets are Virginia settlements.
* ``indigenous_mentions.csv``: dated mentions of Indigenous groups, people, and
  collective terms, with their context, for the before/after comparison.
* ``combined_primary_text.json`` and ``text_segments.csv``: the records each
  mention belongs to (a court meeting, or a Volume III-IV document) and how
  many words survive from each half-year, so a peak in the timeline can be
  broken down by record and read as a rate, not only a count.
* ``list_of_records.json``: Kingsbury's List of Records, whose entry (cited in
  each document's heading as ``List of Records No. 400``) gives a clean title.
* ``data/geo/basemap.json`` and ``data/geo/place_coordinates.csv``: the map
  of where the network's people are tied to, by year (see ``make_basemap.py``;
  each coordinate's source is in ``place_coordinate_sources.csv``).
* ``ner_mentions.csv`` and ``relations.csv``: people named in the same meeting
  (``mentioned`` role, linked to the dictionary), plus person-person relations,
  for the network. The layout (force-directed) and the clusters (Louvain
  modularity) are computed here, so the page draws at rest.

Writes one self-contained HTML file.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import get_close_matches
from pathlib import Path

import numpy as np

from build_associations import meeting_ids
from date_pages import month_of
from make_basemap import FRAMES
from source_loader import load_primary_pages

ATTACK = "1622-03-22"
TIER = {"relation": 4, "close": 3, "near": 2, "same_meeting": 1}
SETTLEMENT_TYPE_RE = re.compile(r"plantation|hundred|settlement|shire|town|ironworks")
# The four boroughs of the 1618-19 reorganization (the "Great Charter").
INCORPORATIONS = {"James City", "Charles City", "Henricus", "Elizabeth City"}
THEMES = {
    "Trade and corn": r"\b(?:trade|trad\w+|truck\w*|traffi\w+|corne?|furr?e?s|beades|copper|barter\w*)\b",
    "Peace and friendship": r"\b(?:peace|league|amit\w+|friend\w*|frend\w*|kind\w*|loue|love|treat\w+)\b",
    "Conversion and schooling": r"\b(?:conver\w+|christ\w+|gospell?|god|religion|colledge|college|school\w*|educat\w+|children|"
                                r"infidells?|heathens?)\b",
    "Violence and revenge": r"\b(?:mass?acre\w*|murth\w+|murd\w+|slaine|slayne|slain|kill\w*|reveng\w+|warr?e?s?|enem\w+|enim\w+|"
                            r"treach\w+|trech\w+|bloud\w*|blood\w*|destroy\w*|extirpat\w+|roote out|subdu\w+)\b",
}
# Distinctive words: log-odds with an informative Dirichlet prior (Monroe,
# Colaresi and Quinn 2008), a period's mention contexts against all others.
PRIOR_STRENGTH = 500
MIN_WORD_COUNT = 3
STOPWORDS = set("""
a about after against all also an and any are as at be been before being both but by can could did doe
done each euer euery for from had has hath haue having he her here him his how i if in into is it its
made make many may me might more most much must my no nor not now of on one onely or other others our
out owne same shal shall shalbe should so some such than that the their theire them then there thereby
therefore therein thereof these they this those thus to two unto upon us vnto vpon very was we wee well
were what when where which while who whom will with within without would ye yet yt you your wch wth weh
sd mr mrs sr sir doth such said like into onely vs haue hee shee bee doe soe theis wherein whereof
vppon vpp itt beene bin vse vsed though ours hir itt wherby therof noe theyr beinge howe whome giuen giue came greate great himselfe himself
""".split())
LIST_ENTRY_RE = re.compile(r"(?m)^\s*(\d{1,4})\.\*?\s+([\s\S]+?)(?=\n\s*[IVXLv]*\.?\s*(?:Ref|Pub)\b|\n\s*\d{1,4}\.\*?\s|\Z)")
# Where the List names the repository (``LC.``, ``PRO.``, ``(1) LC.``) the description has ended.
LIST_REPOSITORY_RE = re.compile(r"\s(?:\(1\)|¥|(?:[A-Z]{2,5}|Le|Lc)\.(?=\s|$)|\[[A-Z]{2,4}\])[\s\S]*$")
LIST_NUMBER_RE = re.compile(r"(?i)List\s+of\s+Rec\w*\s+Nos?\.?\s*(\d{1,4})")
RECORD_SOURCE_RE = re.compile(
    r"(?i)\s*(?:\(1\)|Manuscript|Document\s+in|List\s+of\s+Rec|Ferrar\s+Papers|Smyth\s+of\s+Nibley|"
    r"Manchester\s+Papers|C\.\s*O\.|State\s+Papers|Additional\s+Manuscripts|Privy\s+Council\s+Register|"
    r"Printed\.|For\s+Reference)[\s\S]*$")
VOLUME_NUMERALS = {"1": "I", "2": "II", "3": "III", "4": "IV"}

TERM_GROUPS = {
    "Indians": {"Indians"}, "savages": {"savages"}, "natives": {"natives"}, "infidels": {"infidels"},
    "heathen": {"heathen", "pagans"}, "barbarians": {"barbarians"},
}
NETWORK_SIZE = 70
EDGES_PER_NODE = 4
PERSON_RELATIONS = {"letter_to", "complaint_against", "transfer_to", "kin_of", "married", "agent_of"}


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clip(text: str, n: int = 260) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


# ---------------------------------------------------------------- plantations

def plantations(evidence: list[dict], places: list[dict]) -> dict:
    settlements = {p["preferred_name"]: p for p in places
                   if p["world_region"] == "New World" and SETTLEMENT_TYPE_RE.search(p["place_type"])}
    best: dict[tuple, dict] = {}
    for row in evidence:
        if row["target_type"] != "place" or row["target_name"] not in settlements or row["proximity"] == "present_only":
            continue
        if not row["date_start"]:
            continue
        year = row["date_start"][:4]
        tier = TIER["relation"] if row["relations"] else TIER.get(row["proximity"], 1)
        key = (row["target_name"], year, row["person"])
        cur = best.setdefault(key, {"tier": 0, "meetings": set(), "relations": Counter(), "example": "", "date": ""})
        cur["meetings"].add(row["meeting_id"])
        for rel in filter(None, row["relations"].split(";")):
            cur["relations"][rel.strip().split("×")[0]] += 1
        if tier > cur["tier"]:
            cur.update(tier=tier, example=clip(row["snippet"]), date=row["date_start"])
    years = sorted({y for _, y, _ in best})
    cells: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for (place, year, person), v in best.items():
        cells[place][year].append([person, v["tier"], len(v["meetings"]), ", ".join(sorted(v["relations"])), v["date"], v["example"]])
    out_places = []
    for place, by_year in cells.items():
        for lst in by_year.values():
            lst.sort(key=lambda r: (-r[1], -r[2], r[0]))
        people = {r[0] for lst in by_year.values() for r in lst}
        strong = {r[0] for lst in by_year.values() for r in lst if r[1] >= 3}
        kind = "incorporation" if place in INCORPORATIONS else (
            "town" if re.search(r"town|settlement", settlements[place]["place_type"]) and "Hundred" not in place else "particular")
        out_places.append({"name": place, "kind": kind, "type": settlements[place]["place_type"],
                           "people": len(people), "strong": len(strong)})
    out_places.sort(key=lambda p: -p["people"])
    return {"years": years, "places": out_places, "cells": {p: dict(v) for p, v in cells.items()}}


# ---------------------------------------------------------------- indigenous

def midpoint(row: dict) -> str:
    """The middle of a row's date range: a document dated only ``1622`` sits mid-year."""
    start = date.fromisoformat(row["date_start"])
    end = date.fromisoformat(row["date_end"] or row["date_start"])
    return (start + (end - start) / 2).isoformat()


def half_year(row: dict) -> str:
    middle = midpoint(row)
    return f"{middle[:4]}{'a' if int(middle[5:7]) <= 6 else 'b'}"


def era(row: dict) -> str:
    """``undetermined`` when the date range spans the attack (a paper dated only 1622)."""
    if (row["date_end"] or row["date_start"]) < ATTACK:
        return "before"
    return "after" if row["date_start"] >= ATTACK else "undetermined"


def indigenous(mentions: list[dict], evidence: list[dict], pages: dict[str, dict], segments: list[dict],
               entries: dict[int, str]) -> dict:
    rows = [m for m in mentions if m["usable_for_association"] == "yes" and m["date_start"]
            and m["reading"] != "place_homonym" and m["category"] in {"group", "collective", "person", "title"}]
    series = defaultdict(Counter)
    for m in rows:
        kind = "collective" if m["category"] == "collective" else "named"
        series[half_year(m)][kind] += 1
    first, last = 1616, 1625
    bins = [f"{y}{h}" for y in range(first, last + 1) for h in "ab"]
    early = sum(sum(series[b].values()) for b in series if b < bins[0])
    words = words_by_half_year(pages, segments)
    timeline = [{"bin": b, "named": series[b]["named"], "collective": series[b]["collective"], "words": words[b]}
                for b in bins]

    totals = Counter(era(m) for m in rows)
    terms = []
    for label, forms in TERM_GROUPS.items():
        c = Counter(era(m) for m in rows if m["category"] == "collective" and m["entity_name"] in forms)
        terms.append({"term": label, "before": c["before"], "after": c["after"]})
    coll = Counter(era(m) for m in rows if m["category"] == "collective")
    themes = []
    for label, pattern in THEMES.items():
        rx = re.compile(pattern, re.I)
        hits = Counter(era(m) for m in rows if rx.search(m["context"]))
        example = {}
        for e in ("before", "after"):
            ex = next((m for m in rows if era(m) == e and rx.search(m["context"])), None)
            if ex:
                example[e] = {"text": clip(ex["context"], 200), "date": ex["date_start"]}
        themes.append({"theme": label, "before": hits["before"], "after": hits["after"], "example": example})

    groups: dict[str, dict] = {}
    for m in rows:
        if m["category"] != "group":
            continue
        g = groups.setdefault(m["entity_name"], {"name": m["entity_name"], "before": 0, "after": 0,
                                                 "people": {"before": Counter(), "after": Counter()}})
        if era(m) != "undetermined":
            g[era(m)] += 1
    leaders: dict[str, Counter] = defaultdict(Counter)
    for m in rows:
        if m["category"] == "person":
            leaders[m["entity_name"]][era(m)] += 1  # "undetermined" is not shown
    for row in evidence:
        if row["target_type"] != "indigenous_group" or row["proximity"] not in {"close", "near"} or not row["date_start"]:
            continue
        g = groups.get(row["target_name"])
        if g and era(row) != "undetermined":
            g["people"][era(row)][row["person"]] += 1
    out_groups = []
    for g in sorted(groups.values(), key=lambda g: -(g["before"] + g["after"])):
        out_groups.append({"name": g["name"], "before": g["before"], "after": g["after"],
                           "people_before": g["people"]["before"].most_common(5),
                           "people_after": g["people"]["after"].most_common(5)})
    return {"timeline": timeline, "breakdown": peak_breakdown(rows, record_units(segments, pages, entries)), "earlier": early, "totals": dict(totals), "collective": dict(coll),
            "terms": terms, "themes": themes, "groups": out_groups[:12],
            "leaders": [{"name": k, "before": v["before"], "after": v["after"]}
                        for k, v in sorted(leaders.items(), key=lambda kv: -sum(kv[1].values()))[:8]]}


def document_title(heading: str) -> str:
    """``CCLXXX. COUNCIL IN VIRGINIA. LETTER TO ... JANUARY 20, 1622/3 Manuscript ...``
    -> ``CCLXXX. COUNCIL IN VIRGINIA. LETTER TO ...`` (OCR spelling kept)."""
    words = RECORD_SOURCE_RE.sub("", heading).split()
    for i, word in enumerate(words[2:], 2):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if re.fullmatch(r"[\[(]?1[56]\d\d\S*", word) or (month_of(word) and re.match(r"[\[(]?\d", nxt)):
            words = words[:i]
            break
    return " ".join(words).rstrip(" .,;:")


def list_entries(records: list[dict]) -> dict[int, str]:
    """Kingsbury's List of Records: entry number -> its description."""
    text = "\n".join(r["text"] for r in records)
    entries: dict[int, str] = {}
    for match in LIST_ENTRY_RE.finditer(text):
        description = LIST_REPOSITORY_RE.sub("", " ".join(match.group(2).split())).replace("- ", "")
        entries.setdefault(int(match.group(1)), description.rstrip(" ."))
    return entries


def record_units(segments: list[dict], pages: dict[str, dict], entries: dict[int, str]) -> dict[str, dict]:
    """segment_id -> the record it belongs to (court meeting, document, or lone page)."""
    units = {segment_id: meeting["meeting_id"] for segment_id, meeting in meeting_ids(segments).items()}
    by_id = {sg["segment_id"]: sg for sg in segments}
    records = {}
    for segment_id, unit_id in units.items():
        if unit_id in records:
            continue
        first = by_id[unit_id[2:]]
        page = pages[first["page_id"]]
        where = f"Vol. {VOLUME_NUMERALS[first['page_id'][1:3].lstrip('0')]}, p. {page.get('printed_page') or '?'}"
        if first["date_source"] == "document_heading":
            start = int(first["char_start"])
            cited = LIST_NUMBER_RE.search(page["text"], start, start + 900)
            kind = "document"
            title = entries.get(int(cited.group(1))) if cited else None
            title = clip(title, 200) if title else document_title(first["heading"])
        elif first["date_source"] == "meeting_heading":
            kind = "court"
            title = "Quarter Court" if "QUARTER" in first["heading"].upper() else "Court meeting"
        else:
            kind, title = "page", "Untitled pages"
        records[unit_id] = {"title": title, "kind": kind, "where": where,
                            "date": first["date_start"], "date_end": first["date_end"]}
    return {segment_id: {"id": unit_id, **records[unit_id]} for segment_id, unit_id in units.items()}


def words_by_half_year(pages: dict[str, dict], segments: list[dict]) -> Counter:
    """Words of surviving text per half-year (each segment at its date's midpoint)."""
    words = Counter()
    for sg in segments:
        if not sg["date_start"]:
            continue
        text = pages[sg["page_id"]]["text"][int(sg["char_start"]):int(sg["char_end"])]
        words[half_year(sg)] += len(re.findall(r"[A-Za-z]{2,}", text))
    return words


def tokens(text: str, names: frozenset = frozenset()) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS and w not in names]


def word_key(word: str) -> str:
    return word.replace("v", "u").replace("j", "i")  # vnto = unto, iudge = judge


def distinctive_words(inside: list[str], outside: list[str], names: frozenset, top: int = 12) -> list[dict]:
    """Words over-represented in ``inside`` contexts against ``outside`` (z > 1.96).
    ``names`` (spellings of Indigenous nations and people) are left out: the
    names list already shows who is named."""
    count_in, count_out, forms = Counter(), Counter(), defaultdict(Counter)
    for bag, text in ((count_in, t) for t in inside):
        for w in tokens(text, names):
            bag[word_key(w)] += 1
            forms[word_key(w)][w] += 1
    for text in outside:
        for w in tokens(text, names):
            count_out[word_key(w)] += 1
    n_in, n_out = sum(count_in.values()), sum(count_out.values())
    total = count_in + count_out
    n_all = n_in + n_out
    scored = []
    for w, y_in in count_in.items():
        if y_in < MIN_WORD_COUNT:
            continue
        a = PRIOR_STRENGTH * total[w] / n_all
        y_out = count_out[w]
        delta = (math.log((y_in + a) / (n_in + PRIOR_STRENGTH - y_in - a))
                 - math.log((y_out + a) / (n_out + PRIOR_STRENGTH - y_out - a)))
        z = delta / math.sqrt(1 / (y_in + a) + 1 / (y_out + a))
        if z > 1.96:
            scored.append({"word": forms[w].most_common(1)[0][0], "n": y_in, "z": round(z, 1)})
    return sorted(scored, key=lambda r: -r["z"])[:top]


def peak_breakdown(rows: list[dict], units: dict[str, dict]) -> dict[str, dict]:
    """Per half-year: the records behind its mentions, themes, names, distinctive words, examples."""
    spans = {w for m in rows if m["category"] in {"group", "person", "title"}
             for w in re.findall(r"[a-z]{4,}", m["observed_span"].lower())}
    # OCR spellings that were not matched as mentions (``opochancano``) are still names.
    vocabulary = {w for m in rows for w in re.findall(r"[a-z]{4,}", m["context"].lower())}
    names = frozenset(spans | {w for w in vocabulary if get_close_matches(w, spans, n=1, cutoff=0.75)})
    contexts = {}  # one context per record and passage, so a crowded sentence counts once
    for m in rows:
        contexts.setdefault((m["segment_id"], m["context"]), half_year(m))
    out = {}
    for b in sorted({half_year(m) for m in rows}):
        in_bin = [m for m in rows if half_year(m) == b]
        records = defaultdict(lambda: {"named": 0, "collective": 0})
        for m in in_bin:
            unit = units.get(m["segment_id"])
            key = unit["id"] if unit else m["segment_id"]
            rec = records[key]
            rec.update({k: v for k, v in (unit or {"title": "Untitled pages", "kind": "page", "where": m["page_id"],
                                                    "date": m["date_start"], "date_end": m["date_end"]}).items()
                        if k != "id"})
            rec["collective" if m["category"] == "collective" else "named"] += 1
        ordered = sorted(records.values(), key=lambda r: -(r["named"] + r["collective"]))
        themes = []
        for label, pattern in THEMES.items():
            rx = re.compile(pattern, re.I)
            themes.append({"theme": label, "n": sum(1 for m in in_bin if rx.search(m["context"]))})
        named = Counter(m["entity_name"] for m in in_bin if m["category"] in {"group", "person"})
        inside = [c for (_, c), bb in contexts.items() if bb == b]
        outside = [c for (_, c), bb in contexts.items() if bb != b]
        examples, seen = [], set()
        for rec_key in sorted(records, key=lambda k: -(records[k]["named"] + records[k]["collective"])):
            m = next((m for m in in_bin if (units.get(m["segment_id"]) or {}).get("id", m["segment_id"]) == rec_key), None)
            if m and rec_key not in seen:
                seen.add(rec_key)
                examples.append({"text": clip(m["context"], 240), "date": m["date_start"],
                                 "title": records[rec_key]["title"], "where": records[rec_key]["where"]})
            if len(examples) == 3:
                break
        out[b] = {"total": len(in_bin), "records": ordered[:8], "more_records": max(0, len(ordered) - 8),
                  "themes": themes, "names": named.most_common(6),
                  "words": distinctive_words(inside, outside, names), "examples": examples}
    return out


# ---------------------------------------------------------------- network

def louvain(n: int, weights: dict[tuple[int, int], float], seed: int = 1622) -> list[int]:
    """Communities by Louvain modularity (local moving, then aggregation)."""
    def one_level(n, w):
        adj = defaultdict(dict)
        for (a, b), x in w.items():
            adj[a][b] = adj[a].get(b, 0) + x
            adj[b][a] = adj[b].get(a, 0) + x
        k = [sum(adj[i].values()) for i in range(n)]
        m2 = sum(k) or 1.0
        comm = list(range(n))
        tot = k[:]
        order = list(range(n))
        random.Random(seed).shuffle(order)
        moved = True
        while moved:
            moved = False
            for i in order:
                ci = comm[i]
                links = defaultdict(float)
                for j, x in adj[i].items():
                    if j != i:
                        links[comm[j]] += x
                tot[ci] -= k[i]
                best, gain = ci, links.get(ci, 0) - tot[ci] * k[i] / m2
                for c, l in links.items():
                    g = l - tot[c] * k[i] / m2
                    if g > gain + 1e-12:
                        best, gain = c, g
                tot[best] += k[i]
                if best != ci:
                    comm[i] = best
                    moved = True
        labels = {c: idx for idx, c in enumerate(sorted(set(comm)))}
        return [labels[c] for c in comm]

    membership = list(range(n))
    size, w = n, dict(weights)
    while True:
        level = one_level(size, w)
        if len(set(level)) == size:
            break
        membership = [level[c] for c in membership]
        agg = defaultdict(float)
        for (a, b), x in w.items():
            agg[(level[a], level[b])] += x
        size, w = len(set(level)), dict(agg)
    return membership


def layout(n: int, edges: list[tuple[int, int, float]], communities: list[int], seed: int = 1622) -> np.ndarray:
    """Fruchterman-Reingold with a weak pull toward each community's centre."""
    rng = np.random.default_rng(seed)
    pos = rng.uniform(-1, 1, (n, 2))
    k = 1.6 / math.sqrt(n)
    temp = 0.12
    comm = np.array(communities)
    for step in range(600):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=2) + 1e-6
        rep = (k * k / dist ** 2)[:, :, None] * delta
        disp = rep.sum(axis=1)
        for a, b, w in edges:
            d = pos[a] - pos[b]
            dl = np.linalg.norm(d) + 1e-6
            f = (dl * dl / k) * (0.4 + w) * d / dl
            disp[a] -= f
            disp[b] += f
        for c in set(communities):
            idx = comm == c
            disp[idx] -= 0.04 * (pos[idx] - pos[idx].mean(axis=0)) / k
        disp -= 0.02 * pos / k  # gravity
        length = np.linalg.norm(disp, axis=1)[:, None] + 1e-9
        pos += disp / length * np.minimum(length, temp)
        temp = max(0.004, temp * 0.993)
    pos -= pos.min(axis=0)
    pos /= pos.max(axis=0) + 1e-9
    return pos


def network(mentions_path: Path, relations: list[dict]) -> dict:
    meetings: dict[str, set] = defaultdict(set)
    years: dict[str, Counter] = defaultdict(Counter)
    with mentions_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row["label"] == "PERSON" and row["entity_key"] and row["usable_for_association"] == "yes"
                    and row["mention_role"] == "mentioned" and row["segment_id"]):
                meetings[row["entity_key"]].add(row["segment_id"])
                if row["date_start"]:
                    years[row["entity_key"]][row["date_start"][:4]] += 1
    people = sorted(meetings, key=lambda p: -len(meetings[p]))[:NETWORK_SIZE]
    index = {p: i for i, p in enumerate(people)}
    pair: dict[tuple[int, int], dict] = {}
    for i, a in enumerate(people):
        for j in range(i + 1, len(people)):
            b = people[j]
            shared = len(meetings[a] & meetings[b])
            if shared >= 4:
                jac = shared / len(meetings[a] | meetings[b])
                pair[(i, j)] = {"shared": shared, "jaccard": jac, "relations": Counter()}
    for r in relations:
        if r["relation"] in PERSON_RELATIONS and r["subject_key"] in index and r["object_key"] in index:
            a, b = sorted((index[r["subject_key"]], index[r["object_key"]]))
            if a == b:
                continue
            pair.setdefault((a, b), {"shared": 0, "jaccard": 0.0, "relations": Counter()})["relations"][r["relation"]] += 1
    # Each person keeps the ties strongest for them (Jaccard), so busy people do not take every line.
    keep = set()
    by_node = defaultdict(list)
    for (a, b), e in pair.items():
        by_node[a].append(((e["jaccard"]), (a, b)))
        by_node[b].append(((e["jaccard"]), (a, b)))
        if e["relations"]:
            keep.add((a, b))
    for node, lst in by_node.items():
        for _, key in sorted(lst, reverse=True)[:EDGES_PER_NODE]:
            keep.add(key)
    edges = [(a, b, pair[(a, b)]) for a, b in sorted(keep)]
    weights = {(a, b): e["jaccard"] + 0.05 * sum(e["relations"].values()) for a, b, e in edges}
    comm = louvain(len(people), weights)
    # Number communities by size, largest first.
    order = [c for c, _ in Counter(comm).most_common()]
    comm = [order.index(c) for c in comm]
    pos = layout(len(people), [(a, b, weights[(a, b)] * 4) for a, b, _ in edges], comm)
    nodes = []
    for i, p in enumerate(people):
        ys = years[p]
        nodes.append({"id": p, "x": round(float(pos[i, 0]), 4), "y": round(float(pos[i, 1]), 4), "c": comm[i],
                      "meetings": len(meetings[p]),
                      "span": f"{min(ys)}–{max(ys)}" if ys else ""})
    out_edges = [{"a": a, "b": b, "shared": e["shared"], "j": round(e["jaccard"], 3),
                  "rel": dict(e["relations"])} for a, b, e in edges]
    return {"nodes": nodes, "edges": out_edges, "communities": len(set(comm))}


# ---------------------------------------------------------------- map

def place_map(evidence: list[dict], coordinates: list[dict], basemap: dict, people: list[str],
              mentions: list[dict]) -> dict:
    """Mapped places and Indigenous nations, each with its person links as
    ``[person index, year, tier]`` (strongest tier per person and year).

    Every linked person is indexed (the page can show everyone), the
    network's people first so their index matches the network's nodes.
    """
    located = {}
    for row in coordinates:
        if not row["lat"]:
            continue
        lat, lon = float(row["lat"]), float(row["lon"])
        frame = next((f for f, (x0, y0, x1, y1) in FRAMES.items() if x0 <= lon <= x1 and y0 <= lat <= y1), None)
        if frame:
            located[row["id"]] = {"id": row["id"], "name": row["name"], "frame": frame, "lat": lat, "lon": lon,
                                  "precision": row["precision"], "basis": row["basis"],
                                  "kind": "nation" if row["id"].startswith("IG-") else "place"}
    index = {p: i for i, p in enumerate(people)}
    best: dict[tuple, int] = {}
    for row in evidence:
        if row["target_id"] not in located or row["proximity"] not in TIER or not row["date_start"]:
            continue
        tier = TIER["relation"] if row["relations"] else TIER[row["proximity"]]
        if row["person"] not in index:
            index[row["person"]] = len(index)
        key = (row["target_id"], index[row["person"]], int(midpoint(row)[:4]))
        best[key] = max(best.get(key, 0), tier)
    links = defaultdict(list)
    for (target, person, year), tier in sorted(best.items()):
        links[target].append([person, year, tier])
    named = defaultdict(Counter)  # mentions of each nation per year, for its marker when no one is selected
    for m in mentions:
        if m["category"] == "group" and m["usable_for_association"] == "yes" and m["date_start"]:
            named[m["entity_id"]][int(midpoint(m)[:4])] += 1
    places = []
    for target, place in located.items():
        place = {**place, "links": links.get(target, [])}
        if place["kind"] == "nation":
            place["mentions"] = dict(named.get(target, {}))
        places.append(place)
    return {"frames": basemap, "people": sorted(index, key=index.get), "places": places}


# ---------------------------------------------------------------- page

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--places", type=Path, required=True)
    parser.add_argument("--indigenous", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("data/cleaned/combined_primary_text.json"))
    parser.add_argument("--segments", type=Path, default=Path("data/cleaned/text_segments.csv"))
    parser.add_argument("--records", type=Path, default=Path("data/cleaned/list_of_records.json"))
    parser.add_argument("--basemap", type=Path, default=Path("data/geo/basemap.json"))
    parser.add_argument("--coordinates", type=Path, default=Path("data/geo/place_coordinates.csv"))
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("analysis_page.html"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence = read(args.evidence)
    indigenous_mentions = read(args.indigenous)
    data = {
        "plantations": plantations(evidence, read(args.places)),
        "indigenous": indigenous(indigenous_mentions, evidence,
                                 {p["page_id"]: p for p in load_primary_pages(args.source)}, read(args.segments),
                                 list_entries(json.loads(args.records.read_text(encoding="utf-8")))),
        "network": network(args.mentions, read(args.relations)),
    }
    data["map"] = place_map(evidence, read(args.coordinates), json.loads(args.basemap.read_text(encoding="utf-8")),
                            [node["id"] for node in data["network"]["nodes"]], indigenous_mentions)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = args.template.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    args.output.write_text(html, encoding="utf-8")
    p, i, n = data["plantations"], data["indigenous"], data["network"]
    print(f"plantations: {len(p['places'])} places, years {p['years'][0]}-{p['years'][-1]}")
    print(f"indigenous: {i['totals']} mentions; {len(i['groups'])} groups")
    print(f"network: {len(n['nodes'])} people, {len(n['edges'])} ties, {n['communities']} clusters")
    print(f"map: {len(data['map']['places'])} located places and nations, {len(data['map']['people'])} people")
    print(f"wrote {args.output} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
