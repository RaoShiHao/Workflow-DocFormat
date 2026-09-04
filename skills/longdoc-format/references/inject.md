# How to load `longdoc-format`

The portable unit is **this folder** (it must contain `SKILL.md`).

`officecli` must be on `PATH`. Scripts use the Python **standard library only** (no PyPI packages; `requirements.txt` is empty). They only import `scripts/lib/` inside this folder — they do **not** need `LongDocFormatter` on `PYTHONPATH`.

Do **not** drop a parent wrapper directory into the skills root. DeepSeek Harness does **not** recurse `**/SKILL.md`; it only sees:

```text
<skills-root>/longdoc-format/SKILL.md
```

An extra layer such as `formatter_skills/longdoc-format/` will not be found.

Check the host first:

```powershell
officecli --version
python --version   # 3.11+
```

In this repository the skill lives at `skills/longdoc-format/`. Copy **that** directory (not `skills/` itself) into the scanner root.

---

## Codex

Official scan locations ([Codex skills](https://developers.openai.com/codex/skills)):

| Scope | Path (Windows) |
|------|----------------|
| This repo (recommended) | `<git-root>\.agents\skills\longdoc-format\` |
| Current user | `%USERPROFILE%\.agents\skills\longdoc-format\` |
| Legacy | `%USERPROFILE%\.codex\skills\longdoc-format\` |

From the repository root:

```powershell
$dst = Join-Path (Get-Location) ".agents\skills\longdoc-format"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force "skills\longdoc-format\*" $dst
```

User-global (all projects):

```powershell
$dst = Join-Path $env:USERPROFILE ".agents\skills\longdoc-format"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force "skills\longdoc-format\*" $dst
```

Restart Codex (CLI / IDE plugin). If it does not appear, check that the folder is named exactly `longdoc-format` and contains `SKILL.md`.

Example prompt after opening a workspace that has the source document:

> Use the longdoc-format skill: restyle source.docx from template.docx and text_input.md. Do not change body text. Write output.docx.

Codex first sees `name` + `description`, then reads `SKILL.md`, then runs `scripts/*.py`.

---

## Cursor / Claude

Copy `skills/longdoc-format/` into the project or user skills directory your agent scans (for Cursor: `.cursor/skills/longdoc-format/` or `~/.cursor/skills/longdoc-format/`). Restart the agent so it reloads skills.

---

## DeepSeek Harness

Local provider roots (one level only, [docs](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/skills)):

| Priority | Path |
|----------|------|
| Project | `<git-root>/.dsh/skills/` |
| Project (shared with Codex) | `<git-root>/.agents/skills/` |
| User | `%USERPROFILE%\.dsh\skills\` |
| User (shared) | `%USERPROFILE%\.agents\skills\` |

Recommended: copy to `.agents/skills/longdoc-format/` (same Copy-Item as Codex). Use `.dsh/skills/longdoc-format/` only if you want DSH and not Codex.

Extra directories can be listed in DSH config (field names follow your local harness):

```yaml
skills:
  filesystem:
    customSkillDirs:
      - ./skills
```

Each `customSkillDirs` entry must be a **skills root** (it must contain `longdoc-format/` directly), not `longdoc-format` itself.

---

## What the agent actually does

It looks for `template.docx` / requirement text, then follows the matching branch in `SKILL.md` (`text_requirement` / `template` / `template_w_text`):

1. Parse **source**: `python scripts/parse_docx.py --docx ... --out inventory.json --preview-out preview.json` (also writes `preview.ids.json`)
2. **Text only:** skip stylesheet; `cat preview.ids.json` + requirement; the model writes T+A, then M; apply three JSON files.
3. **Template only:** `python scripts/template_stylesheet.py --template ... --out stylesheet.json` (also writes `stylesheet.roles.json`; stylesheet is T+A). The model cats `preview.ids.json` and `stylesheet.roles.json` and writes only `target_loc.json`. Apply with `--target-set` and `--target-props` both pointing at `stylesheet.json`.
4. **Template + text:** same stylesheet; text usually helps naming. If the text asks for relative format changes, copy `target_props` and edit the named keys. Then write M and apply.

Read **`preview.ids.json` / `stylesheet.roles.json`** / the requirement. Do not `cat` full `preview.json` / `inventory.json`, and do not scrape inventory with `python -c`. If `cat` is truncated, use `scripts/preview_ids.py --preview preview.json --out slice.json --from-id … --to-id …`.

Script paths are relative to the **skill directory** (the copied `longdoc-format/`).

---

## Common “not found” causes

- Extra directory layer (`skills/formatter_skills/longdoc-format/`)
- Folder name is not `longdoc-format`, or it does not match `name:` in `SKILL.md`
- Codex / DSH / Cursor was not restarted
- `officecli` is missing — scripts start then fail immediately
