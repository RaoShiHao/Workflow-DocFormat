# Skills

Two skills for the same restyle task. The folder name matches the `name` field in each `SKILL.md`. Copy a skill so the loader sees `SKILL.md` directly under that folder, not under an extra parent.

| Folder | Name | What it gives the agent |
|--------|------|-------------------------|
| [`DocFmt-Skill/`](DocFmt-Skill/SKILL.md) | `DocFmt-Skill` | Text procedure only. It sets the order of inspect, plan, and apply, and it does not name a document CLI or a property schema. |
| [`DocFmt-ToolSkill/`](DocFmt-ToolSkill/SKILL.md) | `DocFmt-ToolSkill` | The same formatting job with scripts. `scripts/parse_docx.py`, `template_stylesheet.py`, `merge_loc.py`, and `apply_format.py` call `officecli`. Notes for each step are in `references/`. |

```powershell
$root = "$env:USERPROFILE\.agents\skills"
Copy-Item -Recurse -Force .\DocFmt-Skill "$root\DocFmt-Skill"
Copy-Item -Recurse -Force .\DocFmt-ToolSkill "$root\DocFmt-ToolSkill"
```

Load one skill per run. `DocFmt-ToolSkill` needs `officecli` 1.0.145 on `PATH` and Python 3.11+. It does not import `LongDocFmt`. Install steps for that skill are in `DocFmt-ToolSkill/references/inject.md`.
