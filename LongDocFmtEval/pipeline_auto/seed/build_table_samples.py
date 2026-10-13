"""Build distinctive table sample grids for seed / step3 preview.

Unlike role ``example`` text (shared per ParaTbl* role), each table instance gets
its own header/rows derived from outline ``main_point``.

**Layout contract（与常理一致）**：
  - 多列表：``header`` = 第一**行**列名；正文行首列可为 stub（行标题），其余列为同一套 data（多年份/指标列共用 data，不把末列硬套成 note）。
  - 禁止把 ``header`` 当成整列复用到每一行（旧 generic 分支的 bug）。
  - 2 列信息表：``label`` / ``value`` 竖排键值对。
  - 人员表：首行表头 + 姓名/职务/单位等文本列；不要照片列或空占位格。

**Locale**：``locale=en*`` 时表头/行样例用英文；默认中文。由 ``obj["locale"]``、
seed/plan/template meta，或目录名 ``*_en*`` 推断。

Writes ``tables.json``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json  # noqa: E402

# position_map aliases → semantic column class for body rows
_STUB_POS = {
    "stub", "item", "task", "project_name", "row_label", "name", "indicator",
}
_NOTE_POS = {"note", "remark", "source", "footnote"}
_DATA_POS = {
    "data", "detail", "attribute", "responsibility", "person_value", "value", "field_value",
}
_SKIP_AS_COL = {
    "header", "row_first", "row_second", "row_last", "row_odd", "row_even",
    "photo", "photo_slot",
}
# Cover / info-form aliases (Role libraries often use *_col / *_cells)
_LABEL_POS = {"label", "label_col", "label_cells", "key", "field_label"}
_VALUE_POS = {"value", "value_col", "value_cells", "field_value"}

# Concrete matrix keys some LLMs emit instead of semantic positions
_RC_RE = re.compile(r"^r(\d+)c(\d+)$", re.I)


def _parse_rc_keys(keys: list[str] | dict) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for k in keys:
        m = _RC_RE.match(str(k))
        if m:
            out.append((int(m.group(1)), int(m.group(2)), str(k)))
    return out


def _is_headerish_role(rid: str | None) -> bool:
    s = (rid or "").lower()
    return "header" in s or "label" in s


def _is_dataish_role(rid: str | None) -> bool:
    s = (rid or "").lower()
    return any(x in s for x in ("data", "value", "person", "body", "stub", "note"))


def normalize_rc_bindings(by_pos: dict[str, str]) -> dict[str, str]:
    """Rewrite ``rNcM`` position_map bindings to semantic slots.

    Cover 2-col Header|Data → ``label``/``value``.
    Row1 all Header + body Data → ``header`` + ``stub``/``data``/``attribute``…
    Leaves already-semantic maps unchanged.
    """
    if not by_pos:
        return by_pos
    parsed = _parse_rc_keys(by_pos)
    if len(parsed) < 2 or len(parsed) < len(by_pos) * 0.5:
        return by_pos

    def role_at(r: int, c: int) -> str | None:
        return by_pos.get(f"r{r}c{c}") or by_pos.get(f"R{r}C{c}")

    max_r = max(r for r, _, _ in parsed)
    max_c = max(c for _, c, _ in parsed)
    kept = {k: v for k, v in by_pos.items() if not _RC_RE.match(str(k))}
    out = dict(kept)

    row1 = [role_at(1, c) for c in range(1, max_c + 1) if role_at(1, c)]
    row1_all_header = bool(row1) and all(_is_headerish_role(x) for x in row1)
    # 2-col label|value: every row col1 headerish, col2 dataish (and row1 is NOT all-header)
    if max_c == 2 and max_r >= 1:
        col1 = [role_at(r, 1) for r in range(1, max_r + 1) if role_at(r, 1)]
        col2 = [role_at(r, 2) for r in range(1, max_r + 1) if role_at(r, 2)]
        if (
            col1 and col2
            and all(_is_headerish_role(x) for x in col1)
            and all(_is_dataish_role(x) or not _is_headerish_role(x) for x in col2)
            and not (row1_all_header and _is_headerish_role(role_at(1, 2)))
        ):
            out.setdefault("label", col1[0] or "ParaTblHeaderCell")
            out.setdefault("value", col2[0] or "ParaTblDataCell")
            return out

    if row1_all_header:
        out.setdefault("header", row1[0] or "ParaTblHeaderCell")
        body_r = 2 if max_r >= 2 else 1
        # Prefer body row roles; if map only has header row, default body to DataCell
        out.setdefault(
            "stub",
            (role_at(body_r, 1) if body_r > 1 else None) or "ParaTblDataCell",
        )
        data_names = ["data", "attribute", "detail", "responsibility"]
        for c in range(2, max_c + 1):
            name = data_names[c - 2] if c - 2 < len(data_names) else f"data_{c}"
            out.setdefault(
                name,
                (role_at(body_r, c) if body_r > 1 else None) or "ParaTblDataCell",
            )
        return out

    # Ambiguous rNcM: 2-col → label/value; else header+stub+data
    if max_c == 2:
        out.setdefault("label", role_at(1, 1) or "ParaTblHeaderCell")
        out.setdefault("value", role_at(1, 2) or "ParaTblDataCell")
        return out
    out.setdefault("header", role_at(1, 1) or "ParaTblHeaderCell")
    out.setdefault("stub", role_at(2, 1) or role_at(1, 1) or "ParaTblDataCell")
    out.setdefault("data", role_at(2, 2) or role_at(1, 2) or "ParaTblDataCell")
    return out


def normalize_rc_chrome(
    cells: dict[str, dict] | None,
    *,
    table_format: dict | None = None,
) -> dict[str, dict]:
    """Rewrite ``rNcM`` cell-chrome keys to semantic slots (same rules as bindings)."""
    if not cells:
        return {}
    cells = {str(k): dict(v) if isinstance(v, dict) else {} for k, v in cells.items()}
    parsed = _parse_rc_keys(cells)
    if len(parsed) < 2 or len(parsed) < len(cells) * 0.5:
        return cells

    def at(r: int, c: int) -> dict:
        return cells.get(f"r{r}c{c}") or cells.get(f"R{r}C{c}") or {}

    max_r = max(r for r, _, _ in parsed)
    max_c = max(c for _, c, _ in parsed)
    out = {k: dict(v) for k, v in cells.items() if not _RC_RE.match(str(k))}
    tf = table_format or {}
    open_info = tf.get("border.all") in ("none", None) and not (
        tf.get("border.top") or tf.get("border.bottom")
    )
    any_header_fill = any(at(1, c).get("fill") for c in range(1, max_c + 1))

    # 2-col open / no header fill → cover info (label|value), even if r2c* exist
    if max_c == 2 and (open_info or not any_header_fill):
        if at(1, 1):
            out.setdefault("label", dict(at(1, 1)))
        if at(1, 2):
            out.setdefault("value", dict(at(1, 2)))
        return out

    if at(1, 1):
        out.setdefault("header", dict(at(1, 1)))
    body_r = 2 if max_r >= 2 else 1
    if at(body_r, 1) and body_r > 1:
        out.setdefault("stub", dict(at(body_r, 1)))
    data_names = ["data", "attribute", "detail", "responsibility"]
    for c in range(2, max_c + 1):
        name = data_names[c - 2] if c - 2 < len(data_names) else f"data_{c}"
        if at(body_r, c) and body_r > 1:
            out.setdefault(name, dict(at(body_r, c)))
    return out


def _first_pos(by_pos: dict[str, str], candidates: set[str]) -> str | None:
    for p in by_pos:
        if p in candidates:
            return p
    return None


def _is_label_value_form(by_pos: dict[str, str]) -> bool:
    """2-col key/value info table (not a header-row data table)."""
    has_label = _first_pos(by_pos, _LABEL_POS) is not None
    has_value = _first_pos(by_pos, _VALUE_POS) is not None
    # value may collide with data "value" — still OK if label present and no header row key
    return bool(has_label and has_value and "header" not in by_pos and "header_row" not in by_pos)


def _norm_locale(locale: str | None) -> str:
    return "en" if str(locale or "").lower().startswith("en") else "zh"


def _is_en(locale: str | None) -> bool:
    return _norm_locale(locale) == "en"


def infer_locale(
    *,
    seed_dir: Path | None = None,
    obj: dict | None = None,
    fallback: str = "zh",
) -> str:
    """Resolve zh|en from object / IR meta / directory name."""
    if obj and obj.get("locale"):
        return _norm_locale(obj.get("locale"))
    if seed_dir is not None:
        seed_dir = Path(seed_dir)
        for name in ("meta.json", "plan.json", "template.json", "brief.json"):
            path = seed_dir / name
            if not path.exists():
                continue
            try:
                data = load_json(path)
            except Exception:  # noqa: BLE001
                continue
            loc = data.get("locale")
            if not loc and isinstance(data.get("meta"), dict):
                loc = data["meta"].get("locale")
            if loc:
                return _norm_locale(loc)
        slug = seed_dir.name.lower()
        if re.search(r"(?:^|_)en(?:_|$)", slug) or slug.endswith("_en"):
            return "en"
    return _norm_locale(fallback)


_COVER_TABLE_STUBS = frozenset({
    "封面信息表", "TblCoverInfo", "cover info table", "cover information table",
})
_COVER_META_LABELS = frozenset({
    "封面信息表", "封面表", "项目信息表", "cover info", "cover information table",
    "cover info table", "cover table",
})


def _split_field_names(chunk: str) -> list[str]:
    """Split a comma/顿号-separated field-name list (not label=value pairs)."""
    chunk = re.split(r"示例|示意|examples?|e\.g\.", chunk, maxsplit=1, flags=re.I)[0]
    parts = re.split(r"[、，,/]|以及|和|\band\b", chunk, flags=re.I) if chunk else []
    cleaned: list[str] = []
    for p in parts:
        p = re.sub(r"^[的与及\s]+|[的与及\s]+$", "", p.strip(" ."))
        p = re.split(r"等", p, maxsplit=1)[0].strip()
        if 1 < len(p) <= 36:
            cleaned.append(p)
    return cleaned


def is_cover_main_point_stub(main_point: str) -> bool:
    """True when main_point is too short/generic for cover grid synthesis."""
    mp = (main_point or "").strip()
    if not mp:
        return True
    low = mp.lower()
    if mp in _COVER_TABLE_STUBS or low in _COVER_TABLE_STUBS:
        return True
    if "｜" in mp or "|" in mp:
        return False
    if re.search(r"[：:=]", mp):
        return False
    return mp in {"封面表", "信息表"} or (len(mp) <= 8 and "封面" in mp)


def effective_cover_main_point(main_point: str, fallback: str = "") -> str:
    """Prefer rich cover spec; fall back to canonical outline when specimen collapsed it."""
    mp = (main_point or "").strip()
    fb = (fallback or "").strip()
    if not is_cover_main_point_stub(mp):
        return mp
    if fb and not is_cover_main_point_stub(fb):
        return fb
    return mp or fb


def _split_fields(main_point: str) -> list[str]:
    """Extract field-like tokens from outline main_point (zh / en)."""
    mp = (main_point or "").strip()
    if not mp:
        return []
    chunk = ""
    for pat in (
        r"字段[：:]\s*([^。；;\n]+)",
        r"(?:字段)?(?:包括|含有?|含)([^。；;]+)",
        r"(?:列出|含)([^。；;]+)",
        r"(?:fields?(?:\s+include|\s*:)|columns?(?:\s*:|\s+include)|including)\s*([^.;]+)",
        r"^(?:封面(?:/项目)?信息表|Cover(?:\s+info)?(?:\s+table)?)\s*[：:]\s*([^。；;\n]+)",
    ):
        m = re.search(pat, mp, flags=re.I)
        if m:
            chunk = m.group(1)
            break
    if not chunk:
        return []
    return _split_field_names(chunk)


def _example_stubs(main_point: str) -> list[str]:
    """Pull row-title examples from main_point (zh / en cues + quotes)."""
    mp = main_point or ""
    m = re.search(
        r"(?:示例含|示意行可列|示意行|示例[为是：:]?|如可列|"
        r"examples?(?:\s+include|\s*:)|sample rows?(?:\s*:)?|e\.g\.|such as)\s*([^。；;.]+)",
        mp,
        flags=re.I,
    )
    stubs: list[str] = []
    if m:
        chunk = m.group(1)
        parts = re.split(r"[、，,/]|以及|和|等|\band\b|\bor\b", chunk, flags=re.I)
        stubs = [
            re.sub(r"^[的与及\s「『\"“]+|[的与及\s」』\"”]+$", "", p).strip(" .")
            for p in parts
        ]
        stubs = [p for p in stubs if 1 < len(p) <= 48]
    if not stubs:
        stubs = _quoted_examples(mp)
    seen: set[str] = set()
    out: list[str] = []
    for s in stubs:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:5]


def _cell_bank(locale: str) -> dict[str, list[str]]:
    if _is_en(locale):
        return {
            "milestone": ["Start 2026", "Push 2027", "Accept 2028"],
            "year": ["2026", "2027", "2028"],
            "timeline": ["Near term", "Mid term", "Long term"],
            "deadline": ["2026-12", "2027-06", "Ongoing"],
            "owner": ["East district", "West district", "County cluster"],
            "region": ["Core city", "Node county", "Outer towns"],
            "area": ["Core city", "Node county", "Eco buffer"],
            "layout": ["Riverside East", "Campus West", "South county"],
            "type": ["Core", "Coordinated", "Support"],
            "category": ["Integrated", "Specialized", "Safeguard"],
            "function": ["Service hub", "Industry zone", "Eco belt"],
            "role": ["Lead / radiate", "Relay / connect", "Baseline control"],
            "direction": ["Close facility gaps", "Raise service capacity", "Restore ecology"],
            "unit": ["Planning Dept.", "Housing Bureau", "County gov."],
            "lead": ["Planning Dept.", "Transport Bureau", "Ecology Bureau"],
            "content": ["Network upgrade", "Hub retrofit", "Cold-chain logistics"],
            "scope": ["County coverage", "Cross-district link", "Pilot site"],
            "investment": ["~USD 80M", "~USD 120M", "~USD 50M"],
            "note": ["Priority", "Rolling", "Budget-contingent"],
            "remark": ["Annual breakdown", "Cross-agency", "Tracked"],
            "indicator": ["Urbanization rate", "Service equity index", "Eco-space share"],
            "baseline": ["62.4", "71.0", "28.5"],
            "target": ["68.0", "78.0", "32.0"],
            "frequency": ["≥3× / week", "1–2× / week", "Occasional"],
            "sample": ["48", "52", "28"],
            "share": ["37.5%", "40.6%", "21.9%"],
            # also match Chinese keys if mixed headers appear
            "节点": ["Start 2026", "Push 2027", "Accept 2028"],
            "年度": ["2026", "2027", "2028"],
            "责任": ["East district", "West district", "County cluster"],
            "地区": ["Core city", "Node county", "Outer towns"],
            "建设内容": ["Network upgrade", "Hub retrofit", "Cold-chain logistics"],
            "牵头": ["Planning Dept.", "Transport Bureau", "Ecology Bureau"],
            "指标": ["Urbanization rate", "Service equity index", "Eco-space share"],
            "备注": ["Priority", "Rolling", "Budget-contingent"],
        }
    return {
        "节点": ["2026年启动", "2027年攻坚", "2028年验收"],
        "年度": ["2026", "2027", "2028"],
        "时序": ["近期", "中期", "远期"],
        "时限": ["2026-12", "2027-06", "持续推进"],
        "责任": ["东部片区", "西部片区", "县域组团"],
        "地区": ["中心城区", "节点县城", "外围乡镇"],
        "区域": ["中心城区", "节点县城", "生态涵养区"],
        "布局": ["东城·滨河", "西城·学府", "南县组团"],
        "分类": ["核心区", "协同区", "生态功能区"],
        "类型": ["综合型", "专业型", "保障型"],
        "功能": ["综合服务中心", "产业承载区", "生态涵养带"],
        "定位": ["辐射带动", "传导衔接", "底线管控"],
        "方向": ["补齐设施短板", "提升服务能级", "强化生态修复"],
        "协同": ["发改+住建", "卫健+民政", "交通+自然资源"],
        "单位": ["市发改委", "市住建局", "县政府"],
        "牵头": ["市发改委", "市交通局", "市生态环境局"],
        "建设内容": ["管网连通与扩容", "枢纽场站改建", "冷链仓配一体化"],
        "内容": ["能力提升", "机制完善", "试点推广"],
        "投资": ["约8亿元", "约12亿元", "约5亿元"],
        "规模": ["县域覆盖", "跨区联通", "示范试点"],
        "备注": ["优先推进", "滚动实施", "视财力安排"],
        "说明": ["按年度分解", "跨部门会商", "纳入督办"],
        "指标": ["常住人口城镇化率", "基本公共服务均等化指数", "生态空间占比"],
        "单位名": ["%", "分", "%"],
        "基期": ["62.4", "71.0", "28.5"],
        "目标": ["68.0", "78.0", "32.0"],
        "频次": ["每周≥3次", "每周1–2次", "偶发到访"],
        "样本": ["48", "52", "28"],
        "占比": ["37.5%", "40.6%", "21.9%"],
    }


def _cell_text_for_header(
    header: str, stub: str, oid: str, row_i: int, col_i: int, *, locale: str = "zh",
) -> str:
    """Synthesize a cell that fits the column header (preview/seed only)."""
    del stub  # reserved for future stub-aware cells
    h = header or ""
    h_l = h.lower()
    bank = _cell_bank(locale)
    for key, vals in bank.items():
        if key.lower() in h_l or key in h:
            return vals[row_i % len(vals)]
    tag = str(oid)[-3:] if oid else "tbl"
    if _is_en(locale):
        label = (h[:18] or "Col").strip()
        return f"{label}·{tag}-{row_i + 1}{col_i}"
    return f"{(h[:6] or '列')}·{tag}-{row_i + 1}{col_i}"


def _body_rows_from_mp(
    *,
    headers: list[str],
    main_point: str,
    oid: str,
    n_cols: int,
    fallback_stubs: list[str] | None = None,
    locale: str = "zh",
) -> tuple[list[str], list[list[str]]]:
    stubs = _example_stubs(main_point)
    if len(stubs) < 3:
        fb = list(fallback_stubs or [])
        salt = sum(ord(c) for c in str(oid)) % 3
        rotated = fb[salt:] + fb[:salt] if fb else []
        for s in rotated:
            if s not in stubs:
                stubs.append(s)
            if len(stubs) >= 3:
                break
    while len(stubs) < 3:
        if _is_en(locale):
            stubs.append(f"Item {len(stubs) + 1} ({str(oid)[-3:]})")
        else:
            stubs.append(f"条目{len(stubs) + 1}（{str(oid)[-3:]}）")
    stubs = stubs[:3]
    body: list[list[str]] = []
    for r_i, stub in enumerate(stubs):
        row = [stub]
        for c_i in range(1, n_cols):
            h = headers[c_i] if c_i < len(headers) else ""
            row.append(
                _cell_text_for_header(h, stub, str(oid), r_i, c_i, locale=locale)
            )
        body.append(row)
    return stubs, body


def _bindings(obj: dict) -> dict[str, str]:
    """Resolve pos→ParaTbl* from ``cells`` and/or ``position_map``."""
    raw: dict[str, str] = {}
    pmap = obj.get("position_map")
    if isinstance(pmap, dict):
        for pos, v in pmap.items():
            if isinstance(v, dict):
                rid = v.get("paragraph") or v.get("role_id") or ""
            else:
                rid = v
            if rid:
                raw[str(pos)] = str(rid)
    for c in (obj.get("cells") or []):
        if not isinstance(c, dict) or not c.get("pos"):
            continue
        rid = c.get("role_id") or c.get("paragraph") or ""
        if rid:
            raw[str(c["pos"])] = str(rid)
    return normalize_rc_bindings(raw)


def _cell(pos: str, role: str, text: str) -> dict[str, str]:
    return {"pos": pos, "role_id": role, "text": text}


def _body_column_order(
    by_pos: dict[str, str],
    *,
    n_cols: int | None = None,
    fields: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Ordered body columns from the LLM table style (same slots as gold render).

    Extra columns clone ``data`` / ``value``. ``note`` is not auto-appended as
    the last physical column just because it exists in ``position_map``.
    """
    from LongDocFmtEval.pipeline_auto.seed.table_chrome_presets import (  # noqa: PLC0415
        physical_body_slots,
    )

    target = n_cols if n_cols and n_cols > 0 else None
    if target is None and fields:
        target = max(2, min(len(fields), 8))
    if target is None:
        n_mapped = len([p for p in (by_pos or {}) if p not in _SKIP_AS_COL])
        target = max(2, min(n_mapped or 2, 8))
    return physical_body_slots(by_pos, int(target))


def _default_headers(n: int, fields: list[str], *, kind: str = "data", locale: str = "zh") -> list[str]:
    if len(fields) >= n:
        return fields[:n]
    if _is_en(locale):
        if kind == "project":
            base = ["Project", "Scope", "Location", "Lead unit"]
        elif kind == "matrix":
            base = ["Task", "Lead unit", "Partners", "Deadline"]
        elif kind == "roster":
            base = ["Name", "Title", "Organization", "Role"]
        elif kind == "stat":
            base = ["Indicator", "Unit", "Baseline", "Target"]
        else:
            base = ["Item", "Detail", "Note", "Remark"]
        while len(base) < n:
            base.append(f"Col {len(base) + 1}")
        return base[:n]
    if kind == "project":
        base = ["项目名称", "建设内容", "布局区域", "牵头单位"]
    elif kind == "matrix":
        base = ["主要任务", "牵头单位", "配合单位", "完成时限"]
    elif kind == "roster":
        base = ["姓名", "职务", "单位", "角色"]
    elif kind == "stat":
        base = ["指标名称", "单位", "基期值", "目标值"]
    else:
        base = ["项目", "内容", "说明", "备注"]
    while len(base) < n:
        base.append(f"列{len(base) + 1}")
    return base[:n]


def _quoted_examples(main_point: str) -> list[str]:
    """Pull quoted sample values from main_point (中英文引号)."""
    mp = main_point or ""
    return [
        q.strip()
        for q in re.findall(r'[「『""“]([^「『""“」』”]+)[」』""”]', mp)
        if q.strip()
    ]


def _parse_pipe_cover_lines(main_point: str) -> list[tuple[str, str]]:
    """Multiline ``label｜value`` / ``label|value`` rows (specimen / outline)."""
    pairs: list[tuple[str, str]] = []
    for line in re.split(r"[\r\n]+", (main_point or "").strip()):
        line = line.strip()
        if not line or line.startswith("|"):
            continue
        m = re.match(r"^(.+?)[｜|](.+)$", line)
        if not m:
            continue
        lab = m.group(1).strip().strip("「」『』\"'“”")
        val = m.group(2).strip().strip("「」『』\"'“”")
        lab = re.sub(r"^(?:字段|包括|含)\s*", "", lab).strip()
        if 1 < len(lab) <= 32 and 0 < len(val) <= 120:
            pairs.append((lab, val))
    return pairs


def parse_pipe_matrix(main_point: str) -> list[list[str]]:
    """Parse multiline pipe grid (header + body) from specimen/outline ``main_point``.

    Example::
        Metric|2024|2023
        Revenue|102.4|91.5
    """
    rows: list[list[str]] = []
    for line in re.split(r"[\r\n]+", (main_point or "").strip()):
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[\|｜:\-\s]+", line):
            continue
        parts = [p.strip() for p in re.split(r"[｜|]", line)]
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) >= 2:
            rows.append(parts)
    if len(rows) < 2:
        return []
    ncols = len(rows[0])
    if ncols < 2:
        return []
    out: list[list[str]] = []
    for r in rows:
        if len(r) < 2:
            continue
        padded = list(r[:ncols]) + [""] * max(0, ncols - len(r))
        out.append(padded[:ncols])
    return out if len(out) >= 2 else []


def _parse_explicit_cover_pairs(main_point: str) -> list[tuple[str, str]]:
    """Extract self-consistent label→value pairs already written in main_point.

    Accepts forms like:
      示意：作者=张三；导师=王教授
      作者：张三、导师：李四
      Author=Alex; Supervisor=Prof. Lee
      主体｜沧衡科创控股有限公司（多行管道格式见 ``_parse_pipe_cover_lines``）
    """
    mp = (main_point or "").strip()
    if not mp:
        return []

    pipe_pairs = _parse_pipe_cover_lines(mp)
    if len(pipe_pairs) >= 2:
        return pipe_pairs
    if len(pipe_pairs) == 1 and pipe_pairs[0][0] not in _COVER_META_LABELS:
        return pipe_pairs

    # Prefer the clause after 示意/示例/sample
    chunk = mp
    m = re.search(
        r"(?:示意|示例|样例|pairs?|samples?)\s*[:：]?\s*(.+)$",
        mp,
        flags=re.I | re.S,
    )
    if m:
        chunk = m.group(1)
    pairs: list[tuple[str, str]] = []
    # split on ；; or Chinese顿号 between pairs — not row pipes (handled above)
    for part in re.split(r"[；;]|(?<=\S)[、](?=[^：:=]{1,12}[：:=])", chunk):
        part = part.strip(" 。.;，,")
        if not part:
            continue
        kv = re.split(r"[＝=：:]", part, maxsplit=1)
        if len(kv) != 2:
            continue
        lab, val = kv[0].strip(), kv[1].strip().strip("「」『』\"'“”")
        lab = re.sub(r"^(?:字段|包括|含)\s*", "", lab).strip()
        if 1 < len(lab) <= 24 and 0 < len(val) <= 80:
            pairs.append((lab, val))

    if len(pairs) == 1 and pairs[0][0] in _COVER_META_LABELS:
        names = _split_field_names(pairs[0][1])
        if len(names) >= 2:
            return [(n, "") for n in names]
    if pairs:
        return pairs

    m2 = re.match(
        r"^(?:封面(?:/项目)?信息表|Cover(?:\s+info)?(?:\s+table)?)\s*[：:]\s*(.+)$",
        mp,
        flags=re.I,
    )
    if m2:
        names = _split_field_names(m2.group(1))
        if len(names) >= 2:
            return [(n, "") for n in names]

    return pipe_pairs


def _short_theme(theme: str, *, max_len: int = 28, locale: str = "zh") -> str:
    t = re.sub(r"\s+", "", str(theme or "").strip())
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + ("…" if _is_en(locale) else "…")


def _synthesize_cover_value(
    lab: str,
    *,
    theme: str = "",
    locale: str = "zh",
    index: int = 0,
) -> str:
    """Theme-aligned value for a label — no cross-genre default banks.

    Person/org placeholders stay neutral; topic-bearing labels (学科/题名/…) use theme.
    """
    lab_l = lab or ""
    lab_low = lab_l.lower()
    theme_s = (theme or "").strip()
    topic = _short_theme(theme_s, max_len=32, locale=locale)

    if _is_en(locale):
        if any(k in lab_low for k in ("author", "candidate", "student", "name")) and "title" not in lab_low:
            return "Alex Rivera"
        if any(k in lab_low for k in ("supervisor", "advisor")):
            return "Prof. Jordan Lee"
        if any(k in lab_low for k in ("program", "major", "discipline", "field")):
            return topic or "Systems Engineering"
        if any(k in lab_low for k in ("institution", "university", "college", "school", "department")):
            return "Fictional University"
        if any(k in lab_low for k in ("date", "submission", "issued", "year")):
            return "May 2026"
        if any(k in lab_low for k in ("title", "topic", "thesis")):
            return topic or "(sample title)"
        if any(k in lab_low for k in ("no.", "number", "doc")):
            return "(sample document no.)"
        return f"(sample · {lab_l or index + 1})"

    if any(k in lab_l for k in ("作者", "研究生", "姓名", "申请人")):
        return "张三"
    if any(k in lab_l for k in ("导师", "指导老师", "指导教师")):
        return "王教授"
    if any(k in lab_l for k in ("学科", "专业", "方向", "领域")):
        return topic or "（示意学科）"
    if any(k in lab_l for k in ("学校", "学院", "培养单位")):
        return "某某大学"
    if any(k in lab_l for k in ("日期", "时间", "完成", "答辩")):
        return "2026年5月"
    if any(k in lab_l for k in ("题名", "题目", "名称", "标题")):
        return topic or "（示意题名）"
    if any(k in lab_l for k in ("主体", "编制单位", "报告主体", "公司", "单位")):
        return topic.split("（")[0].split("(")[0][:36] or "（示意主体）"
    if any(k in lab_l for k in ("期间", "报告期", "报告期间", "会计期间")):
        if re.search(r"20\d{2}", theme_s):
            m = re.search(r"(20\d{2}[-—–至~到\s]*20\d{2}|20\d{2}年度?)", theme_s)
            return m.group(1) if m else "2024年度"
        return "2024年度"
    if "币种" in lab_l or "货币" in lab_l:
        return "人民币"
    if any(k in lab_l for k in ("版本", "附注范围", "披露范围", "范围")):
        if "范围" in lab_l:
            return "合并报表及附注" if "合并" in theme_s else "（示意范围）"
        return "（示意版本）"
    if "文号" in lab_l or (lab_l.endswith("号") and "学号" not in lab_l):
        # Stay on-theme: only invent a planning-style no. when theme itself is planning
        if any(k in theme_s for k in ("规划", "纲要", "发改", "计划")):
            return "发改规划〔2024〕12号"
        return "（示意文号）"
    if any(k in lab_l for k in ("编制", "机关", "发文单位")):
        if any(k in theme_s for k in ("规划", "纲要", "发改")):
            return "某市发展和改革委员会"
        return "（示意单位）"
    if any(k in lab_l for k in ("规划期", "期限", "年限")):
        return "2026—2030年" if any(k in theme_s for k in ("规划", "五年", "纲要")) else "（示意期限）"
    if any(k in lab_l for k in ("公开", "属性", "密级")):
        return "主动公开"
    return f"（示意·{lab_l or index + 1}）"


def _value_for_cover_label(
    lab: str,
    quotes: list[str],
    qi: list[int],
    explicit: dict[str, str],
    i: int,
    *,
    locale: str = "zh",
    theme: str = "",
) -> str:
    """Resolve one cover cell: explicit main_point pairs → quotes → theme-aligned synthesize."""
    lab_l = (lab or "").strip()
    if lab_l in explicit:
        return explicit[lab_l]
    for k, v in explicit.items():
        if k in lab_l or lab_l in k:
            return v

    def take_quote() -> str | None:
        if qi[0] < len(quotes):
            v = quotes[qi[0]]
            qi[0] += 1
            return v
        return None

    q = take_quote()
    # Quotes only if they look like a value for this label (short, not another label name)
    if q and len(q) <= 80 and q not in explicit:
        return q

    return _synthesize_cover_value(lab_l, theme=theme, locale=locale, index=i)


def _cover_grid(
    by_pos: dict[str, str], fields: list[str], oid: str, main_point: str = "",
    *, locale: str = "zh", role_id: str = "", theme: str = "",
) -> list[list[dict]]:
    """Build label|value rows that stay consistent with this document's theme/main_point."""
    del role_id  # reserved; coherence comes from theme + main_point, not role-name rules
    label_pos = _first_pos(by_pos, _LABEL_POS) or "label"
    value_pos = _first_pos(by_pos, _VALUE_POS) or "value"
    label_r = by_pos.get(label_pos) or "ParaTblCoverLabel"
    value_r = by_pos.get(value_pos) or "ParaTblCoverValue"

    explicit_pairs = _parse_explicit_cover_pairs(main_point)
    if not explicit_pairs and not fields and is_cover_main_point_stub(main_point):
        return []
    fixed_vals = {k: v for k, v in explicit_pairs if (v or "").strip()}
    explicit_map = dict(fixed_vals)
    quotes = _quoted_examples(main_point)
    qi = [0]

    # Row labels: explicit pairs (exact row count) → fields → minimal stubs
    if explicit_pairs:
        labels = [k for k, _ in explicit_pairs]
    else:
        labels = list(fields) if fields else []
    if not labels:
        labels = (
            ["Author", "Supervisor", "Program", "Institution", "Date"]
            if _is_en(locale)
            else ["作者", "导师", "学科", "学校", "日期"]
        )

    # Exact rows×2 cols: one row per label/pair; pad to ≥2 so each table-paragraph shows twice
    n = max(len(labels), 2)
    while len(labels) < n:
        labels.append(
            f"Field {len(labels) + 1}" if _is_en(locale) else f"字段{len(labels) + 1}"
        )
    rows: list[list[dict]] = []
    used_vals: set[str] = set()
    for i in range(n):
        lab = labels[i]
        if lab in fixed_vals:
            val = fixed_vals[lab]
        else:
            val = _value_for_cover_label(
                lab, quotes, qi, explicit_map, i, locale=locale, theme=theme,
            )
        if val in used_vals:
            val = f"{val}·{str(oid)[-3:]}-{i + 1}"
        used_vals.add(val)
        rows.append([_cell(label_pos, label_r, lab), _cell(value_pos, value_r, val)])
    return rows


def _header_row_table(
    by_pos: dict[str, str],
    fields: list[str],
    oid: str,
    *,
    kind: str = "data",
    body_stubs: list[str] | None = None,
    body_rows: list[list[str]] | None = None,
    locale: str = "zh",
) -> list[list[dict]]:
    """Standard data table: row1 = all header; body = stub + data/note columns.
    Column count follows ``fields`` (content), expanding position_map if needed.
    """
    h_r = by_pos.get("header") or "ParaTblHeaderCell"
    cols = _body_column_order(
        by_pos,
        fields=fields,
        n_cols=len(fields) if fields else None,
    )
    n = len(cols)
    headers = _default_headers(n, fields, kind=kind, locale=locale)

    header_row = [_cell("header", h_r, headers[i]) for i in range(n)]

    if body_stubs is None:
        body_stubs = (
            ["Riverside Community", "Campus Community", "Lakeside Community"]
            if _is_en(locale)
            else ["滨河社区", "学府社区", "柳岸社区"]
        )
    if len(body_stubs) < 2:
        body_stubs = list(body_stubs) + (
            ["Campus Community"] if _is_en(locale) else ["学府社区"]
        )
    if body_rows is None:
        body_rows = []
        for r, stub in enumerate(body_stubs):
            row_vals = [stub]
            for c in range(1, n):
                pos = cols[c][0]
                if pos in _NOTE_POS:
                    row_vals.append(f"Note {r + 1}" if _is_en(locale) else f"备注{r + 1}")
                else:
                    if _is_en(locale):
                        row_vals.append(f"Sample {r + 1}-{c} ({str(oid)[-3:]})")
                    else:
                        row_vals.append(f"样例{r + 1}-{c}（{str(oid)[-3:]}）")
            body_rows.append(row_vals)
    while len(body_rows) < 2:
        pad = list(body_rows[-1]) if body_rows else (["—"] * n)
        body_rows.append(pad)

    rows = [header_row]
    for r_i, vals in enumerate(body_rows):
        row: list[dict] = []
        for c_i, (pos, role) in enumerate(cols):
            text = vals[c_i] if c_i < len(vals) else f"{pos}:{r_i + 1}"
            row.append(_cell(pos, role, text))
        rows.append(row)
    return rows


def _stat_grid(
    by_pos: dict[str, str], fields: list[str], oid: str, main_point: str, *, locale: str = "zh",
) -> list[list[dict]]:
    matrix = parse_pipe_matrix(main_point)
    if matrix:
        headers = list(matrix[0])
        body_rows = [list(r) for r in matrix[1:]]
        return _header_row_table(
            by_pos, headers, oid, kind="stat",
            body_stubs=[r[0] for r in body_rows],
            body_rows=body_rows,
            locale=locale,
        )

    cols = _body_column_order(
        by_pos, fields=fields, n_cols=len(fields) if fields else None,
    )
    n = len(cols)
    mp = main_point or ""
    if ("日程" in mp or "schedule" in mp.lower()) or (fields and ("日期" in fields[0] or "date" in fields[0].lower())):
        kind = "data"
        headers_hint = fields or (
            ["Date", "Site", "Activity", "Note"] if _is_en(locale) else ["日期", "社区", "事项", "备注"]
        )
        fallback = (
            ["Jun 12", "Jun 15", "Jun 20"] if _is_en(locale) else ["6月12日", "6月15日", "6月20日"]
        )
    else:
        kind = "stat"
        headers_hint = fields or _default_headers(n, fields, kind="stat", locale=locale)
        fallback = (
            ["Demo item A", "Demo item B", "Demo item C"]
            if _is_en(locale)
            else ["示范条目甲", "示范条目乙", "示范条目丙"]
        )

    headers = _default_headers(n, headers_hint, kind=kind, locale=locale)
    stubs, body_rows = _body_rows_from_mp(
        headers=headers,
        main_point=main_point,
        oid=oid,
        n_cols=n,
        fallback_stubs=fallback,
        locale=locale,
    )
    return _header_row_table(
        by_pos, headers, oid, kind=kind, body_stubs=stubs, body_rows=body_rows, locale=locale,
    )


def _project_or_matrix_grid(
    by_pos: dict[str, str],
    fields: list[str],
    oid: str,
    main_point: str = "",
    *,
    kind: str,
    locale: str = "zh",
) -> list[list[dict]]:
    matrix = parse_pipe_matrix(main_point)
    if matrix:
        headers = list(matrix[0])
        body_rows = [list(r) for r in matrix[1:]]
        return _header_row_table(
            by_pos, headers, oid, kind=kind,
            body_stubs=[r[0] for r in body_rows],
            body_rows=body_rows,
            locale=locale,
        )

    if _is_en(locale):
        fallback = {
            "project": [
                "Riverside aging-friendly retrofit",
                "Campus meal-site expansion",
                "Lakeside smart-care platform",
            ],
            "matrix": [
                "Expand community meal network",
                "Advance aging-friendly retrofit",
                "Strengthen volunteer coordination",
            ],
        }.get(kind, ["Item A", "Item B", "Item C"])
    else:
        fallback = {
            "project": ["滨河社区适老化改造", "学府社区助餐点扩建", "柳岸社区智慧养老平台"],
            "matrix": ["完善社区助餐网络", "推进适老化改造", "健全志愿者联动"],
        }.get(kind, ["条目甲", "条目乙", "条目丙"])
    cols = _body_column_order(
        by_pos, fields=fields, n_cols=len(fields) if fields else None,
    )
    n = len(cols)
    headers = _default_headers(n, fields, kind=kind, locale=locale)
    stubs, body_rows = _body_rows_from_mp(
        headers=headers,
        main_point=main_point,
        oid=oid,
        n_cols=n,
        fallback_stubs=fallback,
        locale=locale,
    )
    return _header_row_table(
        by_pos, fields, oid, kind=kind, body_stubs=stubs, body_rows=body_rows, locale=locale,
    )


def _personnel_grid(
    by_pos: dict[str, str], fields: list[str], oid: str, *, locale: str = "zh",
) -> list[list[dict]]:
    """Personnel table: header row first; text columns only (no photo slot)."""
    h_r = by_pos.get("header") or by_pos.get("label") or "ParaTblHeaderCell"
    value_role = (
        by_pos.get("person_value")
        or by_pos.get("field_value")
        or by_pos.get("data")
        or "ParaTblPersonCell"
    )
    if _is_en(locale):
        data_headers = fields[:3] if len(fields) >= 2 else ["Name", "Title", "Organization"]
        while len(data_headers) < 3:
            data_headers.append(f"Col {len(data_headers) + 1}")
        people = [
            ["Mina Zhang", "Project liaison", "Municipal Aging Center"],
            ["Qiang Li", "Street coordinator", "East Street Office"],
            ["Fang Wang", "Community grid officer", "Riverside Community"],
        ]
    else:
        data_headers = fields[:3] if len(fields) >= 2 else ["姓名", "职务", "单位"]
        while len(data_headers) < 3:
            data_headers.append(f"列{len(data_headers) + 1}")
        people = [
            ["张敏", "项目联络员", "市民政局养老中心"],
            ["李强", "街道协调员", "东城街道办事处"],
            ["王芳", "社区网格员", "滨河社区居委会"],
        ]
    data_headers = data_headers[:3]

    fake_pos = {
        "header": h_r,
        "stub": by_pos.get("stub") or by_pos.get("name") or value_role,
        "data": value_role,
        "note": by_pos.get("note") or value_role,
    }
    syn = {"header": h_r, "name": fake_pos["stub"], "title": value_role, "org": value_role}
    return _header_row_table(
        syn,
        data_headers,
        oid,
        kind="roster",
        body_stubs=[p[0] for p in people],
        body_rows=people,
        locale=locale,
    )


def _field_pair_personnel(
    by_pos: dict[str, str], fields: list[str], *, locale: str = "zh",
) -> list[list[dict]]:
    """Legacy card layout: label/value pairs only (no photo column)."""
    fh_r = by_pos.get("field_header") or by_pos.get("label") or "ParaTblHeaderCell"
    fv_r = by_pos.get("field_value") or by_pos.get("person_value") or "ParaTblPersonCell"
    if _is_en(locale):
        pairs = [
            ("Alias", "Aunt Wang"),
            ("Age band", "70–74"),
            ("Role", "Meal-site regular / volunteer"),
        ]
    else:
        pairs = [
            ("化名", "王阿姨"),
            ("年龄段", "70–74岁"),
            ("角色身份", "助餐点常客 / 志愿者"),
        ]
    if fields:
        pairs = [(fields[i], pairs[i % len(pairs)][1]) for i in range(min(3, len(fields)))]
    return [
        [_cell("field_header", fh_r, pairs[0][0]), _cell("field_value", fv_r, pairs[0][1])],
        [_cell("field_header", fh_r, pairs[1][0]), _cell("field_value", fv_r, pairs[1][1])],
        [_cell("field_header", fh_r, pairs[2][0]), _cell("field_value", fv_r, pairs[2][1])],
    ]


def build_grid_for_table(obj: dict) -> dict[str, Any]:
    rid = obj.get("role_id") or ""
    oid = obj.get("object_id") or obj.get("block_id") or f"tbl_{obj.get('table_index')}"
    mp = obj.get("main_point") or ""
    locale = infer_locale(obj=obj)
    by_pos = _bindings(obj)
    fields = _split_fields(mp)
    rid_l = rid.lower()
    mp_l = mp.lower()

    if _is_label_value_form(by_pos):
        pattern, grid = "label_value", _cover_grid(
            by_pos, fields, str(oid), mp, locale=locale, role_id=str(rid),
            theme=str(obj.get("theme") or ""),
        )
    elif "field_header" in by_pos and "field_value" in by_pos:
        pattern, grid = "personnel_card", _field_pair_personnel(
            by_pos, fields, locale=locale,
        )
    elif "Personnel" in rid or "Roster" in rid:
        pattern, grid = "personnel", _personnel_grid(
            by_pos, fields, str(oid), locale=locale,
        )
    elif "header" in by_pos:
        if "MajorProject" in rid or "project_name" in by_pos:
            pattern, grid = "project", _project_or_matrix_grid(
                by_pos, fields, str(oid), mp, kind="project", locale=locale,
            )
        elif "Responsibility" in rid or "task" in by_pos:
            pattern, grid = "matrix", _project_or_matrix_grid(
                by_pos, fields, str(oid), mp, kind="matrix", locale=locale,
            )
        elif "Appendix" in rid or "清单" in mp or "日程" in mp or "schedule" in mp_l or "checklist" in mp_l:
            pattern, grid = "appendix", _stat_grid(
                by_pos, fields, str(oid), mp, locale=locale,
            )
        else:
            pattern, grid = "stat", _stat_grid(
                by_pos, fields, str(oid), mp, locale=locale,
            )
    else:
        pattern = "generic_header_row"
        cols = _body_column_order(
            by_pos, fields=fields, n_cols=len(fields) if fields else None,
        ) if by_pos else [
            ("stub", "ParaTblDataCell"),
            ("data", "ParaTblDataCell"),
        ]
        if fields and not by_pos:
            while len(cols) < len(fields):
                cols.append(("data", "ParaTblDataCell"))
            cols = cols[: len(fields)]
        syn = {"header": "ParaTblHeaderCell", **{p: r for p, r in cols}}
        pattern_kind = "project" if "project" in rid_l else "data"
        grid = _header_row_table(syn, fields, str(oid), kind=pattern_kind, locale=locale)

    # Hard check: created grid is rectangular — rows×cols == content shape
    if grid:
        ncols = len(grid[0])
        for ri, row in enumerate(grid):
            if len(row) != ncols:
                raise ValueError(
                    f"table grid not rectangular for {rid}/{oid}: "
                    f"row0 has {ncols} cols, row{ri} has {len(row)}"
                )

    return {
        "object_id": oid,
        "role_id": rid,
        "table_index": obj.get("table_index"),
        "pattern": pattern,
        "main_point": mp,
        "locale": locale,
        "rows": len(grid),
        "cols": len(grid[0]) if grid else 0,
        "grid": grid,
    }


def build_table_samples(seed_dir: Path) -> dict[str, Any]:
    seed_dir = seed_dir.resolve()
    outline = load_json(seed_dir / "outline.json")
    locale = infer_locale(seed_dir=seed_dir)
    theme = ""
    for name in ("plan.json", "template.json", "meta.json", "brief.json"):
        path = seed_dir / name
        if not path.exists():
            continue
        try:
            data = load_json(path)
        except Exception:  # noqa: BLE001
            continue
        theme = (
            str((data.get("meta") or {}).get("theme") or data.get("theme") or "").strip()
        )
        if theme:
            break
    out: dict[str, Any] = {}
    for obj in outline.get("objects") or []:
        if obj.get("kind") != "table":
            continue
        payload = dict(obj)
        payload.setdefault("locale", locale)
        if theme:
            payload.setdefault("theme", theme)
        sample = build_grid_for_table(payload)
        out[sample["object_id"]] = sample
    dump_json(seed_dir / "tables.json", out)
    print(f"[tables] {seed_dir / 'tables.json'} ({len(out)} tables, locale={locale})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build distinctive table sample grids for a seed")
    ap.add_argument("--seed-dir", required=True)
    args = ap.parse_args()
    build_table_samples(Path(args.seed_dir))


if __name__ == "__main__":
    main()
