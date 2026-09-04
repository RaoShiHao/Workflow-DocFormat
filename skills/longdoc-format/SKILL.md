---
name: longdoc-format
description: >
  Format an existing Word .docx in place from a reference template.docx and/or
  text style requirements. Use when the user wants 格式调整, apply a template
  look, restyle a long report, or migrate formatting while keeping body text.
  Do not use to write new content, fill mail-merge fields, or edit .xlsx/.pptx.
compatibility: Requires Python 3.11+ and officecli on PATH. No pip packages (requirements.txt is empty). This folder is self-contained — scripts do not import LongDocFormatter or the rest of the repo.
metadata:
  author: LongDocFormatter
  version: "0.14"
---

# Long-document formatting

Keep **body text and structure**. Only change formatting.

Copy the whole `longdoc-format/` directory. Extra binary: **officecli**. Reads go through `scripts/parse_docx.py` (`profile=assign` on source). Do not re-implement reads with officecli.

**Stop after apply.** Typical run is 5–8 tool calls. `scripts/lib/*.py` and `references/` are implementation — not documentation.

## Sequence (do this, nothing else)

1. `parse_docx.py` on **source** (once). It writes `preview.ids.json` next to `--preview-out`.
2. `template_stylesheet.py` **only if** `template.docx` exists (once). It writes `stylesheet.roles.json` next to `--out`.
3. `cat` **`preview.ids.json`** and **`stylesheet.roles.json`** (template branch) plus the requirement text if any. Do **not** `cat` `preview.json`, `inventory.json`, `stylesheet.json`, lib, or officecli help.
4. Write JSON: template branch → **only** `target_loc.json`. Text-only → `target_set.json` + `target_props.json` + `target_loc.json`.
5. **Foreground** `python -u scripts/apply_format.py ...` and wait until stdout has `"ok": true`. Then stop.

```bash
python -u scripts/parse_docx.py --docx SOURCE.docx --out inventory.json --preview-out preview.json
# also writes preview.ids.json. profile=assign (default).

python -u scripts/template_stylesheet.py --template TEMPLATE.docx --out stylesheet.json
# also writes stylesheet.roles.json. Uses profile=full on the template.

python -u scripts/apply_format.py \
  --source SOURCE.docx --output OUTPUT.docx \
  --inventory inventory.json \
  --target-set T.json \
  --target-props A.json \
  --target-loc target_loc.json
```

`--target-set` / `--target-props` may both be `stylesheet.json`. `--target-loc` you always write.

If apply rejects a key, delete that key and re-run apply **once**. Do not read `whitelist.py`.

### If a `cat` is truncated

The files on disk are complete. Do **not** `python -c` over inventory / cells / runs, and do not scrape a harness log.

```bash
python -u scripts/preview_ids.py --preview preview.json --out preview.ids.slice.json --layer paragraph.body --from-id 1 --to-id 80
```

Then `cat` that slice. Repeat with the next id range if needed.

### Apply rules (hard)

- Wait until the process **exits**. First line is `{"status":"applying",...}`; last line is `{"ok": true, ...}`.
- Do **not** background (`&`), `ps`, `nohup`, or pipe through `head`/`tail`.
- Do **not** `rm` the output if apply was interrupted. Re-run apply only if `"ok"` never appeared **and** the file is still the source copy.
- Apply uses officecli batch (named styles, one resident `close`). Do not call `officecli set` yourself.

## How to read files

Use `cat` / the host file-read tool on the **compact** files (`preview.ids.json`, `stylesheet.roles.json`). **Never** `python -c` over inventory, cells, or runs.

`stylesheet.json` is T+A for apply. You do not need to `cat` it: `roles.json` has `style_id` / `object` / `n` / `examples` / `captions` / `header_rows` (on `Tbl*`; `typical_sections` is auxiliary).

## Pick a branch

| Files on the table | Branch |
|--------------------|--------|
| source + **text only** (no template) | [A. text_requirement](#a-text_requirement--text-only) |
| source + **template only** (no requirement text) | [B. template](#b-template--template-only) |
| source + **template + text** | [C. template_w_text](#c-template_w_text--template--text) |

T / A / M are **files for apply**, not three extra LLM rounds.

---

## A. `text_requirement` — text only

1. Parse source. Skip stylesheet.
2. `cat` the requirement + `preview.ids.json` once each.
3. One write: `target_set.json` **and** `target_props.json`. Then `target_loc.json`.
4. Apply.

Do not invent a template look. Do not classify every cell/run.

### T + A

`object` ∈ `section` | `paragraph.body` | `table` | `image` | `run` (run only if the brief names a span). Ids: `Sec*` / `Para*` / `Tbl*` / `Img*` / `Run*`. `description` = purpose, not font size.

Optional `Para*Cell` entries (`object`: `paragraph.table_cell`) only if the brief names cell fonts; then point each `Tbl*` at them with `cell_paragraphs`.

```json
{
  "entries": [
    {"style_id": "SecBody", "object": "section", "display_name": "Body section", "description": "Main document section."},
    {"style_id": "ParaHeading1", "object": "paragraph.body", "display_name": "Heading 1", "description": "Top-level titles (outlineLvl 0)."},
    {"style_id": "ParaBody", "object": "paragraph.body", "display_name": "Body", "description": "Narrative paragraphs."},
    {"style_id": "TblData", "object": "table", "display_name": "Data table", "description": "Body tables."}
  ]
}
```

Allowed A keys (nothing else). Use `12pt` not `2em`.

- paragraph: `font.latin`, `font.ea`, `size`, `bold`, `italic`, `color`, `align`, `lineSpacing`, `spaceBefore`, `spaceAfter`, `firstLineIndent`, `hangingIndent`, `indent`, `rightIndent`, `outlineLvl`, `keepNext`, `pageBreakBefore`, `listStyle`, `numLevel`
- section: `marginTop`, `marginBottom`, `marginLeft`, `marginRight`, `marginHeader`, `marginFooter`, `orientation`, `columns`, `pgBorders`, `type`, `pageNumFmt`, `pageStart`, `titlePage` plus optional `header` / `footer` / `header_first` (`text`, `field`, `align`, `size`, `color`, `bold`, `font.ea`, `font.latin`)
- table `table_format`: `border.all`, `border.top`, `border.bottom`, `border.left`, `border.right`, `align`, `width`, `layout`, `repeat_header`
- table `cells`: named cell-styles on that `Tbl*` (`header` / `data` / `label` / `value` / `stub`) — `fill`, `valign`, `border.*`. Never `r1_c1`.
- table `cell_style_plan`: how those cell-styles are stored on the **template** `Tbl*` (mode / header_row / column_styles). Apply does **not** use this to guess source cells.
- table `cell_paragraphs`: slot → `Para*Cell` style_id. You label each source cell in M `table_cells`; leave `by_layer.paragraph.table_cell` empty.
- image: `width`, `height`, `hAlign`
- run: `bold`, `italic`, `color`, `underline`, `font.ea`, `font.latin`, `size`, `caps`, `smallcaps`, `superscript`, `subscript`

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
    "table_format": {"align": "center", "border.all": "single"},
    "cells": {
      "header": {"fill": "#F0F0F0", "valign": "center"},
      "data": {"valign": "center"}
    },
    "cell_style_plan": {"mode": "row", "header_row": true, "row_styles": ["header", "data"]}
  }
}
```

Then M as in [Mapping (M)](#mapping-m--all-branches).

---

## B. `template` — template only

No LLM for T or A. Stylesheet **is** T + A (includes section header/footer and table `cell_paragraphs`).

1. Parse **source**.
2. `template_stylesheet.py` → `stylesheet.json` + `stylesheet.roles.json`.
3. `cat` `preview.ids.json` and `stylesheet.roles.json` once each. Do not copy template body text.
4. Write **only** `target_loc.json` (source ids → stylesheet `style_id`s such as `ParaCluster0`).
5. Apply with the stylesheet for T and A:

```bash
python -u scripts/apply_format.py \
  --source SOURCE.docx --output OUTPUT.docx \
  --inventory inventory.json \
  --target-set stylesheet.json \
  --target-props stylesheet.json \
  --target-loc target_loc.json
```

A **cluster** is a group of template objects that share the same formatting signature, not a Word gallery name (`Heading 1`). `SecCluster3` is “the 4th distinct section look on the template”. Read `roles.json` `examples` / `n` and bind each source object to the cluster it resembles. **Do not** assign every section, image, or empty paragraph to the cluster with the largest `n`.

---

## C. `template_w_text` — template + text

1. Parse source. Build stylesheet.
2. `cat` `text_input.md` once.

**Naming / heading interpretation only** (default): keep template fonts. Write only `target_loc.json`. Apply like branch B. You may fill `display_name` / `description` — **keep `style_id` keys unchanged**.

**Relative patch** (text names a delta: size / font / margin / line spacing / bold): copy `target_props` and change **only the named keys**. Apply with `--target-set stylesheet.json --target-props target_props.json --target-loc target_loc.json`.

Do not induce a new catalog from the text.

---

## Mapping (M) — all branches

Map **source** ids from `preview.ids.json`. `_map_these` = `section`, `paragraph.body`, `table`, `image`. Leave `run` and `paragraph.table_cell` **empty**. Cell chrome and cell fonts come from `table_cells` + the chosen `Tbl*` (its `cells` / `cell_paragraphs`).

**Required:** `section` + `paragraph.body`. Unmapped ids keep source formatting.

**Sections:** cover / TOC / body are different clusters when the template differs (margins, `pageNumFmt`, `titlePage`, header/footer). Match preview section `text` to `roles.json` examples. Do not bind all sections to one `Sec*`.

**Body paragraphs:** `outlineLvl` beats “looks like a title”. Empty paragraphs follow the surrounding body `Para*`, not a leftover “keep source” cluster.

**Tables (two hops, both LLM):** (1) map the table id → one `Tbl*` **role** (cover / three-line / grid) so apply can set `table_format`. Match **caption** + **header_row** to that `Tbl*` `captions` / `header_rows` (usage). `section_index` / `typical_sections` are a weak prior — the same `Sec*` can host an open summary table and a grid matrix; do not pick a grid `Tbl*` for a summary caption just because both sit in body. Do not use source-table borders: init often has Word default `single` while the template role strips them. (2) **you** classify every physical source cell into a cell-style that **that** `Tbl*` declared (`header` / `data` / `label` / `value` / `stub` / …) plus an in-cell `Para*Cell`. Do not assume row 1 is header or clone extra rows by a fixed plan. Write the labels in `table_cells`. Do **not** fill `by_layer.paragraph.table_cell`. Unlabeled cells keep source formatting; apply will not invent slots from `cell_style_plan`.

**Images:** if several `ImgCluster*` exist, match size/`examples`; do not bind every image to one cluster.

```json
{
  "by_layer": {
    "section": {"1": "SecCluster0", "2": "SecCluster1", "3": "SecCluster2"},
    "paragraph.body": {"1": "ParaCluster0", "2": "ParaCluster1"},
    "table": {"1": "TblCluster0"},
    "image": {"1": "ImgCluster0", "2": "ImgCluster1"},
    "run": {},
    "paragraph.table_cell": {}
  },
  "table_cells": {
    "1": [
      {"row": 1, "col": 1, "cell_style": "header", "paragraph_style": "ParaCluster0Cell"},
      {"row": 1, "col": 2, "cell_style": "header", "paragraph_style": "ParaCluster0Cell"},
      {"row": 2, "col": 1, "cell_style": "data", "paragraph_style": "ParaCluster1Cell"}
    ]
  },
  "paragraph_runs": {}
}
```

On template branches, values are stylesheet ids (`ParaCluster0`, …), not Word gallery names.

---

## Do not

- Rewrite body text or change structure
- Open `scripts/lib/*.py`, `references/ir.md`, `officecli --help`, or `python -c` over inventory / cells / runs
- Write a generator / `apply_patch` helper to emit T/A/M
- On template branches: LLM-invent T or A instead of `stylesheet.json`
- `officecli set` / `batch` / python-docx / unzip instead of `apply_format.py`
- Map every cell as `by_layer.paragraph.table_cell` or `by_layer.run`
- Leave `table_cells` empty and expect apply to guess header/data from geometry
- Treat Word gallery names (`Heading 1`) as `style_id` unless they are ids in T / stylesheet
- Pipe apply through `head`, background it, or delete a partially written `OUTPUT.docx`
- Dump every section / image / empty paragraph onto the cluster with the largest `n`
