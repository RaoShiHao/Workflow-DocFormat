"""Step4 polish phase (2/3): re-apply table & image chrome from style_tree onto docx.

Paragraphs (including in-cell Para*Cell) follow Word named styles; Tbl* chrome
and images were written as **direct** officecli props, so human-visible chrome
updates in style_tree must be pushed back with set_props (without rewriting cell text).

Usage:
  python LongDocFmtEval/pipeline_auto/seed/reapply_tables_images.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/work/step3_templates/01_five_year_plan_outline_zh__theme1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.officecli_runner import close_docx, get_json, set_props  # noqa: E402
from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import (  # noqa: E402
    _chrome_props,
    _finalize_cell_chrome,
    _table_is_open,
    apply_cell_chrome_and_paragraph_style,
    apply_table_repeat_header,
)
from LongDocFmtEval.pipeline_auto.seed.table_chrome_presets import (  # noqa: E402
    assign_declared_cell_styles,
    chrome_for_declared_style,
    llm_cells_chrome,
)
from LongDocFmtEval.pipeline_auto.seed.extract_style_tree import list_inline_pictures  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: E402
    load_json,
    table_format_of,
)


def _first_format(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or payload
    results = data.get("results") or []
    if not results or not isinstance(results[0], dict):
        return {}
    return dict(results[0].get("format") or {})


def _table_dims(doc: Path, idx: int) -> tuple[int, int]:
    """Return (rows, cols) for /body/table[idx]."""
    for path in (f"/body/table[{idx}]", f"/body/tbl[{idx}]"):
        try:
            fmt = _first_format(get_json(doc, path))
            rows = int(fmt.get("rows") or fmt.get("_rows") or 0)
            cols = int(fmt.get("cols") or fmt.get("_gridCols") or 0)
            if rows and cols:
                return rows, cols
        except Exception:  # noqa: BLE001
            continue
    return 0, 0


def _grids_by_table_index(tables_data: dict) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for _oid, spec in (tables_data or {}).items():
        if not isinstance(spec, dict):
            continue
        try:
            ti = int(spec.get("table_index") or 0)
        except (TypeError, ValueError):
            continue
        if ti > 0:
            out[ti] = spec
    return out


def _apply_one_table(
    doc: Path,
    *,
    table_index: int,
    tbl_role: str,
    tbl_entry: dict,
    roles: dict,
    grid_spec: dict | None,
) -> tuple[int, int]:
    """Returns (table_ok, cells_touched)."""
    tf = table_format_of(tbl_entry)
    chrome_by_pos = llm_cells_chrome(tbl_entry)

    open_tbl = _table_is_open(tf)
    tbl_path = f"/body/table[{table_index}]"

    tf_set = {
        k: v for k, v in tf.items()
        if k != "repeat_header" and v not in (None, "", "none")
    }
    if open_tbl or str(tf.get("border.all") or "").lower() in ("none", "nil", "0"):
        tf_set = {
            k: v for k, v in tf_set.items()
            if k not in ("border.top", "border.bottom", "border.left", "border.right")
        }
        if "border.all" not in tf_set:
            tf_set["border.all"] = "none"
    if tf_set:
        try:
            set_props(doc, tbl_path, tf_set)
        except RuntimeError:
            soft = {
                k: v for k, v in tf_set.items()
                if k in ("align", "width", "layout") or str(k).startswith("border")
            }
            if soft:
                try:
                    set_props(doc, tbl_path, soft)
                except RuntimeError as exc:
                    print(f"  [warn] table[{table_index}] {tbl_role} structure: {exc}")
                    return 0, 0

    grid = (grid_spec or {}).get("grid") if isinstance(grid_spec, dict) else None
    apply_table_repeat_header(doc, tbl_path, tf, grid if isinstance(grid, list) else None)
    cells_ok = 0
    if isinstance(grid, list) and grid:
        declared = assign_declared_cell_styles(grid, tbl_entry)
        for ri, row in enumerate(declared, start=1):
            for ci, (cell_style, para_rid, _text) in enumerate(row, start=1):
                chrome = _chrome_props(
                    _finalize_cell_chrome(
                        chrome_for_declared_style(chrome_by_pos, cell_style),
                        table_open=open_tbl,
                    )
                )
                if not chrome and not para_rid:
                    continue
                try:
                    apply_cell_chrome_and_paragraph_style(
                        doc, tbl=tbl_path, ri=ri, ci=ci,
                        chrome=chrome, para_rid=para_rid, text=None,
                    )
                    cells_ok += 1
                except RuntimeError as exc:
                    print(f"  [warn] table[{table_index}] cell[{ri},{ci}]: {exc}")
        return 1, cells_ok

    nrows, ncols = _table_dims(doc, table_index)
    if not nrows or not ncols:
        return 1, 0
    # No tables.json grid: synthesize (pos, para, "") then resolve via cell_style_plan.
    synth: list[list[tuple[str, str, str]]] = []
    for ri in range(nrows):
        row: list[tuple[str, str, str]] = []
        for ci in range(ncols):
            pos = (
                "header" if ri == 0 and "header" in chrome_by_pos
                else ("stub" if ci == 0 and "stub" in chrome_by_pos else "data")
            )
            if pos not in chrome_by_pos and "data" in chrome_by_pos:
                pos = "data"
            row.append((pos, "", ""))
        synth.append(row)
    declared = assign_declared_cell_styles(synth, tbl_entry)
    for ri, row in enumerate(declared, start=1):
        for ci, (cell_style, _para, _text) in enumerate(row, start=1):
            chrome = _chrome_props(
                _finalize_cell_chrome(
                    chrome_for_declared_style(chrome_by_pos, cell_style),
                    table_open=open_tbl,
                )
            )
            if not chrome:
                continue
            try:
                set_props(doc, f"{tbl_path}/row[{ri}]/cell[{ci}]", chrome)
                cells_ok += 1
            except RuntimeError:
                pass
    return 1, cells_ok


def _apply_images(doc: Path, styles: dict, roles: dict) -> int:
    n = 0
    img_locs = styles.get("image") or {}
    max_idx = 0
    for idx in img_locs.values():
        try:
            max_idx = max(max_idx, int(idx))
        except (TypeError, ValueError):
            continue
    pictures = list_inline_pictures(doc, with_paths=True, max_needed=max_idx) if img_locs else []
    for rid, idx in img_locs.items():
        try:
            ii = int(idx)
        except (TypeError, ValueError):
            continue
        spec = roles.get(rid) or {}
        props = dict(spec.get("props") or {})
        payload = {}
        for k in ("width", "height", "hAlign", "wrap", "alt"):
            if props.get(k) not in (None, "", "none"):
                payload[k] = props[k]
        if not payload:
            continue
        pic = pictures[ii - 1] if 1 <= ii <= len(pictures) else {}
        paths = []
        if pic.get("path"):
            paths.append(str(pic["path"]))
        paths.extend((
            f"/body/image[{ii}]",
            f"/body/drawing[{ii}]",
            f"/body/p/image[{ii}]",
        ))
        applied = False
        for path in paths:
            try:
                set_props(doc, path, payload)
                applied = True
                break
            except RuntimeError:
                # hAlign/wrap may be unsupported on picture runs; size still matters
                size_only = {k: payload[k] for k in ("width", "height", "alt") if k in payload}
                if size_only and size_only != payload:
                    try:
                        set_props(doc, path, size_only)
                        applied = True
                        break
                    except RuntimeError:
                        continue
                continue
        if applied:
            n += 1
        else:
            print(f"  [warn] image {rid}#{ii}: set failed on known paths")
    return n


def reapply_tables_images(
    seed_dir: Path,
    *,
    docx_name: str = "template.docx",
) -> dict[str, int]:
    seed_dir = seed_dir.resolve()
    doc = seed_dir / docx_name
    if not doc.is_file():
        raise FileNotFoundError(doc)

    styles = load_json(seed_dir / "styles.json") if (seed_dir / "styles.json").exists() else {}
    tree = load_json(seed_dir / "style_tree.json")
    roles = tree.get("roles") or {}
    tables_data = load_json(seed_dir / "tables.json") if (seed_dir / "tables.json").exists() else {}
    by_idx = _grids_by_table_index(tables_data)

    print(f"[reapply] tables/images ← style_tree → {doc.name}")
    # Only touch tables that styles.json says exist in this docx
    live = set()
    for idx in (styles.get("table_structure") or {}).values():
        try:
            live.add(int(idx))
        except (TypeError, ValueError):
            continue
    tbl_n = cell_n = 0
    for rid, idx in (styles.get("table_structure") or {}).items():
        try:
            ti = int(idx)
        except (TypeError, ValueError):
            continue
        if live and ti not in live:
            continue
        entry = roles.get(rid) or {"object": "table"}
        grid_spec = by_idx.get(ti)
        # Prefer matching role_id in tables.json if index collision
        if grid_spec and grid_spec.get("role_id") and grid_spec.get("role_id") != rid:
            for spec in by_idx.values():
                if spec.get("role_id") == rid and int(spec.get("table_index") or 0) == ti:
                    grid_spec = spec
                    break
        ok, cells = _apply_one_table(
            doc, table_index=ti, tbl_role=rid, tbl_entry=entry, roles=roles, grid_spec=grid_spec,
        )
        tbl_n += ok
        cell_n += cells
        print(f"  · {rid} table[{ti}] cells_updated={cells}")

    img_n = _apply_images(doc, styles, roles)
    print(f"[reapply] done tables={tbl_n} cells={cell_n} images={img_n}")
    close_docx(doc)
    return {"tables": tbl_n, "cells": cell_n, "images": img_n}


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-apply table/image format from style_tree onto docx")
    ap.add_argument("--seed-dir", required=True)
    ap.add_argument("--docx", default="template.docx", help="docx filename inside seed-dir")
    args = ap.parse_args()
    reapply_tables_images(Path(args.seed_dir), docx_name=args.docx)


if __name__ == "__main__":
    main()
