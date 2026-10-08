"""Split hOCR pages into running head, body, margin notes, and footnotes.

Works on hOCR from either the Internet Archive's ABBYY layer (one file for the
whole volume) or Tesseract (one file per page). Word and line bounding boxes
separate what plain OCR text runs together:

* **running head**: lines in the top band of the page (``JULY 16, 1621 515``);
* **margin notes**: narrow lines standing outside the body column (the Court
  Book's side headings, e.g. ``Bills approued of``);
* **footnotes**: smaller lines below the body at the foot of the page;
* **body**: everything else, in reading order, with paragraph breaks.

Output is one JSON record per page image (leaf), with the printed page number
when a page-number map is supplied.
"""

from __future__ import annotations

import argparse
import html
import json
from html.parser import HTMLParser
import re
import statistics
from pathlib import Path


# ``title`` may hold the other quote type inside it (Tesseract: title='image "x.jp2"; bbox ...').
PAGE_RE = re.compile(
    r"<div[^>]*class=(['\"])ocr_page\1[^>]*title=(['\"])(.*?)\2[^>]*>(.*?)(?=<div[^>]*class=['\"]ocr_page|\Z)", re.S)
BBOX_RE = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
CONF_RE = re.compile(r"x_wconf (\d+)")
TAG_RE = re.compile(r"<[^>]+>")

HEAD_BAND = 0.075      # top share of the page holding the running head
FOOT_BAND = 0.62       # footnotes start below this share of the page height
MARGIN_MAX_WIDTH = 0.34


def bbox(title: str) -> tuple[int, int, int, int] | None:
    match = BBOX_RE.search(title)
    return tuple(int(v) for v in match.groups()) if match else None


class _HocrLines(HTMLParser):
    """Collect lines (in reading order) and their words from one page's hOCR."""

    LINE_CLASSES = {"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[dict] = []
        self.stack: list[str] = []
        self.par = -1
        self.block = -1
        self.word: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = (attrs.get("class") or "").split()
        title = attrs.get("title") or ""
        kind = next((c for c in cls if c.startswith("ocr")), "")
        self.stack.append(kind)
        if kind == "ocr_carea":
            self.block += 1
        elif kind == "ocr_par":
            self.par += 1
        elif kind in self.LINE_CLASSES:
            self.lines.append({"words": [], "par": self.par, "block": self.block})
        elif kind == "ocrx_word" and self.lines:
            box = bbox(title)
            conf = CONF_RE.search(title)
            self.word = {"text": "", "box": box, "conf": int(conf.group(1)) if conf else None}

    def handle_endtag(self, tag):
        kind = self.stack.pop() if self.stack else ""
        if kind == "ocrx_word" and self.word is not None:
            text, box = self.word["text"].strip(), self.word["box"]
            if text and box:
                self.lines[-1]["words"].append({"text": text, "x0": box[0], "y0": box[1], "x1": box[2],
                                                "y1": box[3], "conf": self.word["conf"]})
            self.word = None

    def handle_data(self, data):
        if self.word is not None:
            self.word["text"] += data


def parse_lines(page_html: str) -> list[dict]:
    """Lines with words (text + bbox + confidence), paragraph and block index, in reading order."""
    parser = _HocrLines()
    parser.feed(page_html)
    return [line for line in parser.lines if line["words"]]


def segments(line: dict, gap: float) -> list[dict]:
    """Split a line where words are far apart (a margin note glued onto body text)."""
    out, current = [], [line["words"][0]]
    for word in line["words"][1:]:
        if word["x0"] - current[-1]["x1"] > gap:
            out.append(current)
            current = []
        current.append(word)
    out.append(current)
    return [{
        "text": " ".join(w["text"] for w in seg), "x0": min(w["x0"] for w in seg), "y0": min(w["y0"] for w in seg),
        "x1": max(w["x1"] for w in seg), "y1": max(w["y1"] for w in seg), "par": line["par"], "block": line["block"],
        "word_height": statistics.median(w["y1"] - w["y0"] for w in seg),
        "conf": [w["conf"] for w in seg if w["conf"] is not None],
    } for seg in out]


HEAD_CUE_RE = re.compile(r"(?i)RECORDS|VIRGINIA\s+COMPANY|^\s*\d{1,3}\b|\b\d{1,3}\s*$|\b1[56]\d\d\b")


def group_margin_notes(margins: list[dict], left: float, width: int) -> list[dict]:
    """One note per OCR block, lines joined and line-break hyphens closed up."""
    notes: list[dict] = []
    for seg in margins:
        side = "left" if seg["x1"] <= left + 0.03 * width else "right"
        if notes and notes[-1]["block"] == seg["block"] and notes[-1]["side"] == side:
            note = notes[-1]
            note["text"] = (note["text"][:-1] + seg["text"]) if note["text"].endswith("-") \
                else note["text"] + " " + seg["text"]
        else:
            notes.append({"text": seg["text"], "side": side, "y": seg["y0"], "block": seg["block"]})
    # Specks and edge marks near the margin (``727.``, ``Tr.``, ``| |``) are not notes.
    return [n for n in notes if len(re.findall(r"[A-Za-z]{3,}", n["text"])) >= 2]


def classify(lines: list[dict], width: int, height: int) -> dict:
    if not lines:
        return {"head": "", "body": "", "margin_notes": [], "footnotes": "", "mean_conf": None}
    segs = [seg for line in lines for seg in segments(line, gap=0.04 * width)]
    wide = [s for s in segs if (s["x1"] - s["x0"]) > 0.45 * width]
    left = statistics.median(s["x0"] for s in wide) if wide else min(s["x0"] for s in segs)
    right = statistics.median(s["x1"] for s in wide) if wide else max(s["x1"] for s in segs)
    body_height = statistics.median(s["y1"] - s["y0"] for s in (wide or segs))
    body_word_height = statistics.median(s["word_height"] for s in (wide or segs))
    text_top = min(s["y0"] for s in segs)
    text_bottom = max(s["y1"] for s in segs)
    head, body, margins, feet = [], [], [], []
    top_line = [s for s in segs if s["y0"] <= text_top + 1.2 * body_height]
    head_ids = {id(s) for s in top_line} if any(HEAD_CUE_RE.search(s["text"]) for s in top_line) else set()
    for seg in segs:  # reading order from the OCR engine
        w, h = seg["x1"] - seg["x0"], seg["y1"] - seg["y0"]
        if id(seg) in head_ids:
            head.append(seg)
        elif w < MARGIN_MAX_WIDTH * width and (seg["x1"] <= left + 0.03 * width or seg["x0"] >= right - 0.03 * width):
            margins.append(seg)
        else:
            body.append(seg)
    # Footnotes are the run of smaller-type lines at the very foot of the page:
    # walk up from the bottom while lines are small, stop at the first body-size line.
    by_y = sorted(body, key=lambda s: s["y0"])
    while by_y and by_y[-1]["word_height"] < 0.8 * body_word_height and \
            by_y[-1]["y0"] > text_top + FOOT_BAND * (text_bottom - text_top):
        feet.insert(0, by_y.pop())
    feet_ids = {id(s) for s in feet}
    body = [s for s in body if id(s) not in feet_ids]

    def join(group: list[dict]) -> str:
        out, last_par, last_y = [], None, None
        for seg in group:
            if last_y is not None and abs(seg["y0"] - last_y) < 0.5 * body_height and out:
                out[-1] += " " + seg["text"]       # same visual line split by a removed margin note
            else:
                if last_par is not None and seg["par"] != last_par:
                    out.append("")
                out.append(seg["text"])
            last_par, last_y = seg["par"], seg["y0"]
        return "\n".join(out)

    confs = [c for s in segs for c in s["conf"]]
    return {
        "head": " ".join(s["text"] for s in sorted(head, key=lambda s: s["x0"])),
        "body": join(body),
        "margin_notes": group_margin_notes(margins, left, width),
        "footnotes": join(feet),
        "mean_conf": round(statistics.mean(confs), 1) if confs else None,
    }


def page_number_map(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = data.get("pages", data)
    return {int(p["leafNum"]): str(p.get("pageNumber", "")) for p in pages if "leafNum" in p}


def parse_hocr_text(text: str, first_leaf: int = 0) -> list[dict]:
    records = []
    for index, (_, _, title, page_html) in enumerate(PAGE_RE.findall(text)):
        box = bbox(title) or (0, 0, 1, 1)
        leaf_match = re.search(r"ppageno (\d+)", title)
        leaf = int(leaf_match.group(1)) if leaf_match else first_leaf + index
        record = {"leaf": leaf}
        record.update(classify(parse_lines(page_html), box[2], box[3]))
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hocr", type=Path, nargs="+", help="One volume hOCR file, or per-page hOCR files.")
    parser.add_argument("--page-numbers", type=Path, help="Internet Archive *_page_numbers.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    numbers = page_number_map(args.page_numbers)
    records = []
    if len(args.hocr) == 1:
        records = parse_hocr_text(args.hocr[0].read_text(encoding="utf-8", errors="replace"))
    else:
        for path in sorted(args.hocr):
            leaf = int(re.search(r"(\d+)\D*$", path.stem).group(1))
            for record in parse_hocr_text(path.read_text(encoding="utf-8", errors="replace"), leaf):
                record["leaf"] = leaf
                records.append(record)
    for record in records:
        record["printed_page"] = numbers.get(record["leaf"], "")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    with_margins = sum(1 for r in records if r["margin_notes"])
    with_feet = sum(1 for r in records if r["footnotes"])
    print(f"{len(records)} pages -> {args.output}; margin notes on {with_margins}, footnotes on {with_feet}")


if __name__ == "__main__":
    main()
