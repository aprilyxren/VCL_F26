"""Load and validate the segmented primary OCR source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "page_id",
    "section_id",
    "volume",
    "source_id",
    "source_page_key",
    "layer",
    "text",
}


def load_primary_pages(path: str | Path) -> list[dict[str, Any]]:
    """Load primary pages and validate their stable source identity fields."""
    source_path = Path(path)
    with source_path.open(encoding="utf-8") as handle:
        pages = json.load(handle)

    if not isinstance(pages, list):
        raise ValueError("Primary source must be a JSON list of page records")

    seen_page_ids: set[str] = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(f"Page {index} is not a JSON object")
        missing = REQUIRED_FIELDS - page.keys()
        if missing:
            raise ValueError(f"Page {index} is missing fields: {sorted(missing)}")
        if page["page_id"] in seen_page_ids:
            raise ValueError(f"Duplicate page_id: {page['page_id']}")
        if page["layer"] != "primary_text":
            raise ValueError(
                f"Non-primary page in primary source: {page['page_id']}"
            )
        seen_page_ids.add(page["page_id"])

    return pages


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    pages = load_primary_pages(args.source)
    print(f"Validated {len(pages)} primary pages")
