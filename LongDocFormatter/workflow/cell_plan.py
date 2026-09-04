"""Recover AutoDataBuild cell_style slots + plan from a template table.

Forward (dataset): Tbl*.cells is keyed by designed slots (header / data / label /
value / stub / row_last), then a cell_style_plan paints every physical cell.
Extra rows/columns clone the repeating body slot — they are not new styles.

Inverse: cluster Tbl* by table_format + those slot bags, not by the specimen's
r1_cN grid. Assignment (LLM) labels source cells into that Tbl*'s slots; apply
paints from those labels. cell_style_plan is not a source-cell classifier.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

_PHYSICAL_SLOT = re.compile(r"^r\d+_c\d+$", re.I)

from LongDocFormatter.workflow.whitelist import CELL_KEYS

_NOTE = frozenset({"note", "remark", "source", "footnote"})
_SLOT_ORDER = ("header", "label", "value", "stub", "data", "row_last", "row_second")


def chrome_of(cell: dict[str, Any] | None) -> dict[str, Any]:
    raw = (cell or {}).get("chrome") if isinstance(cell, dict) else {}
    if not isinstance(raw, dict):
        raw = cell if isinstance(cell, dict) else {}
    return {k: v for k, v in raw.items() if k in CELL_KEYS and v not in (None, "")}


def chrome_sig(chrome: dict[str, Any] | None) -> str:
    blob = {k: chrome[k] for k in sorted(chrome or {}) if k in CELL_KEYS}
    return json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _grid(cells: Iterable[dict[str, Any]]) -> tuple[dict[tuple[int, int], dict], int, int]:
    grid: dict[tuple[int, int], dict] = {}
    max_r = max_c = 0
    for cell in cells or []:
        if not isinstance(cell, dict):
            continue
        row, col = _as_int(cell.get("row")), _as_int(cell.get("col"))
        if row is None or col is None:
            continue
        grid[(row, col)] = cell
        max_r, max_c = max(max_r, row), max(max_c, col)
    return grid, max_r, max_c


def _majority(values: list[str]) -> str:
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]


def _repeat_last(styles: list[str], index: int) -> str:
    if not styles:
        return "data"
    if index < len(styles):
        return styles[index]
    repeat = styles[-1]
    for key in styles:
        if key not in _NOTE:
            repeat = key
    return repeat


def resolve_cell_slot(
    plan: dict[str, Any] | None,
    row: int | None,
    col: int | None,
    n_rows: int | None = None,
    n_cols: int | None = None,
) -> str:
    """Physical (row, col) → designed cell_style (same rules as seed render)."""
    del n_cols
    plan = plan if isinstance(plan, dict) else {}
    if row == 1 and plan.get("header_row"):
        return "header"
    if (
        plan.get("row_last")
        and n_rows
        and row == n_rows
        and n_rows > 1
    ):
        return "row_last"
    mode = str(plan.get("mode") or "row").lower()
    if mode in {"label_value", "column"}:
        styles = [str(x) for x in (plan.get("column_styles") or []) if x]
        if mode == "label_value" and not styles:
            styles = ["label", "value"]
        if styles and col is not None:
            return _repeat_last(styles, col - 1)
    if mode == "row":
        styles = [str(x) for x in (plan.get("row_styles") or []) if x]
        if styles and row is not None:
            return _repeat_last(styles, row - 1)
    if row == 1:
        return "header"
    return "data"


def infer_table_style(
    row: dict[str, Any],
    *,
    cell_para_sids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return {cells, cell_style_plan, cell_paragraphs} from a template table."""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    physical = list(meta.get("cells") or [])
    grid, n_rows, n_cols = _grid(physical)
    loc_to_sid = cell_para_sids or {}
    tbl_id = row.get("location_id")

    def para_of(cell: dict) -> str:
        r, c = cell.get("row"), cell.get("col")
        loc = f"{tbl_id}:{r}:{c}"
        return str(loc_to_sid.get(loc) or loc_to_sid.get(str(loc)) or "")

    def para_at(r: int, c: int) -> str:
        hit = grid.get((r, c))
        return para_of(hit) if hit else ""

    plan = _infer_plan(grid, n_rows, n_cols, para_at=para_at)
    slots: dict[str, dict[str, Any]] = {}
    para_votes: dict[str, list[str]] = {}
    for (r, c), cell in grid.items():
        slot = resolve_cell_slot(plan, r, c, n_rows, n_cols)
        chrome = chrome_of(cell)
        if chrome and slot not in slots:
            slots[slot] = chrome
        elif chrome and slot in slots:
            # first exemplar cell for the slot wins; majority chrome if we want later
            pass
        sid = para_of(cell)
        if sid:
            para_votes.setdefault(slot, []).append(sid)

    # Majority chrome per slot (overwrite first-seen if needed)
    chrome_votes: dict[str, list[str]] = {}
    chrome_by_sig: dict[str, dict[str, Any]] = {}
    for (r, c), cell in grid.items():
        slot = resolve_cell_slot(plan, r, c, n_rows, n_cols)
        chrome = chrome_of(cell)
        if not chrome:
            continue
        sig = chrome_sig(chrome)
        chrome_by_sig[sig] = chrome
        chrome_votes.setdefault(slot, []).append(sig)
    for slot, sigs in chrome_votes.items():
        slots[slot] = chrome_by_sig[_majority(sigs)]

    if not slots:
        raw = (row.get("props") or {}).get("cells") if isinstance((row.get("props") or {}).get("cells"), dict) else {}
        for name in _SLOT_ORDER:
            if isinstance(raw.get(name), dict):
                cleaned = chrome_of({"chrome": raw[name]})
                if cleaned:
                    slots[name] = cleaned

    cell_paras = {slot: _majority(votes) for slot, votes in para_votes.items() if _majority(votes)}
    return {
        "cells": slots,
        "cell_style_plan": plan,
        "cell_paragraphs": cell_paras,
    }


def _infer_plan(
    grid: dict[tuple[int, int], dict],
    n_rows: int,
    n_cols: int,
    *,
    para_at=None,
) -> dict[str, Any]:
    if n_rows <= 0 or n_cols <= 0:
        return {"mode": "row", "header_row": True, "row_styles": ["header", "data"]}

    def sig_at(r: int, c: int) -> str:
        return chrome_sig(chrome_of(grid.get((r, c)) or {}))

    def para_sig(r: int, c: int) -> str:
        if para_at is None:
            return ""
        return str(para_at(r, c) or "")

    row1 = [sig_at(1, c) for c in range(1, n_cols + 1)]
    header_uniform = bool(row1) and len(set(row1)) == 1
    body_rows = list(range(2, n_rows + 1)) if n_rows > 1 else []

    def col_body(c: int) -> str:
        vals = [sig_at(r, c) for r in body_rows if sig_at(r, c)]
        return _majority(vals) if vals else sig_at(1, c)

    # Cover / key-value: two columns, left ≠ right on the body (or whole table).
    if n_cols == 2:
        left = [sig_at(r, 1) for r in (body_rows or [1]) if sig_at(r, 1)]
        right = [sig_at(r, 2) for r in (body_rows or [1]) if sig_at(r, 2)]
        left_m, right_m = _majority(left), _majority(right)
        pair_like = bool(left_m and right_m and left_m != right_m)
        if pair_like:
            header_row = header_uniform and row1[0] not in {left_m, right_m}
            if not header_row:
                return {"mode": "label_value", "header_row": False, "column_styles": ["label", "value"]}
            return {
                "mode": "column",
                "header_row": True,
                "column_styles": ["label", "value"],
            }

    header_row = header_uniform and n_rows > 1
    body_col_sigs = [col_body(c) for c in range(1, n_cols + 1)] if body_rows else list(row1)
    unique_body = [s for s in body_col_sigs if s]

    def col_para(c: int) -> str:
        vals = [para_sig(r, c) for r in body_rows if para_sig(r, c)]
        return _majority(vals) if vals else para_sig(1, c)

    body_col_paras = [col_para(c) for c in range(1, n_cols + 1)] if body_rows else []
    stub_split = n_cols >= 2 and (
        (
            body_col_sigs
            and body_col_sigs[0]
            and any(s != body_col_sigs[0] for s in body_col_sigs[1:])
        )
        or (
            body_col_paras
            and body_col_paras[0]
            and any(s != body_col_paras[0] for s in body_col_paras[1:] if s)
        )
    )

    last_row_sigs = [sig_at(n_rows, c) for c in range(1, n_cols + 1)] if n_rows > 2 else []
    last_uniform = bool(last_row_sigs) and len(set(last_row_sigs)) == 1
    body_common = _majority([s for s in body_col_sigs[1:] if s] or body_col_sigs)
    row_last = bool(last_uniform and last_row_sigs[0] and last_row_sigs[0] != body_common)

    if stub_split:
        styles = ["stub"]
        rest_m = _majority(body_col_sigs[1:])
        for sig in body_col_sigs[1:]:
            styles.append("data" if sig == rest_m or not sig else "data")
        # If a non-stub body column has a distinct chrome, keep it as data
        # (one repeating body slot — extra columns clone it).
        plan = {"mode": "column", "header_row": header_row, "column_styles": styles}
        if row_last:
            plan["row_last"] = True
        return plan

    if header_row:
        plan: dict[str, Any] = {
            "mode": "row",
            "header_row": True,
            "row_styles": ["header"] + ["data"] * max(0, n_rows - 1),
        }
        if row_last:
            plan["row_last"] = True
        return plan

    if n_cols >= 2 and unique_body and len(set(unique_body)) == 1:
        return {"mode": "row", "header_row": False, "row_styles": ["data"] * n_rows}

    return {"mode": "row", "header_row": header_row or n_rows > 1, "row_styles": ["header", "data"]}


def table_role_signature(
    table_format: dict[str, Any],
    slots: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Identity of a designed Tbl* — no specimen width/height."""
    plan = plan if isinstance(plan, dict) else {}
    styles = [str(x) for x in (plan.get("column_styles") or []) if x]
    return {
        "table_format": table_format or {},
        "slots": {k: slots[k] for k in _SLOT_ORDER if k in slots},
        "plan": {
            "header_row": bool(plan.get("header_row")),
            "mode": str(plan.get("mode") or ""),
            "has_stub": "stub" in styles,
            "has_label": "label" in styles or str(plan.get("mode")) == "label_value",
            "row_last": bool(plan.get("row_last")),
        },
    }


def _named_slot(name: Any) -> str:
    raw = str(name or "").strip()
    if not raw or _PHYSICAL_SLOT.match(raw):
        return ""
    return raw


def designed_slots(spec: dict[str, Any] | None) -> set[str]:
    spec = spec if isinstance(spec, dict) else {}
    keys = list((spec.get("cells") or {}).keys()) + list((spec.get("cell_paragraphs") or {}).keys())
    plan = spec.get("cell_style_plan") if isinstance(spec.get("cell_style_plan"), dict) else {}
    keys += list(plan.get("column_styles") or []) + list(plan.get("row_styles") or [])
    if plan.get("header_row"):
        keys.append("header")
    if plan.get("row_last"):
        keys.append("row_last")
    if str(plan.get("mode") or "") == "label_value":
        keys.extend(("label", "value"))
    return {s for k in keys if (s := _named_slot(k))}


def coerce_slot(name: Any, allowed: set[str] | None) -> str:
    """Map a free-form label onto the cell-styles declared on this Tbl*."""
    allowed = {s for x in (allowed or ()) if (s := _named_slot(x))}
    raw = str(name or "").strip()
    raw_l = raw.lower()
    if raw in allowed:
        return raw
    if raw_l in allowed:
        return raw_l
    aliases = {
        "head": "header",
        "heading": "header",
        "body": "data",
        "cell": "data",
        "val": "value",
        "lab": "label",
        "rowname": "stub",
        "first_col": "stub",
    }
    mapped = aliases.get(raw_l, raw_l)
    if mapped in allowed:
        return mapped
    fallbacks = {
        "value": ("data", "value"),
        "data": ("data", "value"),
        "label": ("label", "stub"),
        "stub": ("stub", "label"),
        "header": ("header", "label"),
        "note": ("note", "data"),
    }
    for cand in fallbacks.get(mapped, ()):
        if cand in allowed:
            return cand
    if "data" in allowed:
        return "data"
    if allowed:
        return sorted(allowed)[0]
    return mapped or "data"


def classify_cells(cells: list[dict[str, Any]] | None, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Phase-2: every physical cell → a cell-style declared on this table-style."""
    spec = spec if isinstance(spec, dict) else {}
    plan = spec.get("cell_style_plan") if isinstance(spec.get("cell_style_plan"), dict) else {}
    para_slots = spec.get("cell_paragraphs") if isinstance(spec.get("cell_paragraphs"), dict) else {}
    allowed = designed_slots(spec)
    rows: list[dict[str, Any]] = [c for c in (cells or []) if isinstance(c, dict)]
    n_rows = max((_as_int(c.get("row")) or 0) for c in rows) if rows else 0
    n_cols = max((_as_int(c.get("col")) or 0) for c in rows) if rows else 0
    out: list[dict[str, Any]] = []
    for cell in rows:
        row, col = _as_int(cell.get("row")), _as_int(cell.get("col"))
        if row is None or col is None:
            continue
        slot = coerce_slot(resolve_cell_slot(plan, row, col, n_rows, n_cols), allowed)
        para = str(para_slots.get(slot) or "").strip()
        out.append({"row": row, "col": col, "cell_style": slot, "paragraph_style": para})
    return out


def index_cell_para_styles(cell_paras: list, declarations: dict[str, Any] | None) -> dict[str, str]:
    """Map template cell-para location_id → Para*Cell style_id by prop overlap."""
    specs: list[tuple[str, dict[str, Any]]] = []
    for sid, spec in (declarations or {}).items():
        if str((spec or {}).get("object") or "") != "paragraph.table_cell":
            continue
        bag = spec.get("props") if isinstance(spec.get("props"), dict) else {}
        specs.append((str(sid), bag))
    out: dict[str, str] = {}
    for el in cell_paras or []:
        props = getattr(el, "props", None)
        if props is None and isinstance(el, dict):
            props = el.get("props") or {}
        loc = getattr(el, "location_id", None)
        if loc is None and isinstance(el, dict):
            loc = el.get("location_id")
        if not props or loc is None:
            continue
        best, best_n = "", -1
        for sid, bag in specs:
            n = 0
            for key, val in bag.items():
                if val in (None, "", "none"):
                    continue
                have = props.get(key)
                if have in (None, "", "none"):
                    continue
                if str(have).strip().lower() == str(val).strip().lower():
                    n += 1
            if n > best_n:
                best, best_n = sid, n
        if best and best_n > 0:
            out[str(loc)] = best
    return out
