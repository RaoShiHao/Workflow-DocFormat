---
name: DocFmt-ToolSkill
description: >
  Format an existing Word .docx in place from a reference template.docx and/or
  text style requirements. Use when the user wants 格式调整, apply a template
  look, restyle a long report, or migrate formatting while keeping body text.
  Do not use to write new content, fill mail-merge fields, or edit .xlsx/.pptx.
compatibility: Requires Python 3.11+ and officecli on PATH. No pip packages (requirements.txt is empty). This folder is self-contained — scripts do not import LongDocFmt or the rest of the repo.
metadata:
  author: LongDocFmt
  version: "0.28"
---

# Long-document formatting

Keep **body text and structure**. Only change formatting.

Copy the whole `DocFmt-ToolSkill/` directory. Extra binary: **officecli**. Reads go through `scripts/parse_docx.py` (`profile=assign` on source). Do not re-implement reads with officecli.

Scripts do the non-LLM work (parse, template clustering, merge, apply). You do the LLM work: choose the T/A recipe, write T/A when the recipe needs it, and bind each source object. Follow this file as the pipeline. Open the matching `references/` note when you reach that step.

## IR files apply needs

| File | What it is | Layered? |
|------|------------|----------|
| **T** `--target-set` | Role catalog | No — one joint catalog |
| **A** `--target-props` | Props for every `style_id` in T | No — one joint attribution |
| **M** `--target-loc` | Source id → `style_id` (and table cell slots) | Yes — one object layer at a time |

T / A / M are **files**, not chat. Mapping belongs only in `merge_loc.py` JSON, never in assistant prose.

## Procedure

Fixed order. Do not apply in the middle, and do not skip a later layer because an earlier layer already looks “good enough”.

1. **Parse source once**
   `python -u scripts/parse_docx.py --docx SOURCE.docx --out inventory.json --preview-out preview.json`
   Writes `preview.ids.manifest.json` and one file per layer. Do not `cat` `preview.ids.json` / `preview.json` / `inventory.json`.
2. **Build T and A** from [Pick a branch](#pick-a-branch) (stylesheet and/or write JSON). Do this **before** mapping.
3. **Map M.** `cat` `preview.ids.manifest.json`. For every `read_order` entry with `n > 0`, `cat` that layer file, classify using the matching `references/m_*.md`, then `merge_loc.py`. Skip a layer only when `n = 0`.
4. **Apply once** after every `n > 0` layer has been merged (including `table.cells` when tables exist and `image` when images exist). Then stop.

```bash
python -u scripts/parse_docx.py --docx SOURCE.docx --out inventory.json --preview-out preview.json

python -u scripts/template_stylesheet.py --template TEMPLATE.docx --out stylesheet.json
# --template only. Never --docx. Never --inventory of the source parse.

python -u scripts/merge_loc.py --loc target_loc.json --layer-loc - <<'EOF'
{"by_layer":{"paragraph.body":{"1":"ParaCluster0","3":"ParaCluster2"}},"table_cells":{}}
EOF

python -u scripts/merge_loc.py --loc target_loc.json --layer-loc - <<'EOF'
{"by_layer":{},"table_cells":{"1":[{"row":1,"col":1,"cell_style":"header"},{"row":2,"col":1,"cell_style":"stub"},{"row":2,"col":2,"cell_style":"data"}]}}
EOF

python -u scripts/apply_format.py \
  --source SOURCE.docx --output OUTPUT.docx \
  --inventory inventory.json \
  --target-set T.json \
  --target-props A.json \
  --target-loc target_loc.json
```

`--target-set` / `--target-props` may both be `stylesheet.json`. `--target-loc` you always build via merge.

Apply silently drops illegal / unknown keys and bad loc refs. Do not read `scripts/lib/*.py`. Do not re-run apply to fix extra keys.

### Layer files

| File | Merge into | Classifier |
|------|------------|------------|
| `preview.ids.section.json` | `by_layer.section` `{id: style_id}` | `references/m_section.md` |
| `preview.ids.paragraph.body.json` | `by_layer["paragraph.body"]` | `references/m_paragraph.md` |
| `preview.ids.table.json` | `by_layer.table` | `references/m_table.md` hop 1 |
| `preview.ids.table.cells.json` | `table_cells` `{table_id: [{row, col, cell_style}, ...]}` | `references/m_table.md` hop 2 |
| `preview.ids.image.json` | `by_layer.image` | `references/m_image.md` |

Leave `by_layer.paragraph.table_cell`, `by_layer.run`, and `paragraph_runs` empty. Cell chrome and fonts come from `table_cells` plus the chosen `Tbl*` (`cells` / `cell_paragraphs`).

**One whole layer per merge** (do not split by id range or table batch). If `cat` of a layer is truncated: merge the ids you saw, omit the rest of **that** layer, then **continue the next `n > 0` layer**. Do not apply early. Do not scrape harness logs. Do not `python -c` over `inventory.json` / cells / runs.

**`skip_if_single`:** if the catalog for that object has **exactly one** role, bind every mapped id on that layer to it. This shortcut is **not** for table hop 2: even with one `Tbl*`, still label every physical cell in `table_cells`.

**Unsure:** omit the id (`other` = leave the key out). Partial loc on a finished layer walk is fine. Partial because you skipped tables/images is not.

### JSON layer-merge

After each layer `cat`, the **next** assistant message must call `merge_loc.py` (not apply). Prefer tool-call only.

```bash
python -u scripts/merge_loc.py --loc target_loc.json --layer-loc - <<'EOF'
{"by_layer":{"section":{"1":"SecCluster0"}},"table_cells":{}}
EOF
```

- Compact JSON is fine. Do not emit TSV. Do not `cat` `target_loc.json` after merge.
- Do not narrate matches, re-list roles/examples, quote preview snippets, or dump the assembled loc in chat.
- Mapping lives **only** in the JSON fragment.

### Apply (once, last)

Required: T file, A file, and a loc that has been merged for **every** `read_order` layer with `n > 0`.

```bash
python -u scripts/apply_format.py \
  --source SOURCE.docx --output OUTPUT.docx \
  --inventory inventory.json \
  --target-set stylesheet.json \
  --target-props stylesheet.json \
  --target-loc target_loc.json
```

- Wait until the process **exits**. First line is `{"status":"applying",...}`; last line is `{"ok": true, ...}`.
- Do **not** background (`&`), `ps`, `nohup`, or pipe through `head`/`tail`.
- Do **not** `rm` the output if apply was interrupted. Re-run apply only if `"ok"` never appeared **and** the file is still the source copy.
- Apply uses officecli batch. Do not call `officecli set` yourself.

## How to read files

`cat` **`stylesheet.roles.json`**, **`preview.ids.manifest.json`**, and **one layer file at a time**. **Never** `cat` `preview.ids.json`, `preview.json`, `inventory.json`, or `stylesheet.json`.

`stylesheet.json` is T+A for apply. `roles.json` has `style_id` / `object` / `n` / `examples` / `captions` / `header_rows` (on `Tbl*`; `typical_sections` is auxiliary). Open `references/ir.md` only if you need the JSON schema.

## Pick a branch

| Files on the table | Branch | T | A |
|--------------------|--------|---|---|
| source + **text only** | [A. text_requirement](#a-text_requirement--text-only) | You write `target_set.json` | You write `target_props.json` |
| source + **template only** | [B. template](#b-template--template-only) | `stylesheet.json` | `stylesheet.json` |
| source + **template + text** | [C. template_w_text](#c-template_w_text--template--text) | `stylesheet.json` | stylesheet, or a sparse patch |

---

## A. `text_requirement` — text only

1. Parse source. Skip stylesheet.
2. `cat` the requirement **once**.
3. One write: `target_set.json` **and** `target_props.json` (joint T + joint A). See `references/t_text.md` and `references/a_text.md`.
4. Then the M walk above. **A is not layered**; only M is.
5. Apply after every `n > 0` layer is merged.

Do not invent a template look.

---

## B. `template` — template only

No LLM for T or A. Stylesheet **is** T + A (includes section header/footer and table `cell_paragraphs`).

1. Parse **source** (`--docx` / default `profile=assign`).
2. `template_stylesheet.py --template TEMPLATE.docx --out stylesheet.json` (also writes `stylesheet.roles.json`).
3. `cat` `stylesheet.roles.json` once. Then the M walk. Do not copy template body text.
4. Apply with the stylesheet for T and A.

A **cluster** is a group of template objects that share the same formatting signature, not a Word gallery name (`Heading 1`). `SecCluster3` is “the 4th distinct section look on the template”. Read `roles.json` `examples` / `n` and bind each source object to the cluster it resembles. **Do not** assign every section, image, or empty paragraph to the cluster with the largest `n`.

---

## C. `template_w_text` — template + text

1. Parse source. Build stylesheet as in B.
2. `cat` `text_input.md` once. Then `cat` `stylesheet.roles.json` and walk M as in B.

**Naming only** (default): the text interprets heading/role names but does not change fonts, sizes, margins, or spacing. Keep template T+A. Write only `target_loc.json`. Apply like branch B. You may fill `display_name` / `description` — **keep `style_id` keys unchanged**.

**Relative patch** when the text names a **delta** (size / font / margin / line spacing / bold / “adjustments relative to the template”), and is not only asking what to call roles: copy stylesheet `target_props` into `target_props.json` and change **only the named keys** in **one** joint patch (`references/c_patch.md`). Apply with `--target-set stylesheet.json --target-props target_props.json --target-loc target_loc.json`.

Do not induce a new catalog from the text.

---

## Mapping (M) — all branches

Classify one layer at a time, each layer only against T entries of that `object`. Open the matching `references/m_*.md` before that merge.

Fill **every** `n > 0` layer in this order, **one whole layer per write**:

1. `section` → `Sec*`
2. `paragraph.body` → `Para*` (skip empty / whitespace-only paragraphs)
3. `table` → `Tbl*` from `preview.ids.table.json` (caption + `header_row`)
4. `table_cells` from `preview.ids.table.cells.json` (every physical cell → that `Tbl*` slot)
5. `image` → `Img*`

Hop 2 is required whenever hop 1 ran. Unlabeled cells keep source formatting — that is a failed table step, not a valid shortcut.

On template branches, style ids are stylesheet ids (`ParaCluster0`, …), not Word gallery names.

---

## Do not

- Rewrite body text or change structure
- Apply before every `n > 0` layer is merged, or treat `section` + body paragraphs as enough
- Narrate the mapping, classify in assistant text, re-list roles/examples, or dump `target_loc.json` in chat
- Use a text-only turn after `cat` of a layer (must `merge_loc` in that next message)
- Split a layer into id ranges, table batches, or extra merge rounds except when that layer’s `cat` was truncated
- Emit TSV instead of a JSON `target_loc` fragment
- `cat` `preview.ids.json`, `preview.json`, `inventory.json`, or `stylesheet.json`
- Open `scripts/lib/*.py` or `references/inject.md`
- `template_stylesheet.py --docx` or `--inventory inventory.json` from the source parse
- On template branches: invent T or A instead of `stylesheet.json`
- `officecli set` / `batch` / python-docx / unzip instead of `apply_format.py`
- Map cells as `by_layer.paragraph.table_cell` or `by_layer.run`
- Treat Word gallery names (`Heading 1`) as `style_id` unless they are ids in T / stylesheet
- Dump every section / image / empty paragraph onto the cluster with the largest `n`
- Pipe apply through `head`, background it, or delete a partially written `OUTPUT.docx`
