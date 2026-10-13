# Workflow-DocFormat

长文档 Word 排版。一棵目录负责构建数据集，另一棵在已有 `.docx` 上按模板、文字要求或两者一起改格式。正文保留，只改格式。

| 路径 | 内容 |
|------|------|
| [`LongDocFmtEval/`](LongDocFmtEval/README.md) | 数据集构建，step 1–7 |
| [`LongDocFmt/`](LongDocFmt/README.md) | 排版方法。流程：prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize |
| [`LongDocFmt/skills/`](LongDocFmt/skills/README.md) | `DocFmt-Skill`（文字流程）和 `DocFmt-ToolSkill`（officecli 脚本） |
| [`dataset/`](dataset/README.md) | 三个英文示例，每种输入模态一个。不是完整数据集。 |

批量实验和消融代码不在本仓库。

需要 **Python 3.11+**，以及 PATH 上的 **[officecli](https://officecli.ai)**：数据集渲染用 **1.0.144**，排版用 **1.0.145**。英文说明见 [README.md](README.md)。

## 安装

```powershell
irm https://d.officecli.ai/install.ps1 | iex

cd Workflow-DocFormat
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Get-Location).Path
copy LongDocFmt\config\llm_config.example.yaml LongDocFmt\config\llm_config.yaml
```

在 `llm_config.yaml` 里填写 `api_key`。数据集合成还要在 `LongDocFmtEval/pipeline_auto/config/` 放三份配置，见该目录的 README。

排版代码会导入 `LongDocFmtEval.common.officecli_runner`，所以 `PYTHONPATH` 必须是本仓库根目录。

## 跑一篇文档

`template` 和 `requirement` 不能同时为空。

```powershell
python -m LongDocFmt --template file\template.docx --source file\source.docx --output workplace_out.docx

python -m LongDocFmt --requirement path\to\requirement.txt --source file\source.docx --output workplace_out.docx
```

## 许可证

MIT。见 [LICENSE](LICENSE)。
