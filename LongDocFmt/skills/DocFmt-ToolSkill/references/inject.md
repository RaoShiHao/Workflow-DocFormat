# 如何加载 `DocFmt-ToolSkill`

可移植单元就是本文件夹（里面必须有 `SKILL.md`）。  
`officecli` 要在 PATH 上；脚本用当前 Python **标准库**（无 PyPI 包，`requirements.txt` 为空）。只依赖本目录 `scripts/lib/`（读引擎、OOXML、apply 都已打进 skill，**不需要** LongDocFmt 的 PYTHONPATH）。

**不要**把 `LongDocFmt/` 整棵丢进 skills 根目录。DeepSeek Harness **不会递归**查找 `**/SKILL.md`，只认：

```text
<skills-root>/DocFmt-ToolSkill/SKILL.md
```

多一层 `skills/LongDocFmt/skills/DocFmt-ToolSkill/` 会扫不到。

先确认本机：

```powershell
officecli --version
python --version   # 3.11+
```

---

## Codex

官方扫描位置（[Codex skills](https://developers.openai.com/codex/skills)）：

| 范围 | 路径（Windows） |
|------|-----------------|
| 本仓库（推荐） | 仓库根 `\.agents\skills\DocFmt-ToolSkill\` |
| 当前用户 | `%USERPROFILE%\.agents\skills\DocFmt-ToolSkill\` |
| 旧路径（仍可用） | `%USERPROFILE%\.codex\skills\DocFmt-ToolSkill\` |

在仓库根目录下执行：

```powershell
$dst = ".\.agents\skills\DocFmt-ToolSkill"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force `
  ".\LongDocFmt\skills\DocFmt-ToolSkill\*" `
  $dst
```

全局（所有项目）：

```powershell
$dst = Join-Path $env:USERPROFILE ".agents\skills\DocFmt-ToolSkill"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force `
  ".\LongDocFmt\skills\DocFmt-ToolSkill\*" `
  $dst
```

重启 Codex（CLI / IDE 插件）。若列表里没有，检查目录名是否正好是 `DocFmt-ToolSkill`，且其中有 `SKILL.md`。

可选：在 `%USERPROFILE%\.codex\config.toml` 里用绝对路径指向 `SKILL.md`（一般拷目录即可，不必配）。

使用：在 Codex 里打开含 source 的工作区，直接说例如：

> 用 DocFmt-ToolSkill skill：按 template.docx 和 text_input.md 调整 source.docx 的格式，正文不要改，输出到 out.docx。

Codex 会先只看到 `name`+`description`，决定启用后再读完整 `SKILL.md`，然后跑 `scripts/*.py`。

---

## DeepSeek Harness

本地 provider 默认根目录（只扫一层，[文档](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/skills)）：

| 优先级 | 路径 |
|--------|------|
| 项目 | `<git根>/.dsh/skills/` |
| 项目（与 Codex 共用） | `<git根>/.agents/skills/` |
| 用户 | `%USERPROFILE%\.dsh\skills\` |
| 用户（共用） | `%USERPROFILE%\.agents\skills\` |

推荐与 Codex 共用仓库目录：拷到 `.agents/skills/DocFmt-ToolSkill/`（同上一段 Copy-Item）。  
只给 DSH、不进 Codex 时用 `.dsh/skills/DocFmt-ToolSkill/`。

额外目录可在 DSH 配置里写（字段名以你本机 harness 为准）：

```yaml
skills:
  filesystem:
    customSkillDirs:
      - ./LongDocFmt/skills
```

注意：`customSkillDirs` 的每一项必须是 **skills 根**（其下直接是 `DocFmt-ToolSkill/` 和 `DocFmt-Skill/`），不能是 skill 目录自己。  
本提交里这个根是 `LongDocFmt/skills/`，其下能看到 `DocFmt-ToolSkill/SKILL.md`。

加载后模型侧通常有 skill 工具，参数为 kebab-case 名：`DocFmt-ToolSkill`。对话里同样说明要排版的三个文件即可；也可以显式说「加载 DocFmt-ToolSkill skill」。

---

## 用起来时 agent 实际会做什么

先看有没有 `template.docx` / 需求文本，再走 `SKILL.md` 对应分支（与 LongDocFmt 的 `text_requirement` / `template` / `template_w_text` 一致）：

1. 解析 **source**：`python scripts/parse_docx.py --docx ... --out inventory.json --preview-out preview.json`（写出 `preview.ids.manifest.json` 和一层一份文件；不要 `cat preview.ids.json`）  
2. **仅有文本：** 跳过 stylesheet；`cat` 需求后 **一次联合**写 T+A（全部 object 的 attribution，不要按层打 A），再按 manifest `read_order` **整层**写入 `target_loc.json`；apply 三个 JSON。  
3. **仅有模板：** `python scripts/template_stylesheet.py --template ... --out stylesheet.json`（同时写出 `stylesheet.roles.json`）。模型只 `cat stylesheet.roles.json` + 各层文件，用 `merge_loc.py --layer-loc`（一层一份 `target_loc` JSON 片段）写入 `target_loc.json`。apply 时 `--target-set` 与 `--target-props` 都指向 `stylesheet.json`。  
4. **模板+文本：** 同上拿 stylesheet；文本默认只帮助命名/对标题；若文本是相对模板改格式，再 **一次联合** sparse patch 改点名的键。然后按层写 M 并 apply。  

读 **`preview.ids.manifest.json` / 各层文件 / `stylesheet.roles.json`** / 需求文本，不要 `cat` 完整 `preview.ids.json` / `preview.json` / `inventory.json`，也不要用 `python -c` 扫 inventory。一层一份（`section` / `paragraph.body` / `table` / `table.cells` / `image`）只用于 **M**，不要按 80 条或 2 张表再切。层文件被截断时不要发明分批，按已写完的层 apply 或停。

脚本路径相对 **skill 目录**（拷贝后的 `DocFmt-ToolSkill/`）。Agent 应用该目录下的 `scripts/`，不要用仓库里另一份旧路径。

---

## 常见扫不到

- 多套了一层目录（`skills/LongDocFmt/skills/DocFmt-ToolSkill/`）  
- 文件夹名不是 `DocFmt-ToolSkill`，或与 `SKILL.md` 里 `name:` 不一致  
- 没重启 Codex / DSH  
- 没有 `officecli`，脚本能被调用但立刻失败
