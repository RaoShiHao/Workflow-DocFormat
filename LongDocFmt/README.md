# LongDocFmt

Formats an existing Word document. It keeps the body text and writes formatting from a template, a text requirement, or both.

The fixed pipeline is:

```text
prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize
```

| Stage | Meaning | Main artifact |
|-------|---------|----------------|
| Plan | Choose the recipe from what inputs exist | `00_compile_plan/compile_plan.json` |
| T | Formatting targets (roles / styles) | `03_target_extract/target_set.json` |
| A | Attributes for those targets | `04_target_attribute_spec/target_props.json` |
| M | Which source elements receive which target | `05_target_element_loc/target_loc.json` |
| Apply | officecli writes the document | `output.docx` |

`template` and `requirement` cannot both be empty. `source` and `output` are required.

## Layout

| Path | README |
|------|--------|
| `workflow/`, `llm/`, `officecli/`, `config/` | This file. The main method. |
| [`skills/DocFmt-Skill`](skills/README.md) | Text skill: the restyle procedure, no document CLI |
| [`skills/DocFmt-ToolSkill`](skills/README.md) | Tool skill: the same job with officecli scripts |
| [`evaluation/`](evaluation/README.md) | Scoring |

Batch experiment runners and ablations are not in this repository.

## Setup

From the repository root:

```powershell
pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Get-Location).Path
officecli --version   # 1.0.145 for formatter runs and evaluation
```

That requirements file also installs LongDocFmtEval. The formatter imports `LongDocFmtEval.common.officecli_runner`.

```powershell
copy LongDocFmt\config\llm_config.example.yaml LongDocFmt\config\llm_config.yaml
```

Edit `llm_config.yaml` and set `api_key` on each model. `shared_params` holds temperature, top_p, seed, and max_tokens. Each `models.<key>` holds provider, model id, api_key, base_url, and extra_body.

`config/longdoc_config.yaml` holds batch sizes and layer switches. Prompts are bilingual files in `config/prompt_config_longdoc/`. `--locale en|zh` selects the prompt language. The default is `en`.

## Run one document

From the repository root:

```powershell
python -m LongDocFmt --template file\template.docx --source file\source.docx --output workplace_out.docx

python -m LongDocFmt --requirement path\to\requirement.txt --source file\source.docx --output workplace_out.docx
```

Optional flags: `--artifacts-dir`, `--locale en|zh`, `--model <registry-key>`, `--llm-config`, `--config`.

Artifacts land under the artifacts directory, one folder per stage (`00_compile_plan` through `07_finalize`). LLM traces are `artifacts/llm/`.
