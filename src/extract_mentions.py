"""Create a conservative, reviewable seed mention file from primary OCR text.

This is an extraction pass, not an identity-resolution pass. It preserves the
observed text and leaves person assignment to later review/authority logic.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import spacy

from source_loader import load_primary_pages


TITLES = {
    "captain", "captaine", "capt", "capt.", "sir", "lady", "lord",
    "chief", "king", "queen", "weroance", "doctor", "dr", "dr.",
    "master", "mistress", "mr", "mr.", "mrs", "mrs.", "governor",
    "m'", "m’", "m*", "m**", "mᵣ", "M'", "M’", "M*", "M**", "Mᵣ",
}

TITLE_NORMALIZATION = {
    "m'": "Mr", "m’": "Mr", "m*": "Mr", "m**": "Mr", "mᵣ": "Mr",
}

TITLE_RE = re.compile(
    r"(?i)(?:\b(?:captain|captaine|capt\.?|sir|lady|lord|chief|king|queen|"
    r"weroance|doctor|dr\.?|master|mistress|mr\.?|mrs\.?|governor)"
    r"|\b[mM][’'*ᵣ]+)"
    r"(?:\s*[,;:\-—–]?\s+)(?:[A-ZÀ-ÖØ-Þ][\w'’.-]*"
    r"(?:\s+(?:[A-ZÀ-ÖØ-Þ][\w'’.-]*|of|the|de|du)){0,3})"
)


def _title_and_name(span: str) -> tuple[str | None, str]:
    match = re.match(r"(?i)^((?:[A-Za-z.]+|[mM][’'*ᵣ]+))[\s,;:\-—–]+(.+)$", span.strip())
    if not match or match.group(1).lower() not in TITLES:
        return None, span.strip()
    observed_title = match.group(1)
    return TITLE_NORMALIZATION.get(observed_title.casefold(), observed_title), match.group(2).strip(" ,;:")


def _mention(
    page: dict[str, Any], start: int, end: int, label: str, text: str,
    sentence_text: str, model_label: str | None = None,
) -> dict[str, Any]:
    title, name_string = _title_and_name(text)
    name_parts = name_string.split()
    title_only_surname = title is not None and len(name_parts) == 1
    return {
        "occurrence_id": f"{page['page_id']}-M{start:05d}-{end:05d}",
        "page_id": page["page_id"],
        "section_id": page["section_id"],
        "source_id": page["source_id"],
        "span_start": start,
        "span_end": end,
        "observed_span": text,
        "sentence_text": sentence_text,
        "label": "PERSON_REF" if title_only_surname else label,
        "mention_kind": "title_surname" if title else "explicit_name",
        "title": title,
        "name_string": name_string,
        "name_variant_group_id": None,
        "candidate_person_ids": [],
        "resolved_person_id": None,
        "resolution_status": "unreviewed",
        "resolution_scope": None,
        "resolution_rule_id": None,
        "review_note": None,
        "non_merge_check": "pending",
        "model_label": model_label,
    }


def extract_mentions(pages: list[dict[str, Any]], model_name: str = "en_core_web_sm") -> list[dict[str, Any]]:
    nlp = spacy.load(model_name)
    output: list[dict[str, Any]] = []
    for page in pages:
        text = page["text"]
        doc = nlp(text)
        spans: dict[tuple[int, int], dict[str, Any]] = {}
        sentences = list(doc.sents)

        def sentence_for(start: int, end: int) -> str:
            for sent in sentences:
                if sent.start_char <= start < sent.end_char or sent.start_char < end <= sent.end_char:
                    return sent.text
            return text[max(0, start - 160): min(len(text), end + 160)]

        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            start, end = ent.start_char, ent.end_char
            observed = text[start:end]
            # Include a directly adjacent title, tolerating OCR punctuation.
            prefix = text[max(0, start - 24):start]
            title_match = re.search(r"(?i)(?:^|[^\w])((?:captain|captaine|capt\.?|sir|lady|lord|chief|king|queen|weroance|doctor|dr\.?|master|mistress|mr\.?|mrs\.?|governor|[mM][’'*ᵣ]+))\s*[,;:\-—–]?\s*$", prefix)
            if title_match:
                start = max(0, start - (len(prefix) - title_match.start(1)))
                observed = text[start:end]
            spans[(start, end)] = _mention(page, start, end, "PERSON_NAME", observed, sentence_for(start, end), ent.label_)

        # Add conservative title + name spans that the general model missed.
        for match in TITLE_RE.finditer(text):
            start, end = match.span()
            if (start, end) not in spans:
                spans[(start, end)] = _mention(page, start, end, "PERSON_NAME", match.group(), sentence_for(start, end), "TITLE_PATTERN")

        output.extend(sorted(spans.values(), key=lambda item: item["span_start"]))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    mentions = extract_mentions(load_primary_pages(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(mentions, handle, ensure_ascii=False, indent=2)
    print(f"Extracted {len(mentions)} candidate mentions")


if __name__ == "__main__":
    main()
