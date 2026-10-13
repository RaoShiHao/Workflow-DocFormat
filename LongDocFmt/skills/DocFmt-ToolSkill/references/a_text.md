# A from text (`target_props.json`)

Use on the **text-only** branch. One JSON for **every** `style_id` in T. Do not produce A by walking object layers.

Each role keeps its catalog `object`. Use `12pt` / `2.5cm`, colors `#RRGGBB`, booleans `true`/`false`. Never `colWidths`. Two roles must not share identical props.

**Tables** must include non-empty `table_format`, `cells`, and `cell_paragraphs`. `cells` use named slots (`header` / `data` / `label` / `value` / `stub` / `note`), never `r1_c1`. `cell_paragraphs` is slot → the document-wide `Para*Cell` ladder (header and stub may share one style). Do not write `cell_style_plan`. Grid, fill, and valign belong on `cells` — do not replace them with `table_format.border.all` alone.

**Sections:** header/footer are nested objects `header` / `footer` / `header_first` (`text` or `field`, plus `align` / `size` / `color` / `bold` / `font.ea` / `font.latin`), not props. Cover may omit header copy but still needs footer. Body `header.text` from the requirement; footer often `field=page`.

Allowed keys (nothing else):

- paragraph: `font.latin`, `font.ea`, `size`, `bold`, `italic`, `color`, `align`, `lineSpacing`, `spaceBefore`, `spaceAfter`, `firstLineIndent`, `hangingIndent`, `indent`, `rightIndent`, `outlineLvl`, `keepNext`, `pageBreakBefore`, `listStyle`, `numLevel`
- section: `marginTop`, `marginBottom`, `marginLeft`, `marginRight`, `marginHeader`, `marginFooter`, `orientation`, `columns`, `pgBorders`, `type`, `pageNumFmt`, `pageStart`, `titlePage`
- table `table_format`: `border.all`, `border.top`, `border.bottom`, `border.left`, `border.right`, `align`, `width`, `layout`, `repeat_header`
- table `cells`: `fill`, `valign`, `border.*`
- image: `width`, `height`, `hAlign`
- run: `bold`, `italic`, `color`, `underline`, `font.ea`, `font.latin`, `size`, `caps`, `smallcaps`, `superscript`, `subscript`

```json
{
  "ParaBody": {
    "object": "paragraph.body",
    "props": {
      "font.ea": "宋体",
      "font.latin": "Times New Roman",
      "size": "12pt",
      "align": "both",
      "firstLineIndent": "24pt",
      "lineSpacing": "1.5"
    }
  },
  "ParaTblHeaderCell": {
    "object": "paragraph.table_cell",
    "props": {"font.ea": "黑体", "size": "10.5pt", "bold": true, "align": "center"}
  },
  "ParaTblDataCell": {
    "object": "paragraph.table_cell",
    "props": {"font.ea": "宋体", "size": "10.5pt", "align": "center"}
  },
  "TblData": {
    "object": "table",
    "table_format": {"align": "center", "border.all": "single"},
    "cells": {
      "header": {"fill": "#F0F0F0", "valign": "center"},
      "data": {"valign": "center"}
    },
    "cell_paragraphs": {"header": "ParaTblHeaderCell", "stub": "ParaTblHeaderCell", "data": "ParaTblDataCell"}
  }
}
```

Emit declarations **only** for style ids listed in T. Then map M.
