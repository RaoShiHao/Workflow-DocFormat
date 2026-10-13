# M — table (two hops)

Never mix the hops into “copy specimen `r1_cN`”. Hop 2 is required whenever hop 1 ran, even if there is only one `Tbl*` (`skip_if_single` does **not** apply here).

## Hop 1 — `preview.ids.table.json` → `by_layer.table`

Pick `table_style` from **usage**: this table’s `caption` + `header_row` vs that `Tbl*` `caption_type` / `header_semantics` / `captions` / `header_rows`. Shape (rows × cols) may help. `section_index` / `typical_sections` / `section_style` are a **weak** prior — the same `Sec*` can host an open summary table and a grid.

**Do not** use source-table borders: init often already has Word `single`; the target look may strip them to `nil`.

```json
{"by_layer":{"table":{"1":"TblCluster0"}},"table_cells":{}}
```

## Hop 2 — `preview.ids.table.cells.json` → `table_cells`

Classify **every** physical cell into a `cell_style` that **that** `Tbl*` declares (`cell_slots` / `cell_slots_by_table`). Write **only** `cell_style`.

- `header` = column titles (often row 1, not always)
- `stub` = row titles (often col 1, not always)
- Both may share one `Para*Cell` via `cell_paragraphs`

In-cell fonts come from `Tbl*.cell_paragraphs[slot]`. Do not pick `paragraph_style`. Do not assume row 1 is header. Do not invent a geometry plan or copy `r1_c1`. Do not use another table’s slots.

When unsure, omit that cell. Merge the entire `table_cells` object after this hop:

```json
{"by_layer":{},"table_cells":{"1":[{"row":1,"col":1,"cell_style":"header"},{"row":2,"col":1,"cell_style":"stub"},{"row":2,"col":2,"cell_style":"data"}]}}
```

Do **not** fill `by_layer.paragraph.table_cell`. Unlabeled cells keep source formatting.
