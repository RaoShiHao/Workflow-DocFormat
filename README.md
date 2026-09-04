# Workflow-DocFormat

Format an existing Word `.docx` in place from a **template document** and/or **text style requirements**. Body text and structure stay; only formatting attributes change.

This repository ships **two implementations of the same method**:

| Version | Path | Who runs it |
|---------|------|-------------|
| **Workflow** | [`LongDocFormatter/`](LongDocFormatter/) | A LangGraph pipeline that calls an LLM itself |
| **Skill** | [`skills/longdoc-format/`](skills/longdoc-format/) | Cursor / Codex / Claude / DeepSeek Harness — the agent writes IR JSON; scripts read/write the docx |

Both share the same intermediate representation:

```text
T  target_set     formatting roles (Heading 1, body, table title, …)
A  target_props   whitelist attributes per role (font, size, alignment, borders, …)
M  target_loc     location_id → style_id
apply             write T+A+M onto a copy of source → output.docx
```

Python **3.11+**. External binary: **[officecli](https://officecli.ai)** on `PATH`.

[中文说明](README.zh.md)

---

## Install

```bash
# officecli (required)
# Windows PowerShell:
irm https://d.officecli.ai/install.ps1 | iex
# macOS / Linux:
curl -fsSL https://d.officecli.ai/install.sh | bash

cd Workflow-DocFormat
python -m pip install -e .
cp LongDocFormatter/config/llm_config.example.yaml LongDocFormatter/config/llm_config.yaml
# edit llm_config.yaml and put your API key on the active model
```

The Skill version needs **no pip packages** — only Python 3.11+ and `officecli`.

---

## Workflow version (LangGraph)

Four inputs. `template` and `requirement` are optional, but **at least one** is required.

| Field | Required | Meaning |
|-------|----------|---------|
| `source` | yes | Unformatted `.docx` |
| `output` | yes | Output `.docx` |
| `template` | no | Layout template `.docx` |
| `requirement` | no | Style rules as text, or a `.txt` / `.md` path |

```bash
# template + source
python -m LongDocFormatter \
  --template examples/template.docx \
  --source examples/source.docx \
  --output examples/output.docx

# text requirement only
python -m LongDocFormatter \
  --requirement examples/requirement.zh.md \
  --source examples/source.docx \
  --output examples/output.docx
```

Optional flags: `--artifacts-dir`, `--locale en|zh`, `--model <registry-key>`, `--llm-config`, `--config`.

Pipeline (node order is fixed; `compile_plan.json` only chooses **how** T and A are built):

```text
prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize
```

Typical recipes:

| User provides | T | A | Model writes |
|---------------|---|---|--------------|
| text only | LLM from text | LLM from text | T + A, then M |
| template only | cluster template | exemplars from template | **M only** |
| template + naming text | template (+ text names) | exemplars | M only |
| template + “also change …” | template | exemplars + text overlay | patched A, then M |

Details: [`LongDocFormatter/README.md`](LongDocFormatter/README.md).

---

## Skill version (agent)

Copy `skills/longdoc-format/` into the skills root your agent scans. Do not wrap it in an extra folder.

```powershell
# Codex / DeepSeek Harness (repo-local)
$dst = Join-Path (Get-Location) ".agents\skills\longdoc-format"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force "skills\longdoc-format\*" $dst
```

Cursor: copy to `.cursor/skills/longdoc-format/` (project) or `~/.cursor/skills/longdoc-format/` (user).

Then ask the agent, for example:

> Use longdoc-format: restyle `examples/source.docx` from `examples/template.docx`. Keep body text. Write `examples/output.docx`.

The agent runs `parse_docx.py` / `template_stylesheet.py`, writes IR JSON, then `apply_format.py`. It does not call `officecli set` itself.

See [`skills/README.md`](skills/README.md) and [`skills/longdoc-format/references/inject.md`](skills/longdoc-format/references/inject.md).

---

## Layout

```text
Workflow-DocFormat/
├── LongDocFormatter/          # workflow (Python package)
│   ├── config/                # prompts, longdoc_config, whitelist, llm_config.example.yaml
│   ├── llm/                   # OpenAI-compatible + Zhipu clients
│   ├── officecli/             # docx read / write helpers + CLI runner
│   ├── workflow/              # LangGraph: T → A → M → apply
│   └── evaluation/            # whitelist accuracy + content integrity
├── skills/longdoc-format/     # agent skill (stdlib + officecli only)
└── examples/                  # small sample documents
```

---

## What is not in this repo

- API keys (`llm_config.yaml` is gitignored; copy the example)
- Private evaluation datasets and batch experiment harnesses
- Third-party baseline skills used only in internal ablations

---

## License

MIT. See [LICENSE](LICENSE).
