# Evaluation

Scores a formatted `output.docx` against `golden.docx`, with `input.docx` as the unchanged baseline.

The headline accuracy is the unweighted mean of four groups: section, paragraph (including runs), table, and image. Run, table-paragraph, and table-cell scores are stored in the detail fields. Text integrity counts tokens that should not have changed (`n_tampered`). A tampered document still receives a format score.

Property keys come from the LongDocFmtEval officecli whitelist.

## Scoring

Batch experiment runners are not in this repository. `evaluation/metrics.py` and `evaluation/integrity.py` are the metric implementations. `evaluation/run_eval.py` scores one sample (`evaluate_sample`).

## Portable scorer

[`longdocform_eval/`](longdocform_eval/) scores agent-run outputs with the same checkpoint rules. It opens one document at a time (`officecli open`, flatten, `close`). Paths are in `longdocform_eval/configs/eval.yaml`.

```text
{result_root}/{agent}/{skill}/{model}/{sample_id}/output.docx
```

`--agent` is `dp-harness` or `codex`. `--skill DocFmt-ToolSkill` reads the `longformatter` result folders, and `--skill DocFmt-Skill` reads the `officecli-orch` result folders. Older folder names (`officecli`, `docxcli`, `longformatter`, `officecli-orch`, `noskills`) still work. `--model` is the result folder name.

```powershell
cd LongDocFmt\evaluation\longdocform_eval
python -m src.run_eval --agent dp-harness --skill longformatter --model deepseek-v4-flash-vision-exp
python -m src.run_eval --agent codex --skill officecli --model deepseek-v4-flash-vision-exp --sample 19
python -m src.run_eval --agent dp-harness --skill docxcli --model deepseek-v4-flash-vision-exp --list
```

An existing `eval/output.flat.json` is reused. `--force` flattens again. Samples with no output docx are recorded as `no_output`.
