# IR JSON (T / A / M)

You do **not** need this file to complete a run. Follow the branch in `SKILL.md`.

| IR | Layered by object? | Notes |
|----|--------------------|-------|
| **T** | No | One catalog of all roles |
| **A** | **No** | One joint attribution for every `style_id` in T (full or sparse patch) — not one call per object layer. |
| **M** | **Yes** | `section` / `paragraph.body` / `table` / `image` each map only to T entries of that `object`. |

Apply ignores `by_layer.paragraph.table_cell` and `by_layer.run` even if they are filled. Hop 2 writes `table_cells.cell_style` only. Apply looks up chrome on `Tbl*.cells[slot]` and fonts on `Tbl*.cell_paragraphs[slot]`. Do not classify source cells from a geometry plan. Skip empty body paragraphs. Write `table_cells` in the JSON file; do not narrate cells in chat.

Apply always wants three inputs: T (`--target-set`), A (`--target-props`), M (`--target-loc`). They are **files**, not three LLM stages.

| Branch | T | A | M |
|--------|---|---|---|
| `text_requirement` | You write `target_set.json` (joint) | You write `target_props.json` (joint A for all objects; same turn as T) | Layer walk → `target_loc.json` |
| `template` | `stylesheet.json` (`target_set_skeleton`) | `stylesheet.json` (`target_props`, includes section header/footer) | Layer walk only |
| `template_w_text` naming | same stylesheet (optional rename of `display_name` only) | same stylesheet | Layer walk |
| `template_w_text` patch | stylesheet | copy `target_props`, joint sparse patches for named keys only | Layer walk |

`object` ∈ `section` | `paragraph.body` | `paragraph.table_cell` | `table` | `image` | `run`. Prefer section, `paragraph.body`, table. Do not enumerate cell paragraphs in `by_layer.paragraph.table_cell`. Put per-cell slot labels in `table_cells`.

A **cluster** (`ParaCluster0`, `SecCluster3`, …) is a bucket of template objects with the same whitelist formatting hash — not a Word style name. `Tbl*` entries carry usage evidence: `captions` (template table captions) and `header_rows` (first-row labels). `typical_sections` is a weak prior for which `Sec*` that look sits in. Source tables in the parse layer files carry `caption`, `header_row`, `section_index`, and nonempty `before`/`after`. Do not `cat` the full `preview.ids.json`; follow `preview.ids.manifest.json` (one whole layer per file).

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

**Text-only / sparse patch: joint attribution — not per object layer.** Emit props for every role in T (full) or every role the revision names (sparse) in **one** JSON. Whitelists still differ by `object`; do not fan out “section pass → paragraph pass → table pass.” Template-only: do not rewrite; use stylesheet `target_props`.

Section specs may include top-level `header` / `footer` / `header_first` (copied from the template exemplar). Table specs use designed slots (`header` / `data` / `label` / `value` / `stub`) plus `cell_paragraphs` (slot → `Para*Cell`). Do not put a geometry plan in A for the agent to read.

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
    "cell_paragraphs": { "header": "ParaCluster0Cell", "data": "ParaCluster1Cell" }
  }
}
```

Unknown keys are dropped silently at apply time.

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
      {"row": 1, "col": 1, "cell_style": "header"}
    ]
  },
  "paragraph_runs": {}
}
```

Source `location_id`s from the parse layer files (`preview.ids.manifest.json` `read_order`). On template branches the values are stylesheet ids. Omitted ids keep init formatting. Merge each whole layer with `merge_loc.py --layer-loc` (one-layer JSON fragment of this schema). Do not narrate classifications, re-list roles/examples, or dump the assembled full loc in chat; the next message after each layer `cat` must be the merge. Apply only after every `n > 0` layer is merged. Do not split a layer unless that layer’s `cat` was truncated.

```json
{
  "paragraph_runs": {
    "12": [{ "text": "Priority issues:", "run_style": "RunLeadin" }]
  }
}
```
