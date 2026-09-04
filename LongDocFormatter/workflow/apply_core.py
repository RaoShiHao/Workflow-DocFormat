"""Shared apply engine: compile equivalent ops, then officecli batch.

Used by both ``LongDocFormatter.workflow`` and the portable skill
(``scripts/lib/apply_core.py`` — keep the two files in sync).

Compile is pure CPU. Execute is: merge same-path sets → skip no-ops already
applied at compile time → ``officecli batch --best-effort`` in chunks → retry
failed items → sequential fallback. Resident session: one ``open``, one
``close`` (no mid-chunk ``save``).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from LongDocFormatter.workflow.cell_plan import coerce_slot, designed_slots, resolve_cell_slot

DEFAULT_CHUNK = 150

_INSTANCE_PARA_KEYS = {
    "listStyle", "numId", "numLevel", "indent",
    "keepNext", "pageBreakBefore", "outlineLvl",
}
_STYLE_SOFT_KEYS = {
    "id", "name", "type", "basedOn",
    "font.ea", "font.latin", "font", "size", "bold", "italic",
    "align", "spaceBefore", "spaceAfter", "lineSpacing",
    "outlineLvl", "color", "firstLineIndent", "hangingIndent",
    "keepNext", "pageBreakBefore",
}
_PARAGRAPH_STYLE_SKIP = {"listStyle", "numId", "numLevel", "indent"}
_HEADER_FOOTER_KEYS = {
    "text", "field", "align", "size", "color", "bold", "italic",
    "type", "direction", "font",
}
_CELL_KEYS = {
    "valign", "fill", "shading",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
}
_TABLE_FORMAT_KEYS = {
    "align", "width", "layout", "repeat_header",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
}
_IMAGE_KEYS = {"width", "height", "hAlign"}
_SECTION_KEYS = {
    "marginTop", "marginBottom", "marginLeft", "marginRight",
    "marginHeader", "marginFooter", "orientation", "columns",
    "pgBorders", "type", "pageNumFmt", "pageStart", "titlePage",
}
_RUN_KEYS = {
    "bold", "italic", "superscript", "subscript", "caps", "smallcaps",
    "color", "underline", "font.ea", "font.latin", "size",
}
_PARA_LAYERS = ("paragraph.body", "paragraph.table_cell")


# ---------------------------------------------------------------------------
# officecli backend: skill lib vs repo runner (same CLI, same encode rules)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_SKILL_LOCAL = _HERE.parent.name == "lib" and (_HERE.parent / "officecli.py").is_file()


def _load_backend() -> dict[str, Any]:
    if _SKILL_LOCAL:
        from lib.officecli import (  # type: ignore
            OfficeCliError,
            add_props,
            batch_commands,
            batch_unsupported,
            encode_props,
            close_doc,
            open_doc,
            remove_path,
            set_props,
        )

        def set_find(doc: Path, path: str, props: dict[str, Any], find: str) -> None:
            set_props(doc, path, props, find=find)

        return {
            "Error": OfficeCliError,
            "add_props": add_props,
            "batch_commands": batch_commands,
            "batch_unsupported": batch_unsupported,
            "encode_props": encode_props,
            "close_doc": close_doc,
            "open_doc": open_doc,
            "remove_path": remove_path,
            "set_props": set_props,
            "set_find": set_find,
        }

    from LongDocFormatter.officecli.runner import (
        add_props,
        batch_commands,
        batch_unsupported,
        encode_props,
        run_officecli,
        set_props,
    )

    def remove_path(doc: Path, path: str) -> None:
        run_officecli(["remove", str(doc), path], check=False)

    def open_doc(doc: Path) -> None:
        run_officecli(["open", str(doc)], check=False)

    def close_doc(doc: Path) -> None:
        run_officecli(["close", str(doc)], check=False)

    def set_find(doc: Path, path: str, props: dict[str, Any], find: str) -> None:
        args = ["set", str(doc), path, "--find", find]
        for key, value in encode_props(props).items():
            args.extend(["--prop", f"{key}={value}"])
        if len(args) > 5:
            run_officecli(args)

    return {
        "Error": RuntimeError,
        "add_props": add_props,
        "batch_commands": batch_commands,
        "batch_unsupported": batch_unsupported,
        "encode_props": encode_props,
        "close_doc": close_doc,
        "open_doc": open_doc,
        "remove_path": remove_path,
        "set_props": set_props,
        "set_find": set_find,
    }


_B = _load_backend()
_encode_props: Callable = _B["encode_props"]
_batch_commands: Callable = _B["batch_commands"]
_batch_unsupported: Callable = _B["batch_unsupported"]
_add_props: Callable = _B["add_props"]
_set_props: Callable = _B["set_props"]
_remove_path: Callable = _B["remove_path"]
_open_doc: Callable = _B["open_doc"]
_close_doc: Callable = _B["close_doc"]
_set_find: Callable = _B["set_find"]
_Error = _B["Error"]


# ---------------------------------------------------------------------------
# Op constructors
# ---------------------------------------------------------------------------

def public_cmd(cmd: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in cmd.items() if not str(k).startswith("_")}


def set_cmd(path: str, props: dict[str, Any], **meta: Any) -> dict[str, Any] | None:
    encoded = _encode_props(props)
    if not encoded:
        return None
    out: dict[str, Any] = {"command": "set", "path": path, "props": encoded}
    out.update(meta)
    return out


def add_cmd(parent: str, typ: str, props: dict[str, Any], **meta: Any) -> dict[str, Any] | None:
    encoded = _encode_props(props)
    if not encoded:
        return None
    out: dict[str, Any] = {"command": "add", "parent": parent, "type": typ, "props": encoded}
    out.update(meta)
    return out


def remove_cmd(path: str, **meta: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"command": "remove", "path": path}
    out.update(meta)
    return out


# ---------------------------------------------------------------------------
# Compile helpers
# ---------------------------------------------------------------------------

def _bag(spec: dict | None, layer: str) -> dict[str, Any]:
    spec = spec or {}
    if layer == "table":
        return {}
    if isinstance(spec.get("props"), dict):
        return dict(spec["props"])
    return {k: v for k, v in spec.items() if k != "object"}


def _index(inventory: dict[str, list[dict]]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for layer, rows in inventory.items():
        for row in rows:
            out[(str(layer), str(row.get("location_id")))] = row
    return out


def _norm_val(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip()
    sl = s.lower()
    if sl in {"true", "false"}:
        return sl
    if s.startswith("#") and len(s) in (4, 7, 9):
        return s[1:].lower()
    if sl.endswith("pt"):
        return sl[:-2]
    return sl


def already_set(current: dict | None, target: dict | None) -> bool:
    if not target:
        return True
    cur = current or {}
    for key, want in target.items():
        if key == "style":
            have = cur.get("style") or cur.get("styleId") or cur.get("styleName")
        else:
            have = cur.get(key)
        if have is None and str(key).startswith("border."):
            have = cur.get("border.all")
        if have is None:
            return False
        if _norm_val(have) != _norm_val(want):
            return False
    return True


def _coerce_outline(value: Any) -> int | None:
    if value in (None, "", "none"):
        return None
    try:
        lvl = int(value)
    except (TypeError, ValueError):
        return None
    return lvl if 0 <= lvl <= 9 else None


def _style_payload(style_id: str, display_name: str, props: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": style_id,
        "name": display_name or style_id,
        "type": "paragraph",
        "basedOn": "Normal",
    }
    for k, v in (props or {}).items():
        if v in (None, "", "none") or k in _PARAGRAPH_STYLE_SKIP:
            continue
        payload[k] = v
    ol = _coerce_outline(payload.get("outlineLvl"))
    if ol is not None:
        payload["outlineLvl"] = ol
    return payload


def _hf_writable(chrome: dict[str, Any] | None, *, first: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (chrome or {}).items():
        if k not in _HEADER_FOOTER_KEYS:
            continue
        if v is None or v == "none":
            continue
        if v == "" and k != "text":
            continue
        out[k] = v
    if first:
        out["type"] = "first"
    return out


def _table_is_open(tf: dict) -> bool:
    if not tf:
        return False
    if str(tf.get("border.all") or "").lower() in ("none", "nil", "0"):
        return True
    if tf.get("border.top") or tf.get("border.bottom"):
        if not tf.get("border.all") and not tf.get("border.left") and not tf.get("border.right"):
            return True
    return False


def _finalize_cell_chrome(chrome: dict, *, table_open: bool) -> dict:
    cleaned: dict[str, Any] = {}
    for k, v in (chrome or {}).items():
        if v in (None, ""):
            continue
        if k not in _CELL_KEYS:
            continue
        cleaned[k] = v
    if not table_open:
        return cleaned
    border_keys = [k for k in cleaned if str(k).startswith("border.")]
    if not border_keys:
        cleaned["border.all"] = "none"
        return cleaned
    if "border.all" in cleaned and str(cleaned["border.all"]).lower() in ("none", "nil"):
        cleaned["border.all"] = "none"
        for side in ("top", "bottom", "left", "right"):
            cleaned[f"border.{side}"] = "nil"
        return cleaned
    if "border.bottom" in cleaned and "border.all" not in cleaned:
        for side in ("top", "left", "right"):
            cleaned.setdefault(f"border.{side}", "nil")
    elif "border.top" in cleaned and "border.bottom" in cleaned and "border.all" not in cleaned:
        for side in ("left", "right"):
            cleaned.setdefault(f"border.{side}", "nil")
    return cleaned


def _repeat_header_enabled(tf: dict) -> bool | None:
    if "repeat_header" not in (tf or {}):
        return None
    v = tf.get("repeat_header")
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def _header_row_indices(grid: list) -> list[int]:
    found: list[int] = []
    if not isinstance(grid, list):
        return [1]
    for ri, row in enumerate(grid, start=1):
        if not isinstance(row, list):
            continue
        for cell in row:
            if isinstance(cell, dict):
                pos = str(cell.get("cell_style") or cell.get("pos") or "")
            else:
                continue
            pl = pos.lower()
            if "header" in pl or pl in ("head",):
                found.append(ri)
                break
    return found or [1]


def _guess_cell_paths(tbl: str, ri: int, ci: int) -> list[str]:
    return [
        f"{tbl}/row[{ri}]/cell[{ci}]",
        f"{tbl}/tr[{ri}]/tc[{ci}]",
        f"{tbl}/row[{ri}]/tc[{ci}]",
        f"{tbl}/tr[{ri}]/cell[{ci}]",
    ]


def _slot_chrome(
    chrome_by_slot: dict,
    slot: str,
    row: int | None,
    col: int | None,
    *,
    plan: dict | None = None,
    n_rows: int | None = None,
    n_cols: int | None = None,
    allowed: set[str] | None = None,
) -> dict[str, Any]:
    designed = ""
    if plan:
        designed = resolve_cell_slot(plan, row, col, n_rows, n_cols)
    name = coerce_slot(slot or designed, allowed) if (allowed or designed) else str(slot or "")
    chrome = dict(chrome_by_slot.get(name) or {})
    if not chrome and designed:
        chrome = dict(chrome_by_slot.get(designed) or {})
    if not chrome and not plan:
        if row is not None and col is not None:
            chrome = dict(chrome_by_slot.get(f"r{row}_c{col}") or {})
        if not chrome:
            if row == 1:
                chrome = dict(chrome_by_slot.get("header") or chrome_by_slot.get("label") or {})
            else:
                chrome = dict(chrome_by_slot.get("data") or chrome_by_slot.get("value") or {})
    return {k: v for k, v in chrome.items() if k in _CELL_KEYS}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

@dataclass
class CompiledOps:
    commands: list[dict[str, Any]] = field(default_factory=list)
    find_cmds: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0


def compile_ops(
    *,
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> CompiledOps:
    """Compile T/A/M + inventory into ordered officecli ops (same coverage as workflow)."""
    idx = _index(inventory)
    by_layer: dict[str, dict] = dict(loc.get("by_layer") or {})
    commands: list[dict[str, Any]] = []
    skipped = 0

    commands.extend(_compile_styles(catalog_entries, props))
    body_cmds, n_skip = _compile_paragraph_binds(by_layer, props, idx)
    commands.extend(body_cmds)
    skipped += n_skip
    commands.extend(_compile_sections(by_layer, props, idx))
    tbl_cmds, n_skip = _compile_tables(props, loc, inventory)
    commands.extend(tbl_cmds)
    skipped += n_skip
    commands.extend(_compile_images(by_layer, props, idx))
    commands = merge_same_path(commands)
    find_cmds = _compile_find_runs(props, loc, inventory)
    return CompiledOps(commands=commands, find_cmds=find_cmds, skipped=skipped)


def _compile_styles(catalog_entries: list[dict], props: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in catalog_entries:
        layer = str(entry.get("object") or "")
        if layer not in _PARA_LAYERS:
            continue
        sid = str(entry.get("style_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        spec = props.get(sid) or {}
        bag = _bag(spec, layer)
        payload = _style_payload(sid, str(entry.get("display_name") or sid), bag)
        soft = {k: v for k, v in payload.items() if k in _STYLE_SOFT_KEYS}
        commands.append(remove_cmd(f"/styles/{sid}"))
        meta: dict[str, Any] = {}
        if soft and soft != payload:
            meta["_soft_props"] = soft
        cmd = add_cmd("/styles", "style", payload, **meta)
        if cmd:
            commands.append(cmd)
    return commands


def _compile_paragraph_binds(
    by_layer: dict[str, dict],
    props: dict[str, Any],
    idx: dict[tuple[str, str], dict],
) -> tuple[list[dict[str, Any]], int]:
    """Bind body paragraphs to named styles. Cell paras are bound via table_cells."""
    mapping = by_layer.get("paragraph.body") or {}
    commands: list[dict[str, Any]] = []
    skipped = 0
    if not isinstance(mapping, dict):
        return [], 0
    for loc_id, sid in mapping.items():
        row = idx.get(("paragraph.body", str(loc_id)))
        if not row or not row.get("path"):
            skipped += 1
            continue
        spec = props.get(str(sid)) or {}
        bag = _bag(spec, "paragraph.body")
        extra = {k: v for k, v in bag.items() if k in _INSTANCE_PARA_KEYS and v not in (None, "", "none")}
        ol = _coerce_outline(extra.get("outlineLvl"))
        if ol is not None:
            extra["outlineLvl"] = ol
        merged = {"style": str(sid), **extra}
        current = row.get("props") if isinstance(row.get("props"), dict) else {}
        if already_set(current, merged):
            skipped += 1
            continue
        cmd = set_cmd(str(row["path"]), merged)
        if cmd:
            commands.append(cmd)
    return commands, skipped


def _compile_sections(
    by_layer: dict[str, dict],
    props: dict[str, Any],
    idx: dict[tuple[str, str], dict],
) -> list[dict[str, Any]]:
    mapping = by_layer.get("section") or {}
    commands: list[dict[str, Any]] = []
    if not isinstance(mapping, dict):
        return []
    for loc_id, sid in mapping.items():
        row = idx.get(("section", str(loc_id)))
        spec = props.get(str(sid)) or {}
        if not row or not row.get("path"):
            continue
        path = str(row["path"])
        bag = {k: v for k, v in _bag(spec, "section").items() if k in _SECTION_KEYS and v not in (None, "", "none")}
        current = row.get("props") if isinstance(row.get("props"), dict) else {}
        if bag:
            soft = {k: v for k, v in bag.items() if k != "pgBorders"}
            if soft and not already_set(current, soft):
                cmd = set_cmd(path, soft)
                if cmd:
                    commands.append(cmd)
            if bag.get("pgBorders") not in (None, "", "none"):
                cmd = set_cmd(path, {"pgBorders": bag["pgBorders"]}, _no_merge=True)
                if cmd:
                    commands.append(cmd)
        header = spec.get("header") if isinstance(spec.get("header"), dict) else None
        footer = spec.get("footer") if isinstance(spec.get("footer"), dict) else None
        header_first = spec.get("header_first") if isinstance(spec.get("header_first"), dict) else None
        for chrome, typ in ((header, "header"), (footer, "footer")):
            writable = _hf_writable(chrome)
            if not writable:
                continue
            cmd = add_cmd(path, typ, writable)
            if cmd:
                commands.append(cmd)
        title_page = bag.get("titlePage") if bag else None
        if header_first or str(title_page).lower() in {"true", "1"}:
            first_chrome = dict(header_first or {})
            if not first_chrome:
                src = header or footer or {}
                first_chrome = {k: src[k] for k in ("align", "size", "color") if k in src}
            writable = _hf_writable(first_chrome, first=True)
            if writable:
                cmd = add_cmd(path, "header", writable)
                if cmd:
                    commands.append(cmd)
    return commands


def _compile_tables(
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> tuple[list[dict[str, Any]], int]:
    tables = {str(r.get("location_id")): r for r in inventory.get("table") or []}
    mapping = (loc.get("by_layer") or {}).get("table") or {}
    table_cells = loc.get("table_cells") if isinstance(loc.get("table_cells"), dict) else {}
    commands: list[dict[str, Any]] = []
    skipped = 0
    if not isinstance(mapping, dict):
        return [], 0
    for loc_id, sid in mapping.items():
        tbl = tables.get(str(loc_id))
        spec = props.get(str(sid)) or {}
        if not tbl or not tbl.get("path"):
            skipped += 1
            continue
        tbl_path = str(tbl["path"])
        tf_raw = spec.get("table_format") if isinstance(spec.get("table_format"), dict) else {}
        tf_raw = dict(tf_raw or {})
        tf_set = {k: v for k, v in tf_raw.items() if k != "repeat_header" and k in _TABLE_FORMAT_KEYS and v not in (None, "")}
        current = tbl.get("props") if isinstance(tbl.get("props"), dict) else {}
        have_tf = current.get("table_format") if isinstance(current.get("table_format"), dict) else current
        if tf_set and not already_set(have_tf if isinstance(have_tf, dict) else {}, tf_set):
            soft = {k: v for k, v in tf_set.items() if k in {"align", "width", "layout"} or str(k).startswith("border")}
            meta: dict[str, Any] = {}
            if soft and soft != tf_set:
                meta["_soft_props"] = soft
            cmd = set_cmd(tbl_path, tf_set, **meta)
            if cmd:
                commands.append(cmd)
        elif tf_set:
            skipped += 1

        chrome_by_slot = spec.get("cells") if isinstance(spec.get("cells"), dict) else {}
        cell_paras = spec.get("cell_paragraphs") if isinstance(spec.get("cell_paragraphs"), dict) else {}
        allowed = designed_slots(spec)
        table_open = _table_is_open(tf_raw)
        inv_cells = _inventory_cells(tbl)
        n_rows = max((r for r, _c in inv_cells), default=0)
        n_cols = max((c for _r, c in inv_cells), default=0)
        cell_map = table_cells.get(str(loc_id)) if isinstance(table_cells.get(str(loc_id)), list) else []
        if cell_map:
            grid: list[list[dict[str, Any]]] = []
            for item in cell_map:
                if not isinstance(item, dict):
                    continue
                row = _as_int(item.get("row"))
                col = _as_int(item.get("col"))
                if row is None or col is None:
                    continue
                while len(grid) < row:
                    grid.append([])
                while len(grid[row - 1]) < col:
                    grid[row - 1].append({"cell_style": ""})
                slot = coerce_slot(item.get("cell_style") or "", allowed)
                grid[row - 1][col - 1] = {"cell_style": slot}
                raw = _slot_chrome(
                    chrome_by_slot,
                    slot,
                    row,
                    col,
                    plan=None,
                    n_rows=n_rows,
                    n_cols=n_cols,
                    allowed=allowed,
                )
                chrome = _finalize_cell_chrome(raw, table_open=table_open)
                para_sid = str(item.get("paragraph_style") or cell_paras.get(slot) or "").strip()
                path, alts = _resolved_cell_path(tbl_path, row, col, inv_cells)
                have = (inv_cells.get((row, col)) or {}).get("chrome") or {}
                if chrome and not already_set(have if isinstance(have, dict) else {}, chrome):
                    cmd = set_cmd(path, chrome, _alt_paths=alts)
                    if cmd:
                        commands.append(cmd)
                elif chrome:
                    skipped += 1
                if para_sid:
                    para_path = path + "/p[1]" if "/p[" not in path else path
                    para_alts = [a + "/p[1]" for a in alts]
                    cmd = set_cmd(para_path, {"style": para_sid}, _alt_paths=para_alts)
                    if cmd:
                        commands.append(cmd)
            enabled = _repeat_header_enabled(tf_raw)
            if enabled is not None and grid:
                for ri in _header_row_indices(grid):
                    cmd = set_cmd(f"{tbl_path}/row[{ri}]", {"header": "true" if enabled else "false"})
                    if cmd:
                        commands.append(cmd)
    return commands, skipped


def _inventory_cells(tbl: dict) -> dict[tuple[int, int], dict]:
    out: dict[tuple[int, int], dict] = {}
    for cell in (tbl.get("meta") or {}).get("cells") or []:
        if not isinstance(cell, dict):
            continue
        row, col = _as_int(cell.get("row")), _as_int(cell.get("col"))
        if row is None or col is None:
            continue
        out[(row, col)] = cell
    return out


def _resolved_cell_path(
    tbl_path: str,
    row: int,
    col: int,
    inv_cells: dict[tuple[int, int], dict],
) -> tuple[str, list[str]]:
    """Prefer inventory path (P3); keep alias list only as retry fallback."""
    guessed = _guess_cell_paths(tbl_path, row, col)
    hit = inv_cells.get((row, col)) or {}
    path = str(hit.get("path") or "")
    if path:
        alts = [p for p in guessed if p != path]
        return path, alts
    return guessed[0], guessed[1:]


def _compile_images(
    by_layer: dict[str, dict],
    props: dict[str, Any],
    idx: dict[tuple[str, str], dict],
) -> list[dict[str, Any]]:
    mapping = by_layer.get("image") or {}
    commands: list[dict[str, Any]] = []
    if not isinstance(mapping, dict):
        return []
    for loc_id, sid in mapping.items():
        row = idx.get(("image", str(loc_id)))
        spec = props.get(str(sid)) or {}
        if not row or not row.get("path"):
            continue
        path = str(row["path"])
        bag = {k: v for k, v in _bag(spec, "image").items() if k in _IMAGE_KEYS and v not in (None, "")}
        current = row.get("props") if isinstance(row.get("props"), dict) else {}
        img = {k: bag[k] for k in ("width", "height") if k in bag}
        if img and not already_set(current, img):
            cmd = set_cmd(path, img)
            if cmd:
                commands.append(cmd)
        if bag.get("hAlign"):
            para = path.rsplit("/r[", 1)[0] if "/r[" in path else path
            cmd = set_cmd(para, {"align": bag["hAlign"]})
            if cmd:
                commands.append(cmd)
    return commands


def _compile_find_runs(
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> list[dict[str, Any]]:
    paras = {str(r.get("location_id")): r for r in inventory.get("paragraph.body") or []}
    out: list[dict[str, Any]] = []
    for para_id, spans in (loc.get("paragraph_runs") or {}).items():
        para = paras.get(str(para_id))
        if not para or not para.get("path"):
            continue
        host = str(para.get("content") or "")
        for span in spans or []:
            if not isinstance(span, dict):
                continue
            sid = str(span.get("run_style") or "")
            bag = {k: v for k, v in _bag(props.get(sid) or {}, "run").items() if k in _RUN_KEYS and v not in (None, "", "none")}
            find = str(span.get("text") or span.get("runs_text") or "").strip()
            if not find or not bag:
                continue
            if host and find not in host and find.lower() not in host.lower():
                continue
            out.append({"path": str(para["path"]), "props": bag, "find": find})
    return out


def merge_same_path(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive ``set`` ops on the same path (style + instance keys)."""
    if not commands:
        return []
    out: list[dict[str, Any]] = []
    for cmd in commands:
        if (
            out
            and cmd.get("command") == "set"
            and out[-1].get("command") == "set"
            and cmd.get("path") == out[-1].get("path")
            and not cmd.get("find")
            and not out[-1].get("find")
            and not cmd.get("_no_merge")
            and not out[-1].get("_no_merge")
            and isinstance(cmd.get("props"), dict)
            and isinstance(out[-1].get("props"), dict)
        ):
            merged = dict(out[-1]["props"])
            merged.update(cmd["props"])
            extra = {k: v for k, v in cmd.items() if k not in {"command", "path", "props"}}
            prev_extra = {k: v for k, v in out[-1].items() if k not in {"command", "path", "props"}}
            # Keep split pgBorders recovery: don't merge a props-only-pgBorders into a prior set
            # if the prior set already omitted it on purpose… actually merge is fine for end state.
            out[-1] = {"command": "set", "path": cmd["path"], "props": merged, **prev_extra, **extra}
            continue
        out.append(cmd)
    return out


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

def _env_sequential() -> bool:
    return os.environ.get("LONGDOC_APPLY_SEQUENTIAL", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _env_chunk() -> int:
    raw = os.environ.get("LONGDOC_APPLY_BATCH_CHUNK", "").strip()
    if not raw:
        return DEFAULT_CHUNK
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CHUNK


def _run_one(doc: Path, cmd: dict[str, Any]) -> None:
    kind = str(cmd.get("command") or cmd.get("op") or "")
    props = cmd.get("props") if isinstance(cmd.get("props"), dict) else {}
    if kind == "remove":
        _remove_path(doc, str(cmd.get("path") or ""))
        return
    if kind == "add":
        _add_props(doc, str(cmd.get("parent") or ""), typ=str(cmd.get("type") or "style"), props=props)
        return
    if kind == "set":
        find = cmd.get("find")
        if find:
            _set_find(doc, str(cmd.get("path") or ""), props, str(find))
            return
        _set_props(doc, str(cmd.get("path") or ""), props)
        return
    raise RuntimeError(f"unsupported apply op: {kind}")


def _retry_failed(doc: Path, original: dict[str, Any]) -> None:
    soft = original.get("_soft_props")
    if isinstance(soft, dict) and soft:
        try:
            _run_one(doc, {**public_cmd(original), "props": _encode_props(soft)})
            return
        except Exception:
            pass
    alts = original.get("_alt_paths")
    if isinstance(alts, list) and alts:
        props = original.get("props") if isinstance(original.get("props"), dict) else {}
        for path in alts:
            try:
                _run_one(doc, {"command": "set", "path": str(path), "props": props})
                return
            except Exception:
                continue
    try:
        _run_one(doc, public_cmd(original))
    except Exception:
        return


def _failed_indices(payload: dict[str, Any], n: int) -> list[int]:
    results = payload.get("results")
    if not isinstance(results, list):
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        failed = int(summary.get("failed") or 0)
        return list(range(n)) if failed >= n else []
    out: list[int] = []
    for row in results:
        if not isinstance(row, dict) or row.get("success") is not False:
            continue
        try:
            i = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < n:
            out.append(i)
    return out


def _run_chunk(doc: Path, chunk: list[dict[str, Any]]) -> int:
    """Batch one chunk; on hard failure split in half. Returns n_failed after retries."""
    if not chunk:
        return 0
    try:
        payload = _batch_commands(doc, [public_cmd(c) for c in chunk], best_effort=True)
        failed = _failed_indices(payload if isinstance(payload, dict) else {}, len(chunk))
        for i in failed:
            _retry_failed(doc, chunk[i])
        return len(failed)
    except Exception as exc:
        if _batch_unsupported(exc):
            raise
        if len(chunk) <= 1:
            _retry_failed(doc, chunk[0])
            return 1
        mid = max(1, len(chunk) // 2)
        return _run_chunk(doc, chunk[:mid]) + _run_chunk(doc, chunk[mid:])


def execute_commands(
    doc: Path,
    commands: list[dict[str, Any]],
    *,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    if not commands:
        return {"mode": "noop", "n_commands": 0, "n_batches": 0, "n_failed": 0}
    chunk = max(1, int(chunk_size or _env_chunk()))
    if _env_sequential():
        n_failed = 0
        for cmd in commands:
            try:
                _run_one(doc, public_cmd(cmd))
            except Exception:
                n_failed += 1
                _retry_failed(doc, cmd)
        return {"mode": "sequential", "n_commands": len(commands), "n_batches": 0, "n_failed": n_failed}

    chunks = [commands[i : i + chunk] for i in range(0, len(commands), chunk)]
    n_failed = 0
    try:
        for i, chunk_cmds in enumerate(chunks, start=1):
            n_failed += _run_chunk(doc, chunk_cmds)
            print(
                json.dumps({"batch": i, "of": len(chunks), "size": len(chunk_cmds), "failed": n_failed}, ensure_ascii=False),
                flush=True,
            )
        return {
            "mode": "batch",
            "n_commands": len(commands),
            "n_batches": len(chunks),
            "n_failed": n_failed,
            "chunk_size": chunk,
        }
    except Exception as exc:
        n_failed = 0
        for cmd in commands:
            try:
                _run_one(doc, public_cmd(cmd))
            except Exception:
                n_failed += 1
                _retry_failed(doc, cmd)
        return {
            "mode": "sequential_fallback",
            "n_commands": len(commands),
            "n_batches": 0,
            "n_failed": n_failed,
            "reason": str(exc)[:200],
        }


def execute_find_cmds(doc: Path, find_cmds: list[dict[str, Any]]) -> int:
    n_failed = 0
    for cmd in find_cmds:
        try:
            _set_find(doc, str(cmd["path"]), cmd.get("props") or {}, str(cmd.get("find") or ""))
        except Exception:
            n_failed += 1
    return n_failed


def apply_document(
    *,
    source: Path,
    output: Path,
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
    chunk_size: int | None = None,
    dump_ops: Path | None = None,
) -> dict[str, Any]:
    """Copy source → output, compile ops, batch-apply, close once."""
    output.parent.mkdir(parents=True, exist_ok=True)
    _close_doc(source)
    if output.resolve() != source.resolve():
        _close_doc(output)
        shutil.copy2(source, output)

    compiled = compile_ops(
        catalog_entries=catalog_entries,
        props=props,
        loc=loc,
        inventory=inventory,
    )
    dump_path = dump_ops or (Path(os.environ["LONGDOC_APPLY_OPS"]) if os.environ.get("LONGDOC_APPLY_OPS") else None)
    if dump_path:
        dump_path = Path(dump_path)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps({"commands": compiled.commands, "find": compiled.find_cmds}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    stats: dict[str, Any] = {
        "n_commands": len(compiled.commands),
        "n_skipped": compiled.skipped,
        "n_find": len(compiled.find_cmds),
        "n_batches": 0,
        "n_failed": 0,
        "mode": "batch",
    }
    print(
        json.dumps(
            {
                "status": "applying",
                "n_commands": len(compiled.commands),
                "n_skipped": compiled.skipped,
                "n_find": len(compiled.find_cmds),
                "chunk_size": max(1, int(chunk_size or _env_chunk())),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    _open_doc(output)
    try:
        stats.update(execute_commands(output, compiled.commands, chunk_size=chunk_size))
        if compiled.find_cmds:
            stats["n_find_failed"] = execute_find_cmds(output, compiled.find_cmds)
    finally:
        _close_doc(output)
    return stats
