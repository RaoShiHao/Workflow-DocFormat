# examples

Small documents for a first run. They are synthetic samples, not a benchmark.

| File | Use |
|------|-----|
| `source.docx` | Unformatted source |
| `template.docx` | Layout template |
| `requirement.zh.md` / `requirement.en.md` | Text-only style rules |

Workflow:

```bash
python -m LongDocFormatter --template examples/template.docx --source examples/source.docx --output examples/output.docx
```

Skill: point the agent at these three paths (see the top-level README).
