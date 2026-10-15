"""Draw a stratified sample of extracted relations for review, and score it.

``sample``: up to ``--per-type`` relations of each type (all of a rare type),
each with its sentence and the two names in [[brackets]]. The reviewer writes
``y`` in ``correct`` for relations that are right; a blank counts as wrong once
the reviewer says the sample is finished.

``score``: precision per relation type from the marked sample.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path


FIELDS = ["correct", "note", "relation", "subject", "object", "cue", "sentence", "page_id", "date_start", "confidence"]


def bracket(sentence: str, *spans: str) -> str:
    for span in spans:
        if span and span in sentence:
            sentence = sentence.replace(span, f"[[{span}]]", 1)
    return sentence


def sample(relations: Path, output: Path, per_type: int, seed: int) -> None:
    with relations.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["relation"]].append(row)
    rng = random.Random(seed)
    picked = []
    for relation in sorted(by_type, key=lambda r: -len(by_type[r])):
        group = by_type[relation]
        for row in rng.sample(group, min(per_type, len(group))):
            picked.append({
                "correct": "", "note": "", "relation": relation, "subject": row["subject"], "object": row["object"],
                "cue": row["cue"], "sentence": bracket(row["sentence"], row["subject_span"], row["object_span"]),
                "page_id": row["page_id"], "date_start": row["date_start"], "confidence": row["confidence"],
            })
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(picked)
    print(f"Wrote {len(picked)} relations ({len(by_type)} types) to {output}")


def score(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_type: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_type[row["relation"]].append(row["correct"].strip().lower() in {"y", "yes"})
    total = [v for values in by_type.values() for v in values]
    print(f"overall: {sum(total)}/{len(total)} correct ({sum(total) / max(1, len(total)):.0%})")
    for relation, values in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        print(f"  {relation:18s} {sum(values)}/{len(values)} ({sum(values) / len(values):.0%})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--relations", type=Path, required=True)
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--per-type", type=int, default=8)
    s.add_argument("--seed", type=int, default=1622)
    c = sub.add_parser("score")
    c.add_argument("sample", type=Path)
    args = parser.parse_args()
    if args.command == "sample":
        sample(args.relations, args.output, args.per_type, args.seed)
    else:
        score(args.sample)
