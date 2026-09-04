# LongDocFormatter (workflow)

LangGraph pipeline for long-document Word formatting. Inputs: `template`, `requirement`, `source`, `output`. `template` and `requirement` are optional, but at least one is required.

This is the **workflow** implementation. The agent **skill** lives in [`../skills/longdoc-format/`](../skills/longdoc-format/).

## Pipeline

Constrained plan-and-execute. Node order is fixed; `compile_plan.json` only chooses how **T** and **A** are produced:

```text
prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize
```

| Symbol | Stage | Artifacts dir | Main file |
|--------|-------|---------------|-----------|
| Plan | Compile plan | `00_compile_plan/` | `compile_plan.json` |
| — | Prepare | `01_prepare/` | `prepare.json` |
| — | Inventory | `02_inventory/` | `inventory_init.json` |
| **T** | Target extract | `03_target_extract/` | `target_set.json` |
| **A** | Attribute spec | `04_target_spec/` | `target_attributes.json` |
| **M** | Element assignment | `05_element_assign/` | `element_assignment.json` |
| — | Apply | `06_apply/` | `done.json` |
| — | Finalize | `07_finalize/` | `run_summary.json` |

### Recipes (`compile_plan.json`)

| User gives | `target_extract_source` (T) | `attribute_spec_mode` (A) |
|------------|-----------------------------|---------------------------|
| text only | `from_text` | `from_text` |
| template only | `from_template` | `from_template` |
| template + naming text | `from_template_with_text` | `from_template` |
| template + relative edits | `from_template_with_text` | `template_then_patch` |

`inventory`, `element_assign`, and `apply` currently have no plan branches. Batch sizes and enabled layers are in `config/longdoc_config.yaml`.

Apply compiles equivalent officecli commands and runs them in one `open` / `close` session via `officecli batch --best-effort`. Force the old sequential path with `LONGDOC_APPLY_SEQUENTIAL=1`. Chunk size: `LONGDOC_APPLY_BATCH_CHUNK` (default 80).

## Run

From the repository root, after `pip install -e .` and copying `config/llm_config.example.yaml` → `config/llm_config.yaml`:

```bash
python -m LongDocFormatter \
  --template ../examples/template.docx \
  --source ../examples/source.docx \
  --output ../examples/output.docx
```

(Paths above assume cwd is `LongDocFormatter/`. From repo root use `examples/…` as in the top-level README.)

`--model` is a key under `models:` in `llm_config.yaml` (default: yaml `active`). `temperature` / `top_p` / `seed` live in `shared_params`. Each model entry only has `provider`, `model` (API id), `api_key`, `base_url`, `extra_body`.

`config/prompt_config_longdoc/` has `zh` / `en` for every section. `--locale` switches prompts only.

## Layout

- `config/llm_config.yaml` — gitignored registry (`shared_params` + `models.<key>`)
- `config/longdoc_config.yaml` — layers, assignment batch, LLM budget
- `config/prompt_config_longdoc/` — bilingual prompts
- `config/officecli_whitelist.yaml` — writable attribute keys
- `llm/` — OpenAI-compatible and Zhipu clients
- `officecli/` — Word read / write + CLI runner
- `workflow/` — LangGraph T → A → M + compile plan
- `evaluation/` — whitelist accuracy + content integrity

LLM call logs: `artifacts/llm/{step}/{seq}.json`. Token totals: `artifacts/llm/usage.json`. Replay cache: `artifacts/llm/by_hash/` (disable new writes with `llm_cache.save_by_hash: false`).
