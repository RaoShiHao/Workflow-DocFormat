# T from text (`target_set.json`)

Use on the **text-only** branch, after you `cat` the requirement. One joint catalog for every object type. Do not walk section then paragraph then table.

Object layers: `section`, `paragraph.body`, `paragraph.table_cell`, `table`, `image`, `run`.

- Table-cell **chrome** is nested under `table`, not its own style.
- `paragraph.table_cell` is one **document-wide** in-cell ladder (`ParaTblHeaderCell` / `ParaTblDataCell`, plus Label/Value/Stub/Note if the brief needs them). Do not clone a private `Para*Cell` family per table, collapse cells into one `ParaTableCellText`, or reuse `ParaBody` inside cells.
- Each `Tbl*` only points at rungs in A via `cell_paragraphs`.
- Heading numbering schemes are not a style role. Do not turn “Chapter 1” into a catalog entry.
- Headers/footers belong to the matching **section** style.
- `run` is an inline fragment (`Run*`), not a body-level block. Add it only if the brief names a span.
- `description` is semantic role information, not font size / margins.
- Prefer ids `Sec*` / `Para*` / `Tbl*` / `Img*` / `Run*`.

Write `target_set.json` as:

```json
{
  "entries": [
    {"style_id": "SecBody", "object": "section", "display_name": "Body section", "description": "Main document section."},
    {"style_id": "ParaHeading1", "object": "paragraph.body", "display_name": "Heading 1", "description": "Top-level titles."},
    {"style_id": "ParaBody", "object": "paragraph.body", "display_name": "Body", "description": "Narrative paragraphs."},
    {"style_id": "ParaTblHeaderCell", "object": "paragraph.table_cell", "display_name": "Header cell", "description": "In-cell header / stub titles."},
    {"style_id": "ParaTblDataCell", "object": "paragraph.table_cell", "display_name": "Data cell", "description": "In-cell body values."},
    {"style_id": "TblData", "object": "table", "display_name": "Data table", "description": "Body tables."}
  ]
}
```

Then fill A for **every** `style_id` in this T (`references/a_text.md`) in the same write cycle, before mapping.
