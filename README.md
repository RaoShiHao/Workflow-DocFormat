# Workflow-DocFormat

Long-document Word formatting. One tree builds the dataset. The other formats an existing `.docx` from a template, a text requirement, or both. Body text stays; formatting changes.

| Path | What it is |
|------|------------|
| [`LongDocFmtEval/`](LongDocFmtEval/README.md) | Dataset construction, steps 1–7 |
| [`LongDocFmt/`](LongDocFmt/README.md) | Formatter. Pipeline: prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize |
| [`LongDocFmt/skills/`](LongDocFmt/skills/README.md) | `DocFmt-Skill` (text) and `DocFmt-ToolSkill` (officecli scripts) |
| [`dataset/`](dataset/README.md) | Three English examples, one per input mode. Not the full dataset. |

Batch experiment runners and ablations are not in this repository.

Python **3.11+**. External binary: **[officecli](https://officecli.ai)** — **1.0.144** for dataset rendering, **1.0.145** for the formatter. [中文说明](README.zh.md).

## Install

```powershell
# officecli
irm https://d.officecli.ai/install.ps1 | iex

cd Workflow-DocFormat
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Get-Location).Path
copy LongDocFmt\config\llm_config.example.yaml LongDocFmt\config\llm_config.yaml
```

Fill `api_key` in `llm_config.yaml`. Dataset synthesis also needs three copies under `LongDocFmtEval/pipeline_auto/config/`; see that README.

The formatter imports `LongDocFmtEval.common.officecli_runner`, so `PYTHONPATH` must be this repository root.

## Run one document

`template` and `requirement` cannot both be empty.

```powershell
python -m LongDocFmt --template file\template.docx --source file\source.docx --output workplace_out.docx

python -m LongDocFmt --requirement path\to\requirement.txt --source file\source.docx --output workplace_out.docx
```

## License

MIT. See [LICENSE](LICENSE).
