# Relative patch (template + text)

Use only on **template + text** when the requirement names a formatting **delta**, not when it only names roles.

Treat as a patch if the text talks about changing size, font, margin, outline, bold, italic, line spacing, or “adjustments relative to the template”, and is not only asking what to call `display_name`s. Otherwise keep stylesheet T+A and write loc only.

One sparse JSON covering **all** objects mentioned in the revision:

1. Patch **only** `style_id`s the revision clearly targets.
2. Each patch contains **only changed keys**. Do not echo template fonts, sizes, or colors unless the text asks to change them.
3. Copy stylesheet props into `target_props.json` first (or conceptually overlay on them). Unmentioned keys stay on the template base.
4. Section header/footer belong in `header` / `footer` / `header_first`, not props.
5. Table patches: only changed `table_format` keys, named cell slots, or `cell_paragraphs`. Omit untouched slots. Do not write `cell_style_plan`.
6. If the revision has no executable formatting change, do not write a patch file — apply with stylesheet T+A.

```json
{
  "ParaHeading1": {"object": "paragraph.body", "props": {"align": "center"}},
  "SecBody": {"object": "section", "header": {"text": "Business Review", "align": "center"}}
}
```

Apply: `--target-set stylesheet.json --target-props target_props.json --target-loc target_loc.json`.
