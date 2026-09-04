# 把 LongDocFormatter 做成 Skill：给 agent 什么、脚本做什么

> 可运行、可拷贝的 skill：[`longdoc-format/`](./longdoc-format/)。  
> 脚本只依赖 `scripts/lib/` + 本机 `officecli`，不 import 仓库其它包。

---

## 1. Skill 实际是什么

Agent 默认很会写 JSON、也会调 CLI。它 **不知道** 的是你们这套任务的约定。Skill 只补这些约定。

| 文件 | 作用 | 自由度 |
|------|------|--------|
| `SKILL.md` | 何时启用、任务框架、T/A/M 心智模型、调用脚本的顺序、禁止事项 | 中：agent 自己填 T/A/M |
| `scripts/` | 读文档、校验 JSON、从模板抄属性、合并 patch、写回 docx | 低：这些步骤脆弱，不要让模型即兴写 python-docx |
| `reference/` | 完整字段表、白名单、示例 JSON；SKILL 里只链过去 | agent 需要时再读 |

Cursor / Claude / Codex 的加载方式都一样：先读 `description` 决定用不用；再用 `SKILL.md`；按需打开 reference；按文档里的命令跑脚本。所以 **核心信息必须能写进 SKILL.md 的前两屏**，不要把流水线论文贴进去。

---

## 2. 核心应该提供给 agent 的，只有四块

其它都是实现细节（LangGraph、prompt yaml、officecli 命令、whitelist 全表）。那些放进脚本或 reference。

### （1）任务框：输入、输出、不变式

Agent 会默认去「生成一篇新文档」或「用 officecli 一段段 set」。必须先钉死：

- 输入：`source.docx`（待调）+ 可选 `template.docx` + 可选需求文本。至少模板或文本之一。  
- 输出：排版后的 `source` 副本，不是新写一篇。  
- **正文、段落顺序、表/图结构不准改。** 只改格式属性。  
- 不要用 officecli / python-docx 绕过下面的 apply 脚本。

没有这一块，后面的 T/A/M 都会被理解成「写作助手」。

### （2）心智模型：先编译三个 IR，再一次写入

这是「方法」真正要教给模型的东西。不是 node 名字，是三个对象和它们的依赖：

```text
T  target_set     这篇文档有哪些格式角色（一级标题 / 正文 / 表题 / …）
A  target_props   每个角色的属性字典（字号、对齐、表框……只允许白名单键）
M  target_loc     每个元素是哪个角色（location_id → style_id）

apply             脚本根据 T+A+M 写 source → output。Agent 不直接改 docx。
```

必须写清依赖：apply 需要三份文件（T+A+M）。有模板时 T 和 A 由 `template_stylesheet.py` 一次写出，模型不必再编。没有模板时 T 和 A 由模型同一轮从文本写出。

Agent 一旦接受「先看输入再选分支」，就不会去发明「先改三号字再看模板」的自由计划。这比罗列 tool 名称更重要。

### （3）三种规格怎么选配方（写进 SKILL 的分支，不是 planner 论文）

与 `compile_plan_rule` 一致：

| 用户给了什么 | T | A | 模型写 |
|--------------|---|---|--------|
| 只有文本 `text_requirement` | LLM `from_text`（与 A 同一轮） | LLM `from_text` full | T+A，再 M |
| 只有模板 `template` | 脚本聚类 `from_template` | 脚本范例 `from_exemplars` | **只写 M**；stylesheet 直接给 apply |
| 模板 + 文本只说明角色 `template_w_text` | 模板；文本可改 display_name | 仍用范例 | 只写 M |
| 模板 + 「相对模板再改…」 | 同模板 | 范例 + 文本点名键 overlay | 复制 A 改键，再写 M |

`compile_plan` 不必作为 agent 必做步骤。Skill 按上面四行写成可执行命令即可。

### （4）脚本契约：每步喂什么、收回什么

Agent 需要的不是 API 散文，是 **命令 + JSON 形状**。建议四个脚本（可合并成一个 CLI 的子命令）：

```text
scripts/inventory.py   source [template]     → inventory.json（元素列表，含 location_id / 层 / 文本预览 / 可选当前格式）
scripts/from_template.py  template inventory  → 可写入 T 草稿 + A 的 exemplar 部分（确定性）
scripts/validate.py    kind json              → 通过或报错（kind ∈ target_set | target_props | target_loc）
scripts/apply.py       source T A M output    → output.docx
```

SKILL.md 里各给一个「最小合法 JSON」例子（十行以内）。完整 schema 放 `reference/contracts.md`。

**LLM 步骤不做成「内部再调一个模型的脚本」。** 有模板时 T/A 来自 `template_stylesheet.py`；无模板时由当前 agent 写 T+A。M 始终由 agent 写。apply 校验失败则改 JSON 再跑。这样：

- 不出现「skill 里又藏一套 API key / 第二模型」；  
- 封装的是方法（IR + 校验 + 写入），不是把 LangGraph 原样黑盒化；  
- 若以后要一键跑完全流水线，另加 `scripts/run_pipeline.py`（L0），SKILL 写「用户只要成品、不管中间 IR 时用这个」。

---

## 3. 明确不要塞进 SKILL.md 的

| 不要给 | 原因 |
|--------|------|
| 现有 prompt yaml 全文 | 上下文会被撑爆；约束已体现在 schema + 短规则里。需要时 `reference/` 链到原文件 |
| whitelist 每一个键 | 让 `validate.py` 拒绝非法键，并在报错里列出允许键；SKILL 只说「只许白名单」 |
| LangGraph、artifacts 目录编号、resume | 编排实现，与 agent 无关 |
| batch_sizes、多 worker | 一次给全量 inventory 即可 |
| officecli 命令大全 | 那是另一个 skill；本 skill 的写入只有 `apply.py` |
| 如何「思考排版美学」 | 模型已会；你们要的是角色对齐和属性落地 |

原则（与 Cursor skill 指南一致）：**只写模型没有的领域约定；能用脚本强制的，不要用散文恳求。**

---

## 4. LLM 步骤如何体现：不是 tool，是「填表 + 校验」

T / 从文本填 A / M 本质是提示词任务。做成 skill 时：

```text
脚本 inventory / from_template  →  给模型看的材料
SKILL.md 里的短规则 + JSON 例子 →  告诉模型表长什么样
模型在对话里直接输出 JSON
脚本 validate                   →  不合格就返回错误，模型改一版
脚本 apply                      →  只接受通过校验的三份 JSON
```

不必 `prepare` 再返回一整份 system prompt（除非实验要「和主流程逐字同一提示词」）。主流程 yaml 里真正关键、模型不知道的约束，**压缩进 SKILL.md 几条**即可，例如：

- 层只能是 `section | paragraph.body | paragraph.table_cell | table | image | run`  
- `style_id` 建议 `Sec*` / `Para*` / `Tbl*` / `Img*` / `Run*`  
- 表框属于 table，不要建成独立 target  
- description 写角色用途，不要把「小四、宋体」写进 T  
- A 的键必须来自白名单；改模板时只输出相对模板的 delta，交给 overlay 脚本  
- M 映射 source preview 的 `section` + `paragraph.body`（加表级 id）；不要给每个 cell/run 贴标签  
- 不要改 `content` 字段

这些才是「提示词里的方法」，不是 yaml 的语气和 few-shot。

若某次实验必须 bit-identical 于 `catalog_from_text.yaml`：让 `scripts/prompt.py --stage T` 把填好槽的 user 打到 stdout，agent 读完再写 JSON。这是可选，不是 skill 的主路径。

---

## 5. SKILL.md 建议长什么样（仍不撰写正文）

目标：大约 80–150 行，不是 500 行。结构固定：

```markdown
---
name: longdoc-format
description: >
  Formats an existing Word document in place from a template.docx and/or
  text requirements. Use when the user wants to adjust formatting of a
  .docx while keeping body text, given a reference template and/or a
  style brief. Do not use for writing new content or mail-merge fill-in.
---

# Long-document formatting

## When
（任务框：三个输入、只改格式）

## Do not
- 不要重写正文 / 不要 officecli set 代替 apply / 不要跳过 validate

## Model
（T、A、M、apply 四行 + 依赖）

## Workflow
1. inventory.py
2. 按「三种规格」表决定 T/A 怎么来
3. 写出 target_set.json → validate.py target_set
4. 写出 target_props.json → validate.py target_props
5. 写出 target_loc.json → validate.py target_loc
6. apply.py

## Input modes
（第 2 节那张决策表）

## JSON sketches
（T / A / M 各一个最小例子）

## Scripts
（命令行，参数与文件名）

## More
- 完整字段：[reference/contracts.md](reference/contracts.md)
- 属性键报错时看 validate 输出，或 [reference/whitelist.md](reference/whitelist.md)
```

`description` 必须同时写 **做什么** 和 **何时用 / 何时不用**，否则 agent 会在「写年报」「改几处加粗」时误触发或漏触发。

---

## 6. scripts/ 里放什么（方法的牙齿）

脚本是 skill 的强制部分。模型可以一次读完整份文档结构，但 **不能** 一次写坏 docx。

| 脚本 | 替代的原 node | 模型参与？ |
|------|----------------|------------|
| `inventory.py` | inventory | 否，只产出给模型看的结构 |
| `from_template.py` | T 聚类 + A `from_exemplars` | 否 |
| `overlay.py` | A `overlay` | 否（base + patch → A） |
| `validate.py` | 各步隐式 schema | 否；失败信息要让模型能改 |
| `apply.py` | document_modify | 否 |
| `run_pipeline.py`（可选） | 整图 L0 | 否（内部再用原 LLM 配置） |

没有 `validate` 的 skill 只是说明书：模型仍会输出幻觉键、漏元素、跳过 M。

---

## 7. 和「每个 node 一个 tool」的关系

不必为每个 LangGraph node 做一个 tool。对 agent 而言工具就是这些脚本。原先的 LLM node 变成 **SKILL 里的填表步骤**。

| 原 node | 在 skill 里的形态 |
|---------|-------------------|
| compile_plan | 决策表（第 2.3 节），不是一次调用 |
| inventory | 脚本 |
| target_extract | 模板路径：脚本按对象聚类；workflow 再 **一次** LLM 命名整份 T（Tbl* 带 caption_type / header_semantics，typical_sections 仅辅助）。Skill 模板分支不另开命名 LLM，roles.json 用 examples + captions + header_rows |
| target_attribute_spec | 范例路径：脚本；文本/delta：agent 写 A；合并：脚本 |
| target_element_loc | agent 一次写完 M（允许整份 inventory） |
| apply / finalize | 脚本 |

这仍然是你们的方法（编译再执行），只是 **脑从流水线 LLM 换成了调用方模型**，**手仍是确定性脚本**。

若对比实验需要「agent 只调度、脑仍用原 prompt」：让 T/A/M 也变成 hosted 脚本（内部调现有 `build_target_set` 等）。那是另一条 SKILL 分支（`run_pipeline.py`），不要和「agent 填 IR」混在同一份主说明里。可在 SKILL 顶部用两行分流：

- 只要成品 → `run_pipeline.py`  
- 要按方法自己编 IR → 下面的 Workflow  

---

## 8. 落地顺序

1. 写短 `SKILL.md`（第 5 节骨架）和 `reference/contracts.md`（字段与最小例子）。  
2. 实现 `inventory` / `validate` / `apply` 三个脚本（方法能否成立取决于这三件）。  
3. 再补 `from_template` / `overlay`。  
4. 用一个真实 agent 试：只给 SKILL + 脚本，看它会不会仍去 officecli set。若会，加硬：`apply.py` 是唯一写 docx 的入口，SKILL 的 Do not 再写死一句。  
5. 可选 `run_pipeline.py` 做 L0。

仍不在本阶段写 Python。契约和 SKILL 骨架稳定后再实现脚本。
