# Workflow-DocFormat

在已有 Word `.docx` 上做格式调整：根据**版式模板**和/或**文本排版要求**改格式，**不改正文、不改结构**。

本仓库提供同一方法的 **两个实现**：

| 版本 | 路径 | 谁来跑 |
|------|------|--------|
| **Workflow** | [`LongDocFormatter/`](LongDocFormatter/) | LangGraph 流水线，自己调 LLM |
| **Skill** | [`skills/longdoc-format/`](skills/longdoc-format/) | Cursor / Codex / Claude / DeepSeek Harness：模型写 IR JSON，脚本读写 docx |

中间表示一致：

```text
T  target_set     格式角色（一级标题 / 正文 / 表题 / …）
A  target_props   每个角色的白名单属性（字体、字号、对齐、表框……）
M  target_loc     location_id → style_id
apply             把 T+A+M 写到 source 的副本 → output.docx
```

需要 **Python 3.11+**，以及 PATH 上的 **[officecli](https://officecli.ai)**。

---

## 安装

```bash
# officecli（必须）
# Windows PowerShell:
irm https://d.officecli.ai/install.ps1 | iex
# macOS / Linux:
curl -fsSL https://d.officecli.ai/install.sh | bash

cd Workflow-DocFormat
python -m pip install -e .
cp LongDocFormatter/config/llm_config.example.yaml LongDocFormatter/config/llm_config.yaml
# 编辑 llm_config.yaml，给 active 模型填入 API Key
```

Skill 版本 **不需要 pip 包**，只要 Python 3.11+ 和 `officecli`。

---

## Workflow 版本（LangGraph）

四个输入。`template` 与 `requirement` 可选，但 **至少提供一个**。

```bash
# 模板 + 源文档
python -m LongDocFormatter \
  --template examples/template.docx \
  --source examples/source.docx \
  --output examples/output.docx

# 仅文本要求
python -m LongDocFormatter \
  --requirement examples/requirement.zh.md \
  --source examples/source.docx \
  --output examples/output.docx
```

可选：`--artifacts-dir`、`--locale en|zh`、`--model <registry-key>`、`--llm-config`、`--config`。

固定节点顺序：

```text
prepare → compile_plan → inventory → target_extract → target_spec → element_assign → apply → finalize
```

`compile_plan.json` 只决定 T / A **怎么生成**，不改变步骤顺序。详见 [`LongDocFormatter/README.md`](LongDocFormatter/README.md)。

---

## Skill 版本（Agent）

把 `skills/longdoc-format/` **整目录**拷到 agent 扫描的 skills 根下，不要再套一层文件夹。

```powershell
$dst = Join-Path (Get-Location) ".agents\skills\longdoc-format"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force "skills\longdoc-format\*" $dst
```

Cursor：拷到 `.cursor/skills/longdoc-format/`（项目）或 `~/.cursor/skills/longdoc-format/`（用户）。

然后对 agent 说例如：

> 用 longdoc-format：按 examples/template.docx 调整 examples/source.docx 的格式，正文不要改，输出 examples/output.docx。

加载说明见 [`skills/longdoc-format/references/inject.md`](skills/longdoc-format/references/inject.md)。

---

## 本仓库不含

- API Key（`llm_config.yaml` 已 gitignore，从 example 复制）
- 私有评测数据集与批量实验脚本
- 仅用于内部消融的第三方 baseline skill

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
