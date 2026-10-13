# Dataset examples

These three folders are examples only, one for each input mode. They are not the full dataset.

Every case has the same job: keep the body of `input.docx` and produce the formatting in `golden.docx`. The folders differ in where that formatting is specified.

| Folder | Input mode | Files |
|--------|------------|-------|
| [`text-based/`](text-based/) | Text-based | `input.docx`, `text_input.md`, `golden.docx` |
| [`reference-document-based/`](reference-document-based/) | Reference-document-based | `input.docx`, `template.docx`, `golden.docx` |
| [`hybrid/`](hybrid/) | Hybrid | `input.docx`, `template.docx`, `text_input.md`, `golden.docx` |

Build caches, `sample_info.json`, and the rest of the export are not included. `sample_info.json` records local build paths.

## Files

| File | Role |
|------|------|
| `input.docx` | Source document. The formatter rewrites its formatting and keeps the body text. |
| `text_input.md` | Written formatting requirement. |
| `template.docx` | Reference document that already shows the target formatting. |
| `golden.docx` | Correctly formatted document. Scoring compares a run's `output.docx` with this file. |

## text-based

The requirement is only `text_input.md`. There is no reference document.

`text_input.md` names the sections, headings, body paragraphs, tables, and images, and it states concrete settings (margins, page numbers, header and footer text, fonts, alignment). The formatter reads that file and applies it to `input.docx`. The expected result is `golden.docx`.

## reference-document-based

The requirement is only `template.docx`. There is no `text_input.md`.

`template.docx` is a styled specimen of the same genre. The formatter reads its formatting and applies the matching styles to `input.docx`. The expected result is `golden.docx`.

## hybrid

Both inputs are present. `template.docx` carries the formatting, and `text_input.md` names the targets in that template (sections, heading levels, body roles, tables, images) so they can be matched to `input.docx`. The expected result is `golden.docx`.
