---
name: DocFmt-Skill
description: >
  Orchestrate in-place Word .docx restyling from a reference template and/or
  text style requirements. Use when the task is 格式调整, apply a template look,
  or restyle a long report while keeping body text. Not for writing new content,
  mail-merge, or .xlsx/.pptx.
compatibility: Task contract only. Does not specify a document CLI or property schema.
metadata:
  author: LongDocFmt
  version: "0.8"
---

# Long-document restyle orchestration

This skill is **the restyle job graph**: short inspect → write a layer plan →
**apply that layer to the output file immediately** → next present layer →
stop. It does **not** name a CLI, flag, or property schema. How you mutate
the `.docx` is chosen by the run (another skill, or your own code).

This skill decides *what to do, in what order, and when you are done*.

## Goal

Keep **body text and structure**. Only change formatting.

- Do not rewrite sentences or copy template body text.
- Do not create a new document from scratch.
- When unsure about **one object**, leave its source formatting.
- **A partial applied document beats a perfect plan that never mutates the file.**
  An unchanged output (copy of input only) is a **hard failure**.
  Skipping an **entire** present layer (all tables, all images, all
  sections) after a working body apply is also a failure — not “partial.”

**Write the mapping, do not narrate it.** Layer assignments go into a local
plan artifact (for example `plan.json`), not into a long chat classification.
After inspect, write-plan → **apply that layer in the same turn or the very
next tool call**.

## Hard constraints (non-negotiable)

These override softer guidance below when they conflict.

1. **First real apply deadline.** After copy + the short inspect budget
   below, the **next mutating action must apply formatting to body
   paragraphs** on the output file. Do **not** spend further steps on more
   inspect, help, or classification-only work before the first body-paragraph
   write succeeds.
2. **Plan without apply is forbidden.** Writing or rewriting the plan counts
   as useful **only if** the following tool call mutates the output for that
   layer. Never polish a plan across multiple steps without applying.
3. **Must-edit layers.** Before you stop, attempt an apply for **every layer
   that inspect showed exists**:
   - **body paragraphs** (headings + body styles) — always; apply this first
   - **tables** if any: hop 1 (whole-table role) **and** hop 2 (cell
     `header` / `stub` / `data`). Hop 2 is required even if there is only
     one table role
   - **sections** if the document has section/page setup to restyle
     (margins / title page / header / footer / page numbers)
   - **images** if any pictures exist
   Skipping an unclear **object** is fine. Skipping a whole present layer
   because body already applied is not.
4. **Stopping rule.** You may stop only after every present layer in (3)
   has been attempted. A successful body-paragraph apply is **necessary,
   not sufficient**. If tables/images/sections exist and you have not
   applied them yet, you are **not done**.

## Branch (look at `inputs/`)

| Files present | Branch |
|---------------|--------|
| `input.docx` + `text_input.md` only | A. text_requirement |
| `input.docx` + `template.docx` only | B. template |
| all three | C. template_w_text |

`ls` first. Do not assume a file exists because the path looks familiar.

## Sequence (do this, nothing else)

Each apply mutates the file, so **apply body first** (largest score mass if
the step budget dies). Then continue; do not stop.

1. Copy `inputs/input.docx` to the output path. Do not edit `inputs/`.
2. **Short inspect (hard cap = at most 6 read tool calls total).** Then
   write a plan and **apply body**. Allowed reads:
   - Output: structure/stats (outline of headings, tables, images, sections)
     and at most **one** extra probe for styles or sections if that is not
     enough.
   - Template (B/C): the same kind of probe on `inputs/template.docx`
     (roles / named styles — not template body text).
   - Text (A/C): `cat` `inputs/text_input.md` **once**.
   - **Stop inspect when you know which layers exist** (body, tables,
     sections, images) **and can name heading/body roles.** Do not page
     through every chapter, dump the whole document, or walk every
     header/footer before the first body apply.
3. Classify **by layer**, then **apply that layer immediately**. Order
   (body first = executor adaptation; completeness still required):
   1. body paragraphs — **apply this first**
   2. tables if any — hop 1 (caption + header row → table role), then hop 2
      (every physical cell → `header` / `stub` / `data`)
   3. sections if any — margins / header / footer / title page / page
      numbers. **Never** block the first body/table apply on header/footer
      debugging; **do** come back and apply sections before stop
   4. images if any
4. Confirm the output file is a valid `.docx`, then stop. One short sentence.

Write a local plan artifact and apply from it **in the next tool call**.
Do **not** narrate the full mapping in chat — the plan is the mapping.

## How to classify

A **role** is a formatting signature (one heading look, one body look, one
table-header look), not a Word gallery name (`Heading 1`) unless that name is
what the requirement or template actually uses.

- Skip empty / whitespace-only paragraphs.
- Do not assign every section, image, or empty paragraph to the most common look.
- If a layer has **one** role, bind every object on that layer to it
  (`skip_if_single`). This shortcut is **not** for table hop 2: even with one
  table role, still label cells.
- **Coarse is enough.** Prefer ~5–15 roles and broad selectors / existing
  style names over per-paragraph perfection. You can refine with a second
  apply only after the first body apply has succeeded — then still continue
  to later layers.
- **Sections:** cover / TOC / body differ when margins, page numbers,
  title page, or header/footer differ. If a section object is unclear after
  the inspect cap, omit that section — still apply the section layer for the
  ones you can name.
- **Body paragraphs:** map from outline / existing styles to template (or
  requirement) roles: cover title, chapter/section/subsection headings,
  body, caption, TOC entry, list, note, etc. Prefer **named styles already
  present** on the output or template.
- **Tables, two hops:**
  1. Pick a table role from caption + header row. Do not use default init
     borders as evidence.
  2. Label cells `header` / `stub` / `data` (plus label/value only if named).
     `header` = column titles (often row 1, not always); `stub` = row titles
     (often col 1, not always). Do not assume row 1 is header. Unlabeled
     **cells** keep source formatting; skipping hop 2 entirely does not.
- **Images:** if several roles exist, match size/nearby text roughly; if one
  role, bind all.

Prefer reassigning paragraphs/cells to **existing named styles** over
per-run inline overrides.

## Branch rules

### A. text_requirement

Derive a small role catalog from the requirement. Do **not** invent a
template look the text did not ask for. Apply each present layer.

### B. template

Migrate the template's named styles / looks onto the source objects. Do not
copy template body text. Do not invent a new catalog.

### C. template_w_text

Template wins for fonts/spacing/look. Text is for heading/numbering
interpretation or a **named delta** (size / font / margin / line spacing /
bold) only. Do not induce a new catalog from the text.

## Do not

- Rewrite body text or change document structure
- Follow a create-a-new-document / cover-fill / TOC / HTML-preview delivery
  workflow from any other skill
- Narrate layer classifications in chat instead of writing a plan
- Rewrite the plan repeatedly without a successful body apply
- Spend the run on inspect / debugging without a successful body-paragraph apply
- Stop after body only while tables, sections, or images that inspect showed
  still have no apply
- Skip table hop 2 because hop 1 succeeded or there is only one table role
- Keep polishing after the output is valid and every present layer was attempted
- Stop / claim done while the output is still an unmodified copy of input
- Add content, placeholders, or a new last-page "thank you"
