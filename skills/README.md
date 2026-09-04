# skills

Agent Skill package: [`longdoc-format/`](./longdoc-format/) (`name` matches the folder, per [agentskills.io](https://agentskills.io/specification)).

Scripts talk to the local `officecli` binary. They only use `scripts/lib/` plus the Python standard library — they do **not** import `LongDocFormatter`.

Copy the whole `longdoc-format/` directory into your agent’s skills root (see [longdoc-format/references/inject.md](./longdoc-format/references/inject.md)).

| Path | Role |
|------|------|
| [longdoc-format/SKILL.md](./longdoc-format/SKILL.md) | When to use it; `text_requirement` / `template` / `template_w_text` branches; what the model writes vs what scripts write |
| [longdoc-format/scripts/](./longdoc-format/scripts/) | `parse_docx` / `template_stylesheet` / `apply_format` + `lib/` |
| [longdoc-format/references/](./longdoc-format/references/) | IR contract and how to inject the skill |
| [DESIGN.md](./DESIGN.md) | Design notes |

The LangGraph / LLM workflow that implements the same T → A → M method lives in [`../LongDocFormatter/`](../LongDocFormatter/).
