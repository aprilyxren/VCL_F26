# Re-OCR of *The Records of the Virginia Company of London*

This folder holds a fresh, layout-aware OCR of Susan Myra Kingsbury's four-volume edition. Volumes I and II appeared in 1906, Volume III in 1933 and Volume IV in 1935. Every later step in the project, from NER to dating to the associations, reads its text from here, by way of `data/cleaned/combined_primary_text.json`.

## Why the volumes were re-OCR'd

The original OCR is in `data/raw/Vol1–4StoreOCRPerPage.json`: plain text, one string per page. It has two problems:

- **Margin notes are mixed into the body.** The Court Book's side headings, such as *Bills approued of*, are run into the body text, often in the middle of a line.
- **Pages are sometimes out of order.**

Tesseract's hOCR output keeps a bounding box for every word. With those boxes, each page can be split into its running head, body, margin notes and footnotes.

## Files

| File | What it is |
| --- | --- |
| `vol{1-4}_tesseract_layout.json` | One record per page image (*leaf*). Fields: `leaf`, `printed_page`, `head` (the running head), `body`, `margin_notes` (a list of `{text, ...}`), `footnotes` and `mean_conf` (Tesseract's mean word confidence, 0–100). Leaves without a printed page number are covers, plates and blank pages. |
| `vol{1-4}_comparison.csv` | Old OCR against new, one row per printed page. Each row checks whether the running head yields a date, how many known person and place names are found, how many margin notes there are, how many of them the old text had glued into the body, how many footnote characters there are, and the new OCR's confidence. |
| `page_mapping.csv` | How each page in `combined_primary_text.json` (`V01-P0001` ...) was matched to a page image, with the word overlap between the old and new text and the `ocr_source` chosen for it. |

## How it was done

### 1. Download the page images

The scans come from the Internet Archive, one item per volume: `recordsofvirgini01virg` to `recordsofvirgini04virg`. For each volume, download:

- `<id>_jp2.zip`, the page images in JPEG 2000;
- `<id>_page_numbers.json`, the Archive's map from each page image to its printed page number.

The download URL is `https://archive.org/download/<id>/<file>`.

### 2. OCR each page with Tesseract 5.5

Tesseract (`brew install tesseract`) uses its English model, with automatic page segmentation (`--psm 1`) and hOCR output:

```sh
for f in jp2/*.jp2; do
  b=$(basename "$f" .jp2)
  tesseract "$f" "hocr/$b" -l eng --psm 1 hocr
done
```

The Archive's own ABBYY hOCR was also tried; `ocr_layout.py` reads either format. The Tesseract output is the one used here.

### 3. Split each page into regions

```sh
.venv/bin/python src/ocr_layout.py hocr/*.hocr \
  --page-numbers recordsofvirgini01virg_page_numbers.json \
  --output data/raw/reocr/vol1_tesseract_layout.json
```

[`src/ocr_layout.py`](../../../src/ocr_layout.py) sorts the lines on each page by position:

- **Running head:** the top 7.5% of the page.
- **Margin notes:** narrow lines (under 34% of the page width) standing outside the body column.
- **Footnotes:** smaller lines in the lower part of the page.
- **Body:** everything else, in reading order.

### 4. Compare the new OCR with the old

```sh
cd src && ../.venv/bin/python compare_ocr.py --volume 1 \
  --raw ../data/raw/Vol1StoreOCRPerPage.json \
  --reocr ../data/raw/reocr/vol1_tesseract_layout.json \
  --dictionary ../data/authority/list_of_records_person_alias_dictionary.json \
  --authority ../data/authority/place_authority_seed.csv \
  --place-resolutions ../configs/place_alias_resolutions.json \
  --output ../data/raw/reocr/vol1_comparison.csv
```

| Volume | Pages compared | Mean confidence | Heads dated (old → new) | Margin notes | Glued into old text |
| --- | --- | --- | --- | --- | --- |
| I | 473 | 89.4 | 201 → 201 | 342 | 94 |
| II | 481 | 89.9 | 169 → 219 | 23 | 6 |
| III | 665 | 88.8 | 247 → 244 | 64 | 6 |
| IV | 520 | 90.2 | 247 → 247 | 53 | 10 |

Person and place name counts are close between old and new, within about 10% in every volume. The re-OCR's gains are in the structure of the page, not in the words themselves.

### 5. Rebuild the primary text

```sh
.venv/bin/python src/build_primary_text_from_reocr.py \
  --primary data/cleaned/combined_primary_text.json \
  --reocr-dir data/raw/reocr \
  --output data/cleaned/combined_primary_text.json \
  --mapping-output data/raw/reocr/page_mapping.csv
```

Each existing page ID is matched to a page image by its printed page number. The match is then checked by word overlap with the old text (at least 25%), and if it fails, the three nearest page images are tried. This keeps a numbering error at the Archive from silently swapping pages.

The page's new `text` is its running head followed by its body. Margin notes and footnotes go in their own fields.

| `ocr_source` | Pages | Meaning |
| --- | --- | --- |
| `tesseract_reocr` | 2,430 | New OCR used |
| `original` | 33 | No verified match; the old text is kept |
| `duplicate` | 11 | The original scans repeat these pages (Vol. II); the text is emptied |

After rebuilding, rerun the downstream steps: `flag_page_roles.py`, `date_pages.py`, `build_ner.py` and `build_associations.py`.

## Known OCR errors

Tesseract reads Kingsbury's old spellings (*vsage*, *gentlenesse*, *Sauages*) without trouble, but it still misreads some words. For example, on Vol. III p. 556, Waterhouse's *Sauages* comes out as *Sausages*. Quote from the printed edition, not the OCR text.

## Adding a new text

Steps 1–4 work on any printed book as they stand. A PDF first needs its pages turned into images:

```sh
pdftoppm -r 300 -png book.pdf pages/p
```

Step 5 doesn't work for a new text. It replaces the text of page IDs that already exist, so a new volume needs a short script that creates page IDs (`V05-P0001` ...) in the format [`src/source_loader.py`](../../../src/source_loader.py) reads.

Manuscripts and secretary hand are beyond Tesseract's English model; they would need a handwriting tool such as Transkribus or Kraken.
