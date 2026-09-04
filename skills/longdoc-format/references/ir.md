# IR JSON (T / A / M)

You do **not** need this file to complete a run. Follow the branch in `SKILL.md`.
Apply ignores `by_layer.paragraph.table_cell` and `by_layer.run` even if they are filled. Cell fonts still apply: you label each source cell in M `table_cells` (`cell_style` + `paragraph_style`). Apply looks up that slot on the chosen `Tbl*` (`cells` / `cell_paragraphs`). It does not classify source cells from `cell_style_plan`.

Apply always wants three inputs: T (`--target-set`), A (`--target-props`), M (`--target-loc`). They are **files**, not three LLM stages.

| Branch | T | A | M |
|--------|---|---|---|
| `text_requirement` | You write `target_set.json` | You write `target_props.json` (same turn as T) | You write `target_loc.json` |
| `template` | `stylesheet.json` (`target_set_skeleton`) | `stylesheet.json` (`target_props`, includes section header/footer) | You write `target_loc.json` only |
| `template_w_text` naming | same stylesheet (optional rename of `display_name` only) | same stylesheet | You write M |
| `template_w_text` patch | stylesheet | copy `target_props`, change mentioned keys | You write M |

`object` ∈ `section` | `paragraph.body` | `paragraph.table_cell` | `table` | `image` | `run`. Prefer section, `paragraph.body`, table. Do not enumerate cell paragraphs in `by_layer.paragraph.table_cell`. Put per-cell slot labels in `table_cells`.

A **cluster** (`ParaCluster0`, `SecCluster3`, …) is a bucket of template objects with the same whitelist formatting hash — not a Word style name. `Tbl*` entries carry usage evidence: `captions` (template table captions) and `header_rows` (first-row labels). `typical_sections` is a weak prior for which `Sec*` that look sits in. Source tables in `preview.ids.json` carry `caption`, `header_row`, `section_index`, and nonempty `before`/`after`.

## T — `target_set.json` (text-only, or optional naming copy)

```json
{
  "entries": [
    {
      "style_id": "ParaHeading1",
      "object": "paragraph.body",
      "display_name": "Heading 1",
      "description": "Top-level section titles in the report body."
    }
  ]
}
```

Stylesheet equivalent: `target_set_skeleton.entries`. Optional fields: `exemplar_path`, `exemplar_location_id`.

## A — `target_props.json`

Text-only: fill from the brief. Template: do not rewrite; use stylesheet `target_props`.

Section specs may include top-level `header` / `footer` / `header_first` (copied from the template exemplar). Table specs use designed slots (`header` / `data` / `label` / `value` / `stub`) plus `cell_style_plan` and `cell_paragraphs` — the inverse of AutoDataBuild's Tbl* / ParaTbl* / plan, not a frozen r1_cN specimen.

```json
{
  "ParaBody": {
    "object": "paragraph.body",
    "props": {
      "font.ea": "宋体",
      "font.latin": "Times New Roman",
      "size": "12pt",
      "align": "both",
      "firstLineIndent": "24pt",
      "lineSpacing": "1.5"
    }
  },
  "TblData": {
    "object": "table",
    "table_format": { "align": "center", "border.all": "single" },
    "cells": {
      "header": { "fill": "#F0F0F0", "valign": "center" },
      "data": { "valign": "center" }
    },
    "cell_style_plan": { "mode": "row", "header_row": true, "row_styles": ["header", "data"] },
    "cell_paragraphs": { "header": "ParaCluster0Cell", "data": "ParaCluster1Cell" }
  }
}
```

Unknown keys → apply error → drop the key.

## M — `target_loc.json`

```json
{
  "by_layer": {
    "section": { "1": "SecBody" },
    "paragraph.body": { "1": "ParaHeading1", "2": "ParaBody" },
    "paragraph.table_cell": {},
    "table": { "1": "TblData" },
    "image": {},
    "run": {}
  },
  "table_cells": {
    "1": [
      {"row": 1, "col": 1, "cell_style": "header", "paragraph_style": "ParaCluster0Cell"},
      {"row": 2, "col": 1, "cell_style": "data", "paragraph_style": "ParaCluster1Cell"}
    ]
  },
  "paragraph_runs": {}
}
```

Source `location_id`s from `preview.ids.json`. On template branches the values are stylesheet ids. Omitted ids keep init formatting.

```json
{
  "paragraph_runs": {
    "12": [{ "text": "Priority issues:", "run_style": "RunLeadin" }]
  }
}
```
