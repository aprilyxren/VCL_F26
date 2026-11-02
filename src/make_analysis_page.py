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
from pathlib import Path

import numpy as np

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

def half_year(date: str) -> str:
    return f"{date[:4]}{'a' if int(date[5:7]) <= 6 else 'b'}"


def indigenous(mentions: list[dict], evidence: list[dict]) -> dict:
    rows = [m for m in mentions if m["usable_for_association"] == "yes" and m["date_start"]
            and m["reading"] != "place_homonym" and m["category"] in {"group", "collective", "person", "title"}]
    series = defaultdict(Counter)
    for m in rows:
        kind = "collective" if m["category"] == "collective" else "named"
        series[half_year(m["date_start"])][kind] += 1
    first, last = 1616, 1625
    bins = [f"{y}{h}" for y in range(first, last + 1) for h in "ab"]
    early = sum(sum(series[b].values()) for b in series if b < bins[0])
    timeline = [{"bin": b, "named": series[b]["named"], "collective": series[b]["collective"]} for b in bins]

    def era(date: str) -> str:
        return "before" if date < ATTACK else "after"

    totals = Counter(era(m["date_start"]) for m in rows)
    terms = []
    for label, forms in TERM_GROUPS.items():
        c = Counter(era(m["date_start"]) for m in rows if m["category"] == "collective" and m["entity_name"] in forms)
        terms.append({"term": label, "before": c["before"], "after": c["after"]})
    coll = Counter(era(m["date_start"]) for m in rows if m["category"] == "collective")
    themes = []
    for label, pattern in THEMES.items():
        rx = re.compile(pattern, re.I)
        hits = Counter(era(m["date_start"]) for m in rows if rx.search(m["context"]))
        example = {}
        for e in ("before", "after"):
            ex = next((m for m in rows if era(m["date_start"]) == e and rx.search(m["context"])), None)
            if ex:
                example[e] = {"text": clip(ex["context"], 200), "date": ex["date_start"]}
        themes.append({"theme": label, "before": hits["before"], "after": hits["after"], "example": example})

    groups: dict[str, dict] = {}
    for m in rows:
        if m["category"] != "group":
            continue
        g = groups.setdefault(m["entity_name"], {"name": m["entity_name"], "before": 0, "after": 0,
                                                 "people": {"before": Counter(), "after": Counter()}})
        g[era(m["date_start"])] += 1
    leaders: dict[str, Counter] = defaultdict(Counter)
    for m in rows:
        if m["category"] == "person":
            leaders[m["entity_name"]][era(m["date_start"])] += 1
    for row in evidence:
        if row["target_type"] != "indigenous_group" or row["proximity"] not in {"close", "near"} or not row["date_start"]:
            continue
        g = groups.get(row["target_name"])
        if g:
            g["people"][era(row["date_start"])][row["person"]] += 1
    out_groups = []
    for g in sorted(groups.values(), key=lambda g: -(g["before"] + g["after"])):
        out_groups.append({"name": g["name"], "before": g["before"], "after": g["after"],
                           "people_before": g["people"]["before"].most_common(5),
                           "people_after": g["people"]["after"].most_common(5)})
    return {"timeline": timeline, "earlier": early, "totals": dict(totals), "collective": dict(coll),
            "terms": terms, "themes": themes, "groups": out_groups[:12],
            "leaders": [{"name": k, "before": v["before"], "after": v["after"]}
                        for k, v in sorted(leaders.items(), key=lambda kv: -sum(kv[1].values()))[:8]]}


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


# ---------------------------------------------------------------- page

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--places", type=Path, required=True)
    parser.add_argument("--indigenous", type=Path, required=True)
    parser.add_argument("--mentions", type=Path, required=True)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("analysis_page.html"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence = read(args.evidence)
    data = {
        "plantations": plantations(evidence, read(args.places)),
        "indigenous": indigenous(read(args.indigenous), evidence),
        "network": network(args.mentions, read(args.relations)),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = args.template.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    args.output.write_text(html, encoding="utf-8")
    p, i, n = data["plantations"], data["indigenous"], data["network"]
    print(f"plantations: {len(p['places'])} places, years {p['years'][0]}-{p['years'][-1]}")
    print(f"indigenous: {i['totals']} mentions; {len(i['groups'])} groups")
    print(f"network: {len(n['nodes'])} people, {len(n['edges'])} ties, {n['communities']} clusters")
    print(f"wrote {args.output} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
