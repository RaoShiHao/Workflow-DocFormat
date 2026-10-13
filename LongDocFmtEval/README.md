# LongDocFmtEval

This package is the dataset-construction code. It builds one genre into a role inventory, three writing tasks (C1, C2, C3), a reference document, a full document, and a formatting task.

The tree is `pipeline_auto` plus the modules that pipeline imports: [`common/`](common/) (LLM, Excel, officecli, figures) and [`llm_client/`](llm_client/) (OpenAI-compatible and Zhipu clients).

## Paper steps and code steps

The paper has six paragraphs. Topic Collection is manual and is not a code step. The other five paragraphs are steps 1–7. Document Writing Planning and Document Generation are each split across more than one code step.

| Paper | Code |
|-------|------|
| Topic Collection | No step. The 108 topics and their writing guidance are the curated table `pipeline_auto/config/genres.xlsx`. The pipeline starts from that file. |
| Semantic Role Construction | **Step 1.** The LLM builds the role inventory for sections, paragraphs, tables, and figures. |
| Writing Task Generation | **Step 2.** Three tasks, C1, C2, C3, with increasing outline depth and role budget. |
| Document Writing Planning | **Step 3**, and the short reference plan in **step 5**. Step 3 writes the outline, selects the roles, and plans the full document (text, media, chapter blocks). The reference plan is the short specimen written in step 5, beside the reference document. |
| Document Generation | **Steps 4–6.** Step 4 assigns formatting properties and patches roles until their signatures differ. Step 5 renders the reference document `template.docx`. Step 6 writes and renders the full document `document.docx`. Both use the step 4 stylesheet. Human review of coherence is outside this code. |
| Requirement Diversification | **Step 7.** The full document is the target. A default-format copy is the source. Each task is assigned one of text-based, reference-document-based, or hybrid. |

## Setup

From the repository root (or any working directory, once `PYTHONPATH` points at that root):

```powershell
cd D:\path\to\Workflow-DocFormat
pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Get-Location).Path
officecli --version   # expect 1.0.144
```

`requirements.txt` also installs the formatter. `LongDocFmtEval/requirements.txt` is the dataset subset.

Copy the LLM example and fill `api_key` / `base_url`. Step 1 reads the role-library file; image calls read the seed file. The three files can have the same contents.

```powershell
cd LongDocFmtEval\pipeline_auto\config
copy llm_config.example.yaml llm_config.yaml
copy llm_config.example.yaml role_library_llm_config.yaml
copy llm_config.example.yaml seed_llm_config.yaml
```

## What each step writes

Outputs go to `pipeline_auto/datasets/{id}_{lang}_{topic_name}/`.

| Step | Paper | What it writes |
|------|-------|----------------|
| 1 | Semantic Role Construction | Role inventory, `step1_role/` |
| 2 | Writing Task Generation | C1/C2/C3 tasks, `step2_themes/` |
| 3 | Document Writing Planning | Outline, selected roles, full-document plan, `C*/step3_outlines/` |
| 4 | Document Generation | Formatting properties, `C*/step4_styles/` |
| 5 | Document Generation | Reference plan and `template.docx`, `C*/step5_template/` |
| 6 | Document Generation | Full `document.docx`, `C*/step6_document/` |
| 7 | Requirement Diversification | Source, target, and one requirement type, `C*/step7_pairs/` |

The default run stops at step 6. Pass `--until-step 7` to build pairs. Factor rows already live in `config/datasets_factors.json` (seed 1234). Regenerate them only with `scripts/assign_training_factors.py --force`.

Prompts: Chinese in `config/prompt_config/`, English in `config/prompt_config_en/`. The locale on the genre row selects the folder. Unused legacy prompts are not included.

## Run

```powershell
# One genre, all three tiers, through document.docx
python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --id 1 --overwrite

# One tier
python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --id 1 --complexity C2 --overwrite

# Stop after a step (1–7)
python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --id 1 --complexity C2 --until-step 5

# Several genres
python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --ids 1,2 --complexity C1 --until-step 6

# Pairs only (step 6 document must already exist)
python LongDocFmtEval/pipeline_auto/scripts/run_step7.py --id 1 --overwrite

# Overlapped scheduler: LLM and officecli on separate queues
python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_overlapped.py --id 1 --overwrite
```

Single steps also have `scripts/run_step1.py` through `scripts/run_step7.py`. Re-render without another LLM call:

```powershell
python LongDocFmtEval/pipeline_auto/scripts/rerender_docx_only.py --ids 1,12
```

Export finished step 7 folders into the flat bench the formatter reads. Order follows `datasets_factors.json`: `export_id = (topic_id - 1) * 3 + {C1:1, C2:2, C3:3}`.

```powershell
python LongDocFmtEval/pipeline_auto/scripts/build_bench/export_longdocform.py --all --out D:\LongDocFmtEval
```

`config/eval_export_keys.txt` lists the representative subset. `config/ids.txt` is an id list for batch scripts.

## Config that is included

| File | Use |
|------|-----|
| `genres.xlsx` | Curated topic list. Required input for steps 1–7. |
| `datasets_factors.json` | Frozen step 7 assignment. |
| `training_factor_pools.yaml` | Factor pools. |
| `heading_schemes.yaml` | Heading numbering schemes. |
| `template_perturb_pools.yaml` | Template perturbation pools. |
| `officecli_whitelist.yaml` / `_en.yaml` | Allowed officecli properties. |
| `officecli_prop_labels.yaml` / `_en.yaml` | Property labels in prompts. |
| `ids.txt`, `eval_export_keys.txt` | Batch and export id lists. |
| `llm_config.example.yaml` | Empty-key template. Not a live credential file. |
