# NER review sample: how to mark it

File: `ner_review_sample.csv`, 864 predictions from 50 record pages. Pages are interleaved across the four volumes (`review_order`), so stopping after page 20 still gives a usable sample.

In `context`, the predicted text is in `[[double brackets]]`.

## Mark what's right; leave the rest blank

In `review_status`, write:

| Write | When the bracketed text is… |
|---|---|
| `person` | a real person, whatever the `predicted_label` says |
| `place` | a real place, whatever the `predicted_label` says |
| *(blank)* | not a person or place |

That's all that's required.

## Optional extras

| Column | Fill in when |
|---|---|
| `correct_entity` | You marked `person`/`place`, but `entity_name` is wrong or empty and you know who/what it is (e.g. `Nicholas Ferrar`) |
| `review_note` | The brackets are cut short or run long: write the exact correct text |

## Missed names

If you notice a person or place with no row, add a row with:
- `page_id`
- `review_status` = `person` or `place`
- `observed_span`: the exact text
- `review_note` = `missed`

## When you stop

Tell me the last `review_order` page you finished, e.g. "done through 20". Blank rows on those pages count as "not a person or place"; later pages are ignored.

## Quick conventions

- **Titles.** A title attached to a name is part of the person: `m' Iohn Ferrar`, `Captaine Argall`, `Ea: of Southampton` are all `person`.
- **Not people.** An office without a name (`m' Deputy`, `m* Atturney`, `the Treasuror`) stays blank.
- **Places inside titles or names.** `Earle of Southampton` is a `person`, and so is "Bishop of London". `Virginia` in `Virginia Company` / `Virginia Court` stays blank.
- **Ships and archives.** Ships named after places (`the Southampton`) and archive citations (`Magdalene College, Cambridge`) stay blank.
