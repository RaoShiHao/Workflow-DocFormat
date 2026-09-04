"""officecli inventory engine (resident open + query/get). Skill-local, no repo imports."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.officecli import batch_commands, run as run_officecli, run_json
from lib.element import DocElement, Layer
from lib.ooxml_outline import body_outline_index, coerce_outline_lvl
from lib.ooxml_section import (
    section_index_for_picture_path,
    section_index_for_table_path,
    section_preview_text,
)
from lib.whitelist import (
    CELL_KEYS,
    TABLE_FORMAT_KEYS,
    filter_props,
    merge_element_props,
    merge_paragraph_props,
    whitelist_keys,
)


def get_json(doc: Path, path: str) -> dict[str, Any]:
    return run_json(["get", str(doc), path])

_NORMAL_STYLE_IDS = {"a", "normal"}
_INSTANCE_INHERITED_KEYS = ("outlineLvl", "keepNext", "pageBreakBefore")


def open_docx(doc: Path) -> None:
    run_officecli(["open", str(doc)], check=False)


def query_json(doc: Path, selector: str) -> Dict[str, Any]:
    proc = run_officecli(["query", str(doc), selector, "--json"], check=False)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def results_of(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or payload
    if isinstance(data, dict):
        raw = data.get("results") or data.get("Results") or []
    else:
        raw = payload.get("results") or payload.get("Results") or []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def first_format(payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = results_of(payload)
    if not rows:
        return {}
    fmt = rows[0].get("format") or {}
    return dict(fmt) if isinstance(fmt, dict) else {}


def get_format(doc: Path, path: str) -> Dict[str, Any]:
    try:
        payload = get_json(doc, path)
    except Exception:
        return {}
    return first_format(payload)


def get_node(doc: Path, path: str, *, depth: int = 1) -> Dict[str, Any]:
    proc = run_officecli(
        ["get", str(doc), path, "--depth", str(depth), "--json"],
        check=False,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    rows = results_of(payload)
    return rows[0] if rows else {}


def _batch_item_node(item: Dict[str, Any]) -> Dict[str, Any]:
    out = item.get("output")
    if isinstance(out, dict):
        rows = out.get("results") or out.get("Results") or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        if out.get("path") or out.get("format"):
            return out
    rows = item.get("results") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _prefetch_nodes(doc: Path, paths: List[str], *, depth: int = 1) -> Dict[str, Dict[str, Any]]:
    """One ``officecli batch`` of ``get``s (batch supports get/query)."""
    uniq: List[str] = []
    seen: set[str] = set()
    for path in paths:
        p = str(path or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    if not uniq:
        return {}
    cmds = [{"command": "get", "path": p, "depth": depth} for p in uniq]
    try:
        payload = batch_commands(doc, cmds, best_effort=True)
    except Exception:
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return {}
    results = data.get("results") or []
    out_file = data.get("outputFile")
    if out_file and Path(str(out_file)).is_file():
        try:
            extra = json.loads(Path(str(out_file)).read_text(encoding="utf-8"))
            blob = extra.get("data") if isinstance(extra.get("data"), dict) else extra
            if isinstance(blob, dict) and blob.get("results"):
                results = blob.get("results") or results
        except (OSError, json.JSONDecodeError):
            pass
    out: Dict[str, Dict[str, Any]] = {}
    for path, item in zip(uniq, results):
        if not isinstance(item, dict):
            continue
        if item.get("success") is False:
            continue
        node = _batch_item_node(item)
        if node:
            out[path] = node
    return out


def _prefetch_styles(
    doc: Path,
    rows: List[Dict[str, Any]],
    cache: Dict[str, Dict[str, Any]],
) -> None:
    paths: List[str] = []
    for row in rows:
        sid = _linked_style_id(row, row.get("format") or {})
        if sid and sid not in cache:
            paths.append(f"/styles/{sid}")
    nodes = _prefetch_nodes(doc, paths, depth=1)
    for path, node in nodes.items():
        sid = path.rsplit("/", 1)[-1]
        cache[sid] = dict(node.get("format") or {})


def _text_of(node: Dict[str, Any]) -> str:
    t = node.get("text") or (node.get("format") or {}).get("text") or ""
    return str(t).strip()


_RUN_TAIL_RE = re.compile(r"/(?:w:)?(?:r|run)\[\d+\](?:/.*)?$", re.I)


def _path_is_table_cell_para(path: str) -> bool:
    p = path.replace("\\", "/")
    return "/tc[" in p or "/tbl[" in p or "/table[" in p


def _parent_para_path(run_path: str) -> str:
    p = str(run_path or "").replace("\\", "/")
    return _RUN_TAIL_RE.sub("", p)


def _index_query_rows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map path, normalized path, and table:row:col → query/get row (first hit wins)."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("path") or "")
        if not path:
            continue
        out.setdefault(path, row)
        out.setdefault(_norm_body_path(path), row)
        cell = _tbl_cell_key(path)
        if cell:
            out.setdefault("cell:" + cell, row)
    return out


def _row_for_path(index: Dict[str, Dict[str, Any]], path: str) -> Dict[str, Any] | None:
    if not path:
        return None
    hit = index.get(path) or index.get(_norm_body_path(path))
    if hit:
        return hit
    cell = _tbl_cell_key(path)
    if cell:
        return index.get("cell:" + cell)
    return None


def _query_or_walk(doc: Path, selector: str, fallback_prefix: str, max_n: int = 400) -> List[Dict[str, Any]]:
    rows = results_of(query_json(doc, selector))
    if rows:
        return rows
    out: List[Dict[str, Any]] = []
    for i in range(1, max_n + 1):
        node = get_node(doc, f"{fallback_prefix}[{i}]")
        if not node:
            if i > 8 and not out:
                break
            if i > 3 and out:
                break
            continue
        node.setdefault("path", f"{fallback_prefix}[{i}]")
        out.append(node)
    return out


def list_sections(
    doc: Path,
    body_paras: Optional[List[DocElement]] = None,
    *,
    rows: Optional[List[Dict[str, Any]]] = None,
    hf_nodes: Optional[Dict[str, Dict[str, Any]]] = None,
    include_props: bool = True,
    include_hf: bool = True,
    include_preview: bool = True,
) -> List[DocElement]:
    if rows is None:
        rows = _query_or_walk(doc, "section", "/section", max_n=40)
    else:
        rows = list(rows)
    if not rows:
        node = get_node(doc, "/")
        if node:
            rows = [node]
    elements: List[DocElement] = []
    for i, row in enumerate(rows, start=1):
        path = str(row.get("path") or f"/section[{i}]")
        fmt = dict(row.get("format") or {})
        if include_props and not fmt:
            fmt = get_format(doc, path)
        props = filter_props(merge_element_props(fmt), whitelist_keys("section")) if include_props else {}
        hf = _section_hf(doc, fmt, node_cache=hf_nodes) if include_hf else {}
        if hf:
            props["_header_footer"] = hf
        content = _section_preview_text(doc, i, body_paras=body_paras) if include_preview else ""
        elements.append(
            DocElement(
                layer="section",
                location_id=i,
                path=path,
                props=props,
                content=content[:1600],
                meta={"header_footer": hf},
            )
        )
    return elements


def _section_hf(
    doc: Path,
    fmt: Dict[str, Any],
    *,
    node_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    mapping = {
        "header": fmt.get("headerRef.default") or fmt.get("headerRef"),
        "footer": fmt.get("footerRef.default") or fmt.get("footerRef"),
        "header_first": fmt.get("headerRef.first"),
    }
    cache = node_cache or {}
    for key, ref in mapping.items():
        if not ref:
            continue
        node = cache.get(str(ref))
        if node is None:
            node = get_node(doc, str(ref), depth=2)
        text = _text_of(node)
        nfmt = merge_element_props(node.get("format") or {})
        out[key] = {
            "text": text,
            "font.ea": nfmt.get("font.ea"),
            "font.latin": nfmt.get("font.latin"),
            "size": nfmt.get("size"),
            "align": nfmt.get("align"),
        }
    return {k: v for k, v in out.items() if any(v.get(x) for x in v)}


def _section_preview_text(
    doc: Path,
    section_index: int,
    body_paras: Optional[List[DocElement]] = None,
) -> str:
    text = section_preview_text(doc, section_index)
    if text:
        return text
    if not body_paras:
        return ""
    from lib.ooxml_section import (
        _load_body_section_breaks,
        paragraph_index_for_path,
        section_index_for_paragraph,
    )

    breaks = _load_body_section_breaks(str(Path(doc).resolve()))
    parts: List[str] = []
    for el in body_paras:
        idx = paragraph_index_for_path(doc, el.path)
        if idx is None:
            continue
        if section_index_for_paragraph(idx, breaks) != int(section_index):
            continue
        chunk = str(el.content or "").strip()
        if not chunk:
            continue
        parts.append(chunk[:120])
        if len(parts) >= 4:
            break
    return " / ".join(parts)


def _linked_style_id(row: Dict[str, Any] | None, fmt: Dict[str, Any] | None) -> str | None:
    for bag in (row or {}, fmt or {}):
        for key in ("style", "styleId"):
            val = bag.get(key)
            if val and str(val).strip().lower() not in _NORMAL_STYLE_IDS:
                return str(val)
    return None


def _style_format(
    doc: Path,
    style_id: str,
    cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if style_id in cache:
        return cache[style_id]
    try:
        fmt = get_format(doc, f"/styles/{style_id}")
    except Exception:
        fmt = {}
    cache[style_id] = fmt
    return fmt


def _paragraph_props(
    doc: Path,
    *,
    row: Dict[str, Any],
    fmt: Dict[str, Any],
    keys: list[str],
    style_cache: Dict[str, Dict[str, Any]],
    allow_get: bool = True,
) -> Dict[str, Any]:
    """Style GET ``/styles/{id}`` + paragraph effective (AutoDataBuild extract)."""
    if not fmt and allow_get:
        path = str(row.get("path") or "")
        if path:
            fmt = get_format(doc, path)
    sid = _linked_style_id(row, fmt)
    style_fmt = _style_format(doc, sid, style_cache) if sid else {}
    return filter_props(merge_paragraph_props(fmt, style_fmt), keys)


def fill_missing_outline_levels(doc: Path, elements: Dict[Layer, List[DocElement]]) -> bool:
    """Fill style-inherited whitelist keys officecli paragraph get omitted."""
    try:
        index = body_outline_index(doc)
    except Exception:
        return False
    changed = False
    for el in elements.get("paragraph.body") or []:
        inherited = index.lookup_props(el.path, location_id=el.location_id)
        if not inherited:
            continue
        for key in _INSTANCE_INHERITED_KEYS:
            if el.props.get(key) not in (None, "", "none"):
                continue
            val = inherited.get(key)
            if val in (None, "", "none"):
                continue
            if key == "outlineLvl":
                val = coerce_outline_lvl(val)
                if val is None:
                    continue
            el.props[key] = val
            el.meta = dict(el.meta or {})
            el.meta[key] = val
            changed = True
    return changed


def list_body_paragraphs(
    doc: Path,
    *,
    style_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[DocElement]:
    rows = list(rows) if rows is not None else _query_or_walk(doc, "paragraph", "/body/p", max_n=800)
    keys = whitelist_keys("paragraph.body")
    cache = style_cache if style_cache is not None else {}
    try:
        outline_index = body_outline_index(doc)
    except Exception:
        outline_index = None
    elements: List[DocElement] = []
    loc = 0
    for row in rows:
        path = str(row.get("path") or "")
        if not path.startswith("/body"):
            continue
        if _path_is_table_cell_para(path):
            continue
        loc += 1
        fmt = dict(row.get("format") or {})
        props = _paragraph_props(doc, row=row, fmt=fmt, keys=keys, style_cache=cache)
        if outline_index is not None:
            inherited = outline_index.lookup_props(path, location_id=loc)
            for key in _INSTANCE_INHERITED_KEYS:
                if props.get(key) not in (None, "", "none"):
                    continue
                val = inherited.get(key)
                if key == "outlineLvl":
                    val = coerce_outline_lvl(val)
                if val in (None, "", "none"):
                    continue
                props[key] = val
        text = _text_of(row)
        elements.append(
            DocElement(
                layer="paragraph.body",
                location_id=loc,
                path=path,
                props=props,
                content=text[:2000],
                meta={"outlineLvl": props.get("outlineLvl")},
            )
        )
    return elements


def list_table_cell_paragraphs(
    doc: Path,
    tables: Optional[List[DocElement]] = None,
    *,
    style_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    para_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[DocElement]:
    tables = tables if tables is not None else list_tables(doc)
    keys = whitelist_keys("paragraph.table_cell")
    cache = style_cache if style_cache is not None else {}
    elements: List[DocElement] = []
    loc = 0
    for tbl in tables:
        grid = (tbl.meta or {}).get("cells") or []
        for cell in grid:
            path = cell.get("para_path") or cell.get("path")
            if not path:
                continue
            loc += 1
            hit = _row_for_path(para_index or {}, path)
            fmt = dict((hit or {}).get("format") or {})
            row = dict(hit or {})
            row["path"] = path
            if fmt:
                row["format"] = fmt
            props = _paragraph_props(
                doc,
                row=row,
                fmt=fmt,
                keys=keys,
                style_cache=cache,
                allow_get=False,
            )
            elements.append(
                DocElement(
                    layer="paragraph.table_cell",
                    location_id=f"{tbl.location_id}:{cell.get('row')}:{cell.get('col')}",
                    path=path,
                    props=props,
                    content=str(cell.get("text") or "")[:300],
                    meta={
                        "table_index": tbl.location_id,
                        "row": cell.get("row"),
                        "col": cell.get("col"),
                        "cell_path": cell.get("path"),
                    },
                )
            )
    return elements


def list_tables(
    doc: Path,
    *,
    para_index: Optional[Dict[str, Dict[str, Any]]] = None,
    table_trees: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[DocElement]:
    rows = results_of(query_json(doc, "table"))
    if not rows:
        rows = []
        for i in range(1, 80):
            node = get_node(doc, f"/body/table[{i}]", depth=1)
            if not node:
                node = get_node(doc, f"/body/tbl[{i}]", depth=1)
            if not node:
                break
            node.setdefault("path", f"/body/table[{i}]")
            rows.append(node)
    keys = whitelist_keys("table")
    paths = [str(row.get("path") or "") for row in rows]
    trees = dict(table_trees or {})
    missing = [p for p in paths if p and p not in trees]
    if missing:
        trees.update(_prefetch_nodes(doc, missing, depth=4))
    elements: List[DocElement] = []
    for i, row in enumerate(rows, start=1):
        path = str(row.get("path") or f"/body/table[{i}]")
        fmt = dict(row.get("format") or {}) or get_format(doc, path)
        table_format = filter_props(merge_element_props(fmt), keys)
        cells = _read_table_cells(
            doc, path, para_index=para_index, tree=trees.get(path)
        )
        hint_parts = [c.get("text") or "" for c in cells[:8]]
        elements.append(
            DocElement(
                layer="table",
                location_id=i,
                path=path,
                props={"table_format": table_format, "cells": _cells_chrome_index(cells)},
                content=" | ".join(p for p in hint_parts if p)[:600],
                meta={
                    "cells": cells,
                    "n_rows": _max_row(cells),
                    "n_cols": _max_col(cells),
                    "section_index": _section_index_for_table(doc, path),
                },
            )
        )
    return elements


def _max_row(cells: List[Dict[str, Any]]) -> int:
    return max((int(c.get("row") or 0) for c in cells), default=0)


def _max_col(cells: List[Dict[str, Any]]) -> int:
    return max((int(c.get("col") or 0) for c in cells), default=0)


def _cell_para_text(
    *,
    para_path: str,
    fallback_node: Dict[str, Any] | None,
    para_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    hit = _row_for_path(para_index or {}, para_path)
    if hit is not None:
        return _text_of(hit)[:200]
    if fallback_node:
        text = _text_of(fallback_node)
        if text:
            return text[:200]
        for child in fallback_node.get("children") or []:
            if isinstance(child, dict):
                text = _text_of(child)
                if text:
                    return text[:200]
    return ""


def _read_table_cells(
    doc: Path,
    tbl_path: str,
    *,
    para_index: Optional[Dict[str, Dict[str, Any]]] = None,
    tree: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    node = tree if tree else get_node(doc, tbl_path, depth=4)
    cells: List[Dict[str, Any]] = []
    for child in _walk_nodes(node):
        path = str(child.get("path") or "")
        typ = str(child.get("type") or "").lower()
        if not _is_table_cell_element(path, typ):
            continue
        fmt = merge_element_props(child.get("format") or {})
        chrome = {k: fmt[k] for k in CELL_KEYS if k in fmt}
        row_i, col_i = _row_col_from_path(path)
        para_path = path + "/p[1]" if "/p[" not in path else path
        text = _cell_para_text(
            para_path=para_path,
            fallback_node=child,
            para_index=para_index,
        )
        cells.append(
            {
                "path": path,
                "para_path": para_path,
                "row": row_i,
                "col": col_i,
                "text": text[:200],
                "chrome": chrome,
            }
        )
    if cells:
        return cells
    for ri in range(1, 40):
        row_hit = False
        for ci in range(1, 20):
            cpath = f"{tbl_path}/tr[{ri}]/tc[{ci}]"
            cnode = get_node(doc, cpath, depth=2)
            if not cnode:
                break
            row_hit = True
            fmt = merge_element_props(cnode.get("format") or {})
            para_path = f"{cpath}/p[1]"
            cells.append(
                {
                    "path": cpath,
                    "para_path": para_path,
                    "row": ri,
                    "col": ci,
                    "text": _cell_para_text(
                        para_path=para_path,
                        fallback_node=cnode,
                        para_index=para_index,
                    ),
                    "chrome": {k: fmt[k] for k in CELL_KEYS if k in fmt},
                }
            )
        if not row_hit:
            break
    return cells


def _walk_nodes(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = [node]
    for ch in node.get("children") or []:
        if isinstance(ch, dict):
            out.extend(_walk_nodes(ch))
    return out


def _row_col_from_path(path: str) -> tuple[int, int]:
    tr = re.search(r"/tr\[(\d+)\]", path)
    tc = re.search(r"/tc\[(\d+)\]", path)
    return (int(tr.group(1)) if tr else 0, int(tc.group(1)) if tc else 0)


def _cells_chrome_index(cells: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Cluster chrome bags under synthetic slot names row{N}_col{M} — assignment may remap."""
    out: Dict[str, Dict[str, Any]] = {}
    for c in cells:
        key = f"r{c.get('row')}_c{c.get('col')}"
        chrome = dict(c.get("chrome") or {})
        if chrome:
            out[key] = chrome
    return out


def list_images(
    doc: Path,
    *,
    rows: Optional[List[Dict[str, Any]]] = None,
    para_index: Optional[Dict[str, Dict[str, Any]]] = None,
    extra_gets: bool = True,
) -> List[DocElement]:
    if rows is None:
        rows = results_of(query_json(doc, "picture"))
        if not rows:
            rows = results_of(query_json(doc, "image"))
    keys = whitelist_keys("image")
    elements: List[DocElement] = []
    for i, row in enumerate(rows, start=1):
        path = str(row.get("path") or "")
        fmt = dict(row.get("format") or {})
        if not fmt and extra_gets and path:
            fmt = get_format(doc, path)
        props = filter_props(merge_element_props(fmt), keys)
        para_path = path.rsplit("/r[", 1)[0] if "/r[" in path else path
        if "hAlign" not in props:
            hit = _row_for_path(para_index or {}, para_path)
            pfmt = merge_element_props((hit or {}).get("format") or {})
            align = pfmt.get("align") or pfmt.get("effective.alignment")
            if not align and extra_gets:
                raw = get_format(doc, para_path)
                align = raw.get("align") or raw.get("effective.alignment")
            if align:
                props["hAlign"] = align
        pic_id = fmt.get("id") if fmt.get("id") not in (None, "") else i
        caption = _text_of(row)
        if not caption:
            hit = _row_for_path(para_index or {}, para_path)
            if hit:
                caption = _text_of(hit)
        if not caption and extra_gets:
            caption = _text_of(get_node(doc, para_path))
        elements.append(
            DocElement(
                layer="image",
                location_id=i,
                path=path,
                props=props,
                content=caption[:300],
                meta={
                    "officecli_id": pic_id,
                    "relId": fmt.get("relId"),
                    "contentType": fmt.get("contentType"),
                    "para_path": para_path,
                    "section_index": _section_index_for_picture(doc, path or para_path),
                },
            )
        )
    return elements


def list_runs_with_delta(
    doc: Path,
    paragraphs: Optional[List[DocElement]] = None,
    *,
    run_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[DocElement]:
    """Runs whose font-level props differ from the host paragraph."""
    paras = paragraphs if paragraphs is not None else list_body_paragraphs(doc)
    queried = list(run_rows) if run_rows is not None else results_of(query_json(doc, "run"))
    if queried:
        return _runs_from_query(paras, queried)
    return _runs_via_get_node(doc, paras)


def _delta_run_element(
    *,
    loc: int,
    para: DocElement,
    path: str,
    text: str,
    fmt: Dict[str, Any],
    host: Dict[str, Any],
    offset: int,
) -> DocElement | None:
    n = len(text)
    if n == 0:
        return None
    delta = {k: v for k, v in fmt.items() if host.get(k) != v}
    if not delta:
        return None
    return DocElement(
        layer="run",
        location_id=loc,
        path=path or para.path,
        props=delta,
        content=text[:80],
        meta={
            "para_path": para.path,
            "para_location_id": para.location_id,
            "range": f"{offset}:{offset + n}",
        },
    )


def _runs_from_query(paras: List[DocElement], run_rows: List[Dict[str, Any]]) -> List[DocElement]:
    run_keys = whitelist_keys("run")
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in run_rows:
        path = str(row.get("path") or "")
        if not path or _path_is_table_cell_para(path):
            continue
        parent = _norm_body_path(_parent_para_path(path))
        grouped.setdefault(parent, []).append(row)
    elements: List[DocElement] = []
    loc = 0
    for para in paras:
        host = filter_props(para.props, run_keys)
        offset = 0
        for child in grouped.get(_norm_body_path(para.path)) or []:
            text = str(child.get("text") or (child.get("format") or {}).get("text") or "")
            n = len(text)
            fmt = filter_props(merge_element_props(child.get("format") or {}), run_keys)
            el = _delta_run_element(
                loc=loc + 1,
                para=para,
                path=str(child.get("path") or para.path),
                text=text,
                fmt=fmt,
                host=host,
                offset=offset,
            )
            offset += n
            if el is None:
                continue
            loc += 1
            elements.append(el)
    return elements


def _runs_via_get_node(doc: Path, paras: List[DocElement]) -> List[DocElement]:
    run_keys = whitelist_keys("run")
    elements: List[DocElement] = []
    loc = 0
    for para in paras:
        node = get_node(doc, para.path, depth=2)
        children = node.get("children") or []
        host = filter_props(para.props, run_keys)
        offset = 0
        for child in children:
            if not isinstance(child, dict):
                continue
            typ = str(child.get("type") or "").lower()
            text = str(child.get("text") or "")
            n = len(text)
            if typ not in {"run", "r"} or n == 0:
                offset += n
                continue
            fmt = filter_props(merge_element_props(child.get("format") or {}), run_keys)
            el = _delta_run_element(
                loc=loc + 1,
                para=para,
                path=str(child.get("path") or para.path),
                text=text,
                fmt=fmt,
                host=host,
                offset=offset,
            )
            offset += n
            if el is None:
                continue
            loc += 1
            elements.append(el)
    return elements


def _norm_body_path(path: str) -> str:
    p = str(path or "").replace("\\", "/").lower()
    p = p.replace("/w:tbl[", "/table[")
    p = p.replace("/tbl[", "/table[")
    p = p.replace("/w:p[", "/p[")
    return p


def _tbl_cell_key(path: str) -> str | None:
    """``tableIndex:row:col`` so ``p[1]`` and ``p[@paraId=…]`` hit the same cell."""
    p = _norm_body_path(path)
    tbl = re.search(r"/table\[(\d+)\]", p)
    tr = re.search(r"/tr\[(\d+)\]", p)
    tc = re.search(r"/tc\[(\d+)\]", p)
    if not (tbl and tr and tc):
        return None
    return f"{tbl.group(1)}:{tr.group(1)}:{tc.group(1)}"


def _is_table_cell_element(path: str, typ: str) -> bool:
    t = str(typ or "").lower()
    if t in {"cell", "tc"}:
        return True
    p = str(path or "").replace("\\", "/")
    return bool(re.search(r"/tc\[\d+\]$", p))


def _body_child_kind(typ: str, path: str) -> str:
    t = str(typ or "").lower()
    np = _norm_body_path(path)
    if "/tc[" in np:
        return "other"
    if t in {"tbl", "table"} or "/table[" in np:
        return "table"
    if t in {"drawing", "picture", "image"} or "/pict" in np or "/drawing" in np:
        return "image"
    if t in {"p", "paragraph"} or "/p[" in np:
        return "paragraph"
    return t or "other"


def _para_preview(el: DocElement, limit: int = 180) -> dict[str, Any]:
    return {"location_id": el.location_id, "content": (el.content or "")[:limit]}


def _has_visible_text(el: DocElement) -> bool:
    return bool(str(el.content or "").strip())


def _section_index_for_picture(doc: Path, path: str) -> int:
    try:
        return int(section_index_for_picture_path(doc, path) or 1)
    except Exception:
        return 1


def _section_index_for_table(doc: Path, path: str) -> int:
    try:
        return int(section_index_for_table_path(doc, path) or 1)
    except Exception:
        return 1


def _neighbor_window(para_seq: List[DocElement], idx: int, n: int) -> tuple[list, list]:
    if n <= 0:
        return [], []
    before = [p for p in para_seq[: max(0, idx)] if _has_visible_text(p)][-n:]
    after = [p for p in para_seq[max(0, idx) :] if _has_visible_text(p)][:n]
    return before, after


def attach_neighbor_paragraphs(
    doc: Path | None,
    *,
    paras: List[DocElement],
    tables: List[DocElement],
    images: List[DocElement],
    max_n: int = 8,
) -> None:
    """Store up to *max_n* body paragraphs before/after each table and image."""
    n = max(0, int(max_n or 0))
    para_by_path = {_norm_body_path(p.path): p for p in paras if p.path}
    blocks: List[Dict[str, Any]] = []
    if doc is not None:
        node = get_node(doc, "/body", depth=1)
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            path = str(child.get("path") or "")
            blocks.append({"path": path, "kind": _body_child_kind(child.get("type") or "", path)})

    if blocks:
        para_seq: List[DocElement] = []
        table_at: Dict[str, int] = {}
        image_at: Dict[str, int] = {}
        for i, block in enumerate(blocks):
            kind = block["kind"]
            np = _norm_body_path(block["path"])
            if kind == "paragraph":
                el = para_by_path.get(np)
                if el:
                    para_seq.append(el)
            elif kind == "table":
                table_at[np] = len(para_seq)
            elif kind == "image":
                image_at[np] = len(para_seq)
        for tbl in tables:
            idx = table_at.get(_norm_body_path(tbl.path), 0)
            before, after = _neighbor_window(para_seq, idx, n)
            tbl.meta = dict(tbl.meta or {})
            tbl.meta["neighbor_before"] = [_para_preview(p) for p in before]
            tbl.meta["neighbor_after"] = [_para_preview(p) for p in after]
        for img in images:
            host = _norm_body_path(str((img.meta or {}).get("para_path") or img.path))
            host_el = para_by_path.get(host)
            if host_el is not None:
                try:
                    hi = next(i for i, p in enumerate(para_seq) if p.location_id == host_el.location_id)
                except StopIteration:
                    hi = image_at.get(host, 0)
            else:
                hi = image_at.get(host, 0)
            before, after = _neighbor_window(para_seq, hi, n)
            img.meta = dict(img.meta or {})
            img.meta["neighbor_before"] = [_para_preview(p) for p in before]
            img.meta["neighbor_after"] = [_para_preview(p) for p in after]
        return

    for img in images:
        host = _norm_body_path(str((img.meta or {}).get("para_path") or ""))
        host_el = para_by_path.get(host)
        if host_el is None:
            continue
        try:
            hi = next(i for i, p in enumerate(paras) if p.location_id == host_el.location_id)
        except StopIteration:
            continue
        before, after = _neighbor_window(paras, hi, n)
        img.meta = dict(img.meta or {})
        img.meta["neighbor_before"] = [_para_preview(p) for p in before]
        img.meta["neighbor_after"] = [_para_preview(p) for p in after]


def inventory_bundle(doc: Path, *, profile: str = "full") -> Dict[str, Any]:
    """Inventory plus the query rows flatten/integrity reuse.

    ``full``: template clustering (neighbors, section preview, extra image gets).
    ``eval``: same whitelist props / cell paras / HF as ``full``, no LLM extras.
    ``assign``: init/source T/A/M — no section props/HF and no cell-para layer.
    Neighbors (nonempty paragraphs) are attached on ``full`` and ``assign``.
    """
    mode = str(profile or "full").strip().lower()
    if mode not in ("full", "eval", "assign"):
        mode = "full"
    assign = mode == "assign"
    full = mode == "full"
    style_cache: Dict[str, Dict[str, Any]] = {}
    para_rows = _query_or_walk(doc, "paragraph", "/body/p", max_n=800)
    para_index = _index_query_rows(para_rows)
    _prefetch_styles(doc, para_rows, style_cache)
    paras = list_body_paragraphs(doc, style_cache=style_cache, rows=para_rows)
    tables = list_tables(doc, para_index=para_index)
    picture_rows = results_of(query_json(doc, "picture"))
    image_rows = picture_rows if picture_rows else results_of(query_json(doc, "image"))
    images = list_images(
        doc,
        rows=image_rows,
        para_index=para_index,
        extra_gets=full,
    )
    if full or assign:
        attach_neighbor_paragraphs(
            doc,
            paras=paras,
            tables=tables,
            images=images,
            max_n=8,
        )
    run_rows = results_of(query_json(doc, "run"))
    hf_nodes: Dict[str, Dict[str, Any]] = {}
    sec_rows: Optional[List[Dict[str, Any]]] = None
    if not assign:
        sec_rows = results_of(query_json(doc, "section"))
        refs: List[str] = []
        for row in sec_rows:
            fmt = dict(row.get("format") or {})
            for key in (
                "headerRef.default",
                "headerRef",
                "footerRef.default",
                "footerRef",
                "headerRef.first",
            ):
                ref = fmt.get(key)
                if ref:
                    refs.append(str(ref))
        hf_nodes = _prefetch_nodes(doc, refs, depth=2)
    cell_paras: List[DocElement] = []
    if not assign:
        cell_paras = list_table_cell_paragraphs(
            doc, tables=tables, style_cache=style_cache, para_index=para_index
        )
    elements: Dict[Layer, List[DocElement]] = {
        "section": list_sections(
            doc,
            body_paras=paras,
            rows=sec_rows,
            hf_nodes=hf_nodes,
            include_props=not assign,
            include_hf=not assign,
            include_preview=True,
        ),
        "paragraph.body": paras,
        "paragraph.table_cell": cell_paras,
        "table": tables,
        "image": images,
        "run": list_runs_with_delta(doc, paras, run_rows=run_rows),
    }
    return {
        "elements": elements,
        "para_rows": para_rows,
        "picture_rows": picture_rows,
    }


def inventory(doc: Path, *, profile: str = "full") -> Dict[Layer, List[DocElement]]:
    """Snapshot a docx. See ``inventory_bundle`` for profiles."""
    return inventory_bundle(doc, profile=profile)["elements"]


def heading_outline(elements: List[DocElement]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for el in elements:
        lvl = el.props.get("outlineLvl")
        text = (el.content or "").strip()
        if lvl in (None, "", "none"):
            if text and len(text) <= 40 and not text.endswith("。") and not text.endswith("."):
                if any(k in text for k in ("第", "章", "节", "Chapter", "Section")):
                    rows.append({"location_id": el.location_id, "text": text[:80], "outlineLvl": None})
            continue
        try:
            lv = int(lvl)
        except (TypeError, ValueError):
            lv = lvl
        if lv in (9, "9"):
            continue
        rows.append({"location_id": el.location_id, "text": text[:80], "outlineLvl": lv})
    return rows


def table_format_of(props: Dict[str, Any]) -> Dict[str, Any]:
    tf = props.get("table_format")
    if isinstance(tf, dict):
        return filter_props(tf, TABLE_FORMAT_KEYS)
    return filter_props(props, TABLE_FORMAT_KEYS)
