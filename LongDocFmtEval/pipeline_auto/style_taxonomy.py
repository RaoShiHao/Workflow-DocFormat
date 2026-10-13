"""Shared stylesheet layer taxonomy for pipeline_auto steps 3–5.

Table-related scopes (three different surfaces — do not conflate):

1. **table-structure** (stylesheet ``object=table``, Tbl*):
   whole-table identity — width, align, repeat_header, table-level borders/layout.
2. **table-cell** (nested under ``Tbl*.props.cells.<pos>``, NOT separate style_ids):
   per-slot chrome — cell borders, fill, valign. Keys like label/value/header/data.
3. **table-paragraph** (stylesheet ``object=paragraph.table_cell``, Para*Cell):
   Word paragraph/named style on the text *inside* each cell — font/size/bold/align.
   One Para*Cell may be referenced by many Tbl* via ``position_map`` (reuse, not collision).

Aliases kept for older artifacts: ``paragraph.cell_text`` ≡ ``paragraph.table_cell``.

Other layers: section, paragraph.body, image, run.

Selection JSON keys stay backward-compatible:
  ``paragraph_cell_roles`` ↔ table-paragraph layer.
"""
from __future__ import annotations

from typing import Any

# Stable layer keys used in logs / style_layers.json
LAYER_SECTION = "section"
LAYER_PARAGRAPH_BODY = "paragraph.body"
LAYER_TABLE = "table"  # table-structure (Tbl*)
LAYER_CELL_TEXT = "paragraph.table_cell"  # table-paragraph (Para*Cell); was paragraph.cell_text
LAYER_IMAGE = "image"
LAYER_RUN = "run"

# Canonical stylesheet object strings
OBJ_TABLE_STRUCTURE = "table"
OBJ_TABLE_PARAGRAPH = "paragraph.table_cell"
OBJ_PARAGRAPH_BODY = "paragraph.body"
# Legacy aliases still accepted when reading
_TABLE_PARAGRAPH_ALIASES = frozenset({
    "paragraph.table_cell",
    "paragraph.cell_text",
    "table.paragraph",
    "cell",
    "cell_text",
})

LAYER_ORDER: tuple[str, ...] = (
    LAYER_SECTION,
    LAYER_PARAGRAPH_BODY,
    LAYER_TABLE,
    LAYER_CELL_TEXT,
    LAYER_IMAGE,
    LAYER_RUN,
)

# selection / keep_by_layer legacy key → display layer
_SELECTION_TO_LAYER = {
    "section": LAYER_SECTION,
    "paragraph.body": LAYER_PARAGRAPH_BODY,
    "paragraph.table_cell": LAYER_CELL_TEXT,
    "paragraph.cell_text": LAYER_CELL_TEXT,
    "table": LAYER_TABLE,
    "image": LAYER_IMAGE,
    "paragraph_runs": LAYER_RUN,
}

TAXONOMY_BRIEF_ZH = """\
## 样式分层（step3–5 统一；stylesheet 按 style_id 去重计数）
- **section**：节级版式（Sec*）
- **paragraph.body**：流式正文/标题/题注等（Para*，不含格内）
- **table-structure（object=table，Tbl*）**：整表结构身份（边框体系、宽度、repeat_header…）
- **table-cell（非独立 style；写在 Tbl*.cells）**：单元格 chrome（底纹/valign/格线）；
  位名 label/value/header/data/stub/note 只表示格位，不是段落样式
- **table-paragraph（object=paragraph.table_cell，Para*Cell）**：格内**文字**段落样式；
  可被多个 Tbl* 的 position_map 引用——跨表复用，stylesheet 只计 1 次；**不是**同格式碰撞
- **image** / **run**：图与行内标记
一张表的观感 = 1×table-structure + 若干 table-cell chrome + 其引用的 table-paragraph。
渲染时每个物理格必须带/解析出 cell_style（→ cells[cell_style]）与 paragraph_style（须为 Para*Cell）；
可用 props.cell_style_plan：优先 column | row；matrix 仅允许、非必须。
禁止渲染器按行号硬编码三线。以可读/美观/体裁规范为先。
C 档：Tbl*/Img* 跨档近齐；靠合并近义 Para*Cell（与正文/节）拉开总数。
"""

TAXONOMY_BRIEF_EN = """\
## Style layers (unified across steps 3–5; stylesheet counts distinct style_ids)
- **section**: section layout (Sec*)
- **paragraph.body**: flow body/headings/captions (Para*, excluding in-cell)
- **table-structure (object=table, Tbl*)**: whole-table identity (borders, width, repeat_header…)
- **table-cell (not separate style_ids; under Tbl*.cells)**: per-slot chrome (fill/valign/cell borders);
  keys like label/value/header/data are slot names, not paragraph styles
- **table-paragraph (object=paragraph.table_cell, Para*Cell)**: in-cell **text** paragraph styles;
  many Tbl* may reference the same Para*Cell via position_map — reuse (count once), not a collision
- **image** / **run**: figures and inline marks
One table look = 1×table-structure + table-cell chrome + referenced table-paragraph roles.
At render time each physical cell resolves cell_style → cells[cell_style] and paragraph_style
(must be a Para*Cell); optional props.cell_style_plan: prefer column | row; matrix allowed-not-required.
No row-index three-line hardcoding. Favor readability / aesthetics / genre norms.
C-tiers: keep Tbl*/Img* near-parity; shrink totals mainly by merging near-synonym Para*Cell
(and body/section), not by dropping purpose-distinct Tbl*.
"""


def taxonomy_brief(*, locale: str = "zh") -> str:
    if str(locale).lower().startswith("en"):
        return TAXONOMY_BRIEF_EN.strip()
    return TAXONOMY_BRIEF_ZH.strip()


def is_table_paragraph_object(obj: str | None, style_id: str = "") -> bool:
    """True if stylesheet/role object (or id heuristic) is table-paragraph (Para*Cell)."""
    o = str(obj or "").strip().lower()
    sid = str(style_id or "")
    if o in _TABLE_PARAGRAPH_ALIASES:
        return True
    if o in {"paragraph", "paragraph.body", ""} and looks_like_table_paragraph_id(sid):
        return True
    return looks_like_table_paragraph_id(sid) and o.startswith("paragraph")


def looks_like_table_paragraph_id(style_id: str) -> bool:
    sid = str(style_id or "")
    if not sid.startswith("Para"):
        return False
    return "Cell" in sid or sid.startswith("ParaTbl")


def normalize_paragraph_object(obj: str | None, style_id: str = "") -> str:
    """Canonical object string for a Para* / Para*Cell role."""
    o = str(obj or "").strip().lower()
    if is_table_paragraph_object(o, style_id):
        return OBJ_TABLE_PARAGRAPH
    if o in {"", "paragraph", "para"}:
        return OBJ_PARAGRAPH_BODY if not looks_like_table_paragraph_id(style_id) else OBJ_TABLE_PARAGRAPH
    if o.startswith("paragraph.body") or o == "body":
        return OBJ_PARAGRAPH_BODY
    return str(obj or OBJ_PARAGRAPH_BODY)


def empty_layer_counts() -> dict[str, int]:
    return {k: 0 for k in LAYER_ORDER}


def normalize_keep_counts(keep_or_counts: dict[str, Any]) -> dict[str, int]:
    """Map step3 `_keep_by_layer` sets or raw counts → display layer counts."""
    out = empty_layer_counts()
    for key, val in (keep_or_counts or {}).items():
        layer = _SELECTION_TO_LAYER.get(str(key), str(key))
        if layer not in out:
            # legacy display name
            if str(key) in {"paragraph.cell_text", "table-paragraph"}:
                layer = LAYER_CELL_TEXT
            elif str(key) in {"table-structure"}:
                layer = LAYER_TABLE
            else:
                continue
        if isinstance(val, (set, list, tuple)):
            out[layer] = len(val)
        else:
            try:
                out[layer] = int(val)
            except (TypeError, ValueError):
                out[layer] = 0
    return out


def count_from_selection(selection: dict[str, Any] | None) -> dict[str, int]:
    sel = selection or {}
    tbl = sel.get("table_roles") or []
    table_ids = {
        *(t.get("role_id") for t in tbl if isinstance(t, dict) and t.get("role_id")),
        *(t for t in tbl if isinstance(t, str)),
    }
    return {
        LAYER_SECTION: len(sel.get("section_roles") or []),
        LAYER_PARAGRAPH_BODY: len(sel.get("paragraph_body_roles") or []),
        LAYER_TABLE: len(table_ids),
        LAYER_CELL_TEXT: len(sel.get("paragraph_cell_roles") or []),
        LAYER_IMAGE: len(sel.get("image_roles") or []),
        LAYER_RUN: len(sel.get("run_roles") or []),
    }


def count_from_stylesheet(stylesheet: Any) -> dict[str, int]:
    """Count stylesheet entries by object (+ heuristic for table-paragraph)."""
    out = empty_layer_counts()
    if not isinstance(stylesheet, list):
        return out
    for item in stylesheet:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        obj = str(item.get("object") or "").lower()
        if obj == "section" or sid.startswith("Sec"):
            out[LAYER_SECTION] += 1
        elif obj == "table" or sid.startswith("Tbl"):
            out[LAYER_TABLE] += 1
        elif obj == "image" or sid.startswith("Img"):
            out[LAYER_IMAGE] += 1
        elif obj == "run" or sid.startswith("Run"):
            out[LAYER_RUN] += 1
        elif is_table_paragraph_object(obj, sid):
            out[LAYER_CELL_TEXT] += 1
        elif obj.startswith("paragraph") or sid.startswith("Para"):
            out[LAYER_PARAGRAPH_BODY] += 1
        elif "Cell" in sid:
            out[LAYER_CELL_TEXT] += 1
    return out


def total_styles(counts: dict[str, int]) -> int:
    return sum(int(counts.get(k) or 0) for k in LAYER_ORDER)


def format_ref_roles(counts: dict[str, int]) -> str:
    """Python-dict-like string for logs (stable key order)."""
    return str({k: int(counts.get(k) or 0) for k in LAYER_ORDER})


def cell_paragraphs_for_table(
    table_role_id: str,
    plan: dict[str, Any] | None,
    lib: dict[str, Any] | None,
) -> list[str]:
    """Para*Cell role_ids referenced by a Tbl* position_map (deduped, stable order)."""
    plan = plan or {}
    lib = lib or {}
    kept = None
    for t in (plan.get("selection") or {}).get("table_roles") or []:
        if isinstance(t, dict) and t.get("role_id") == table_role_id:
            kept = t.get("position_map_kept")
            break
    out: list[str] = []
    seen: set[str] = set()

    def _add(para: Any) -> None:
        p = str(para or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    # 1) stylesheet entry may carry position_map (enriched) or cells.*.paragraph
    for item in plan.get("stylesheet") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("style_id") or item.get("role_id") or "") != table_role_id:
            continue
        pmap = item.get("position_map") or {}
        if isinstance(pmap, dict):
            for v in pmap.values():
                if isinstance(v, str):
                    _add(v)
                elif isinstance(v, dict):
                    _add(v.get("paragraph") or v.get("role_id"))
        cells = item.get("cells") or {}
        if isinstance(cells, dict):
            for meta in cells.values():
                if isinstance(meta, dict):
                    _add(meta.get("paragraph") or meta.get("paragraph_style"))
        break

    # 2) role library table_layout.position_map
    for t in (lib.get("roles") or {}).get("table") or []:
        if not isinstance(t, dict) or t.get("role_id") != table_role_id:
            continue
        pm = (t.get("table_layout") or {}).get("position_map") or {}
        for pos, meta in pm.items():
            if kept is not None and pos not in kept:
                continue
            if isinstance(meta, dict):
                _add(meta.get("paragraph") or meta.get("paragraph_role"))
            elif isinstance(meta, str):
                _add(meta)
        break
    return out


def layers_payload(
    *,
    selection: dict[str, Any] | None = None,
    stylesheet: Any = None,
    keep_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Artifact for style_layers.json / validation meta."""
    from_sel = count_from_selection(selection) if selection is not None else None
    from_ss = count_from_stylesheet(stylesheet) if stylesheet is not None else None
    from_keep = normalize_keep_counts(keep_counts) if keep_counts is not None else None
    primary = from_sel or from_keep or from_ss or empty_layer_counts()
    return {
        "version": 2,
        "layers": list(LAYER_ORDER),
        "counts": primary,
        "counts_from_selection": from_sel,
        "counts_from_stylesheet": from_ss,
        "table_scopes": {
            "table-structure": "object=table (Tbl*)",
            "table-cell": "Tbl*.cells chrome (not separate style_ids)",
            "table-paragraph": "object=paragraph.table_cell (Para*Cell)",
        },
        "note": (
            "table-structure = Tbl*; table-cell = nested chrome; "
            "table-paragraph = reusable Para*Cell; sharing one Para*Cell across Tbl* "
            "is reuse (count once), not a collision."
        ),
    }
