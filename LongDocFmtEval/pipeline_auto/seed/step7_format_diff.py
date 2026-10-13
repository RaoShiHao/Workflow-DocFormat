"""Step7 · derive T-text props from golden.docx vs init.docx (not raw role_params dump).

Uses styles.json / map.json locators to read whitelist-scoped effective props on both
sides, then keeps only deltas that init still needs to reach the designed target.
Target values prefer role_params (writable canonical); comparison uses init extract.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from LongDocFmtEval.common.officecli_runner import close_docx, get_json
from LongDocFmtEval.pipeline_auto.auto_paths import officecli_locale_yaml
from LongDocFmtEval.pipeline_auto.seed.extract_style_tree import (
    _clean_props,
    _extract_paragraph_props,
    _extract_run_props,
    _extract_section_chrome,
    _first_format,
    _para_index_for_role,
    list_inline_pictures,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (
    normalize_section_role_entry,
    table_cells_chrome_of,
    table_format_of,
)
from LongDocFmtEval.pipeline_auto.style_taxonomy import is_table_paragraph_object


def _extract_bucket(obj: str = "", rid: str = "") -> str:
    """Coarse bucket for live extract (heading vs body share paragraph read path)."""
    o = str(obj or "").strip().lower()
    r = str(rid or "")
    if o.startswith("section") or r.startswith("Sec"):
        return "section"
    if o.startswith("run") or r.startswith("Run"):
        return "run"
    if o.startswith("table") or o in {"tbl"} or r.startswith("Tbl"):
        return "table"
    if o.startswith("image") or o.startswith("img") or r.startswith("Img"):
        return "image"
    if is_table_paragraph_object(o, r):
        return "paragraph_table"
    if o.startswith("paragraph") or o in {"para", "cell", "cell_text"} or r.startswith("Para"):
        return "paragraph"
    return ""


def _whitelist_keys(locale: str = "zh") -> dict[str, set[str]]:
    path = officecli_locale_yaml("officecli_whitelist.yaml", locale)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {} or {}
    out: dict[str, set[str]] = {}
    for layer in ("paragraph", "run", "section", "table_format", "cell_format", "image"):
        block = raw.get(layer) or {}
        if isinstance(block, dict):
            out[layer] = set(block.keys())
    return out


def _norm(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low
    return s


def _props_equal(a: Any, b: Any) -> bool:
    if a in (None, "", "none") and b in (None, "", "none"):
        return True
    return _norm(a) == _norm(b)


def _filter_keys(props: dict[str, Any] | None, allowed: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if v in (None, "", "none") or isinstance(v, (dict, list)):
            continue
        if allowed and k not in allowed:
            # allow font / border family prefixes when parent key listed
            if not any(k == a or k.startswith(a + ".") for a in allowed):
                continue
        out[k] = v
    return out


def _diff_flat(
    target: dict[str, Any],
    init: dict[str, Any],
    allowed: set[str],
) -> dict[str, Any]:
    tgt = _filter_keys(target, allowed)
    ini = _filter_keys(init, allowed)
    return {k: v for k, v in tgt.items() if not _props_equal(v, ini.get(k))}


def _diff_chrome(target: dict[str, Any] | None, init: dict[str, Any] | None) -> dict[str, Any]:
    """Scalar chrome fields only (header/footer/cell)."""
    if not isinstance(target, dict) or not target:
        return {}
    ini = init if isinstance(init, dict) else {}
    out: dict[str, Any] = {}
    for k, v in target.items():
        if isinstance(v, (dict, list)) or v in (None,):
            continue
        if not _props_equal(v, ini.get(k)):
            out[k] = v
    return out


def _section_index(styles: dict, maps: dict, rid: str) -> int | None:
    sec = styles.get("section") or {}
    if rid in sec:
        try:
            return int(sec[rid])
        except (TypeError, ValueError):
            pass
    idxs = (maps.get("section") or {}).get(rid) or []
    if isinstance(idxs, list) and idxs:
        try:
            return int(idxs[0])
        except (TypeError, ValueError):
            pass
    if isinstance(idxs, (int, float)):
        return int(idxs)
    return None


def _table_index(styles: dict, maps: dict, rid: str) -> int | None:
    nested = (styles.get("table") or {}).get(rid)
    if isinstance(nested, dict):
        idx = nested.get("table_index")
        if idx is None and isinstance(nested.get("table_structure"), dict):
            idx = nested["table_structure"].get("table")
        try:
            return int(idx)
        except (TypeError, ValueError):
            pass
    flat = (styles.get("table_structure") or {}).get(rid)
    if flat is not None:
        try:
            return int(flat)
        except (TypeError, ValueError):
            pass
    idxs = (maps.get("table_structure") or {}).get(rid) or []
    if isinstance(idxs, list) and idxs:
        try:
            return int(idxs[0])
        except (TypeError, ValueError):
            pass
    return None


def _image_index(styles: dict, maps: dict, rid: str) -> int | None:
    loc = (styles.get("image") or {}).get(rid)
    if loc is not None:
        try:
            return int(loc if not isinstance(loc, dict) else loc.get("image_index") or loc.get("index"))
        except (TypeError, ValueError):
            pass
    idxs = (maps.get("image") or {}).get(rid) or []
    if isinstance(idxs, list) and idxs:
        try:
            return int(idxs[0])
        except (TypeError, ValueError):
            pass
    return None


def extract_role_snapshot(
    doc: Path,
    *,
    rid: str,
    spec: dict[str, Any],
    styles: dict[str, Any],
    maps: dict[str, Any],
    pictures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Best-effort live props for one role from a docx. Shape mirrors role_params entry."""
    bucket = _extract_bucket(spec.get("object"), rid)
    out: dict[str, Any] = {"object": spec.get("object") or bucket}

    try:
        if bucket == "section":
            idx = _section_index(styles, maps, rid)
            if idx is None:
                return out
            sec_fmt = _first_format(get_json(doc, f"/section[{int(idx)}]"))
            page = _clean_props(sec_fmt)
            entry: dict[str, Any] = {"object": "section", "props": page}
            _extract_section_chrome(doc, sec_fmt, entry)
            entry = normalize_section_role_entry(entry)
            out["props"] = dict(entry.get("props") or {})
            for hf in ("header", "footer", "header_first"):
                if isinstance(entry.get(hf), dict):
                    out[hf] = dict(entry[hf])
            return out

        if bucket in ("paragraph", "paragraph_table"):
            idx = _para_index_for_role(styles, maps, rid)
            if idx is None:
                return out
            out["props"] = _extract_paragraph_props(doc, idx)
            return out

        if bucket == "run":
            loc = (styles.get("run") or {}).get(rid) or {}
            if not isinstance(loc, dict):
                loc = {}
            out["props"] = _extract_run_props(doc, styles, maps, rid, loc)
            return out

        if bucket == "table":
            ti = _table_index(styles, maps, rid)
            if ti is None:
                return out
            fmt = _clean_props(_first_format(get_json(doc, f"/body/table[{ti}]")))
            if not fmt:
                fmt = _clean_props(_first_format(get_json(doc, f"/body/tbl[{ti}]")))
            tf = {
                k: fmt[k] for k in ("align", "width", "layout", "repeat_header")
                if fmt.get(k) not in (None, "", "none")
            }
            for k, v in fmt.items():
                if str(k).startswith("border") and v not in (None, "", "none"):
                    tf[k] = v
            out["props"] = {"table_format": tf, "cells": {}}
            return out

        if bucket == "image":
            ii = _image_index(styles, maps, rid)
            pics = pictures if pictures is not None else list_inline_pictures(doc, with_paths=False)
            if ii is None or ii < 1 or ii > len(pics):
                return out
            pic = pics[ii - 1]
            props = {
                k: pic[k] for k in ("width", "height", "hAlign", "align")
                if pic.get(k) not in (None, "", "none")
            }
            if props.get("align") and not props.get("hAlign"):
                props["hAlign"] = props["align"]
            out["props"] = props
            return out
    except Exception:  # noqa: BLE001
        return out
    return out


def _target_table_props(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict]]:
    tf = table_format_of(spec)
    cells = table_cells_chrome_of(spec)
    nested = (spec.get("props") or {}).get("table_format")
    if isinstance(nested, dict) and nested and not tf:
        tf = {k: v for k, v in nested.items() if not isinstance(v, (dict, list))}
    nested_cells = (spec.get("props") or {}).get("cells")
    if isinstance(nested_cells, dict) and nested_cells and not cells:
        cells = {k: v for k, v in nested_cells.items() if isinstance(v, dict)}
    return tf, cells


def build_need_role_params(
    role_params: dict[str, Any],
    *,
    golden: Path | None,
    init: Path | None,
    styles: dict[str, Any] | None = None,
    maps: dict[str, Any] | None = None,
    locale: str = "zh",
    stylesheet: list | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return role_params-shaped dict with props reduced to init→target deltas.

    If *init* is missing or unreadable, returns a deep copy of *role_params* (full target).
    """
    del stylesheet  # reserved for future base-role clustering
    styles = styles or {}
    maps = maps or {}
    meta: dict[str, Any] = {
        "mode": "role_params_full",
        "golden": str(golden).replace("\\", "/") if golden else "",
        "init": str(init).replace("\\", "/") if init else "",
        "roles_diffed": 0,
        "roles_unchanged": 0,
        "roles_extract_fail": 0,
        "warnings": [],
    }
    if not role_params:
        return {}, meta
    if init is None or not Path(init).is_file():
        meta["warnings"].append("init.docx missing; t_text uses full role_params")
        return copy.deepcopy(role_params), meta

    wl = _whitelist_keys(locale)
    need: dict[str, Any] = {}
    init_doc = Path(init)
    golden_doc = Path(golden) if golden and Path(golden).is_file() else None

    max_img = 0
    for rid, spec in role_params.items():
        if not isinstance(spec, dict):
            continue
        if _extract_bucket(spec.get("object"), rid) != "image":
            continue
        ii = _image_index(styles, maps, rid)
        if ii:
            max_img = max(max_img, ii)
    try:
        pictures = list_inline_pictures(init_doc, with_paths=False, max_needed=max_img) if max_img else []
    except Exception as exc:  # noqa: BLE001
        pictures = []
        meta["warnings"].append(f"init image scan failed: {exc}"[:200])

    try:
        for rid, spec in role_params.items():
            if not isinstance(spec, dict):
                continue
            bucket = _extract_bucket(spec.get("object"), rid)
            entry = {
                k: copy.deepcopy(v)
                for k, v in spec.items()
                if k not in ("props", "header", "footer", "header_first", "cells", "table_format")
            }
            entry.setdefault("object", spec.get("object"))
            snap = extract_role_snapshot(
                init_doc, rid=rid, spec=spec, styles=styles, maps=maps, pictures=pictures,
            )

            if bucket == "table":
                tgt_tf, tgt_cells = _target_table_props(spec)
                init_tf = table_format_of(snap) or ((snap.get("props") or {}).get("table_format") or {})
                if not isinstance(init_tf, dict):
                    init_tf = {}
                need_tf = _diff_flat(tgt_tf, init_tf, wl.get("table_format") or set())
                need_cells: dict[str, dict] = {}
                init_cells = table_cells_chrome_of(snap) or ((snap.get("props") or {}).get("cells") or {})
                if not isinstance(init_cells, dict):
                    init_cells = {}
                cell_allowed = wl.get("cell_format") or set()
                for pos, cf in (tgt_cells or {}).items():
                    if not isinstance(cf, dict):
                        continue
                    delta = _diff_flat(cf, init_cells.get(pos) or {}, cell_allowed)
                    if delta:
                        need_cells[pos] = delta
                # Plain/default grid often yields empty init chrome → keep full target cells
                if tgt_cells and not init_cells and not need_cells:
                    need_cells = {
                        pos: _filter_keys(cf, cell_allowed)
                        for pos, cf in tgt_cells.items()
                        if isinstance(cf, dict)
                    }
                    need_cells = {k: v for k, v in need_cells.items() if v}
                entry["props"] = {"table_format": need_tf, "cells": need_cells}
                if need_tf or need_cells:
                    meta["roles_diffed"] += 1
                else:
                    meta["roles_unchanged"] += 1
                need[rid] = entry
                continue

            if bucket == "section":
                tgt_page = {
                    k: v for k, v in (spec.get("props") or {}).items()
                    if not isinstance(v, (dict, list))
                }
                init_page = dict(snap.get("props") or {})
                need_page = _diff_flat(tgt_page, init_page, wl.get("section") or set())
                entry["props"] = need_page
                changed = bool(need_page)
                for hf in ("header", "footer", "header_first"):
                    tgt_hf = spec.get(hf) if isinstance(spec.get(hf), dict) else None
                    if tgt_hf is None and isinstance((spec.get("props") or {}).get(hf), dict):
                        tgt_hf = (spec.get("props") or {}).get(hf)
                    if not isinstance(tgt_hf, dict):
                        continue
                    ini_hf = snap.get(hf) if isinstance(snap.get(hf), dict) else {}
                    delta = _diff_chrome(tgt_hf, ini_hf)
                    if delta:
                        entry[hf] = delta
                        changed = True
                if changed:
                    meta["roles_diffed"] += 1
                else:
                    meta["roles_unchanged"] += 1
                need[rid] = entry
                continue

            # paragraph / run / image
            if bucket in ("paragraph", "paragraph_table"):
                allowed = wl.get("paragraph") or set()
            elif bucket == "run":
                allowed = wl.get("run") or set()
            elif bucket == "image":
                allowed = wl.get("image") or set()
            else:
                allowed = set()

            tgt = {
                k: v for k, v in (spec.get("props") or {}).items()
                if not isinstance(v, (dict, list))
            }
            # Optional: if golden extract has a key role_params omitted, ignore (canonical = role_params)
            if golden_doc and not tgt:
                gsnap = extract_role_snapshot(
                    golden_doc, rid=rid, spec=spec, styles=styles, maps=maps,
                )
                tgt = dict(gsnap.get("props") or {})

            init_props = dict(snap.get("props") or {})
            if not init_props and not snap.get("props") and bucket:
                meta["roles_extract_fail"] += 1
            need_props = _diff_flat(tgt, init_props, allowed)
            entry["props"] = need_props
            if need_props:
                meta["roles_diffed"] += 1
            else:
                meta["roles_unchanged"] += 1
            need[rid] = entry

        meta["mode"] = "init_delta"
    finally:
        try:
            close_docx(init_doc)
        except Exception:  # noqa: BLE001
            pass
        if golden_doc:
            try:
                close_docx(golden_doc)
            except Exception:  # noqa: BLE001
                pass

    return need, meta


def heading_style_ids(stylesheet: list | None, role_params: dict | None = None) -> set[str]:
    """Paragraph roles that are outline headings or cover title_kind (not table-paragraph)."""
    from LongDocFmtEval.pipeline_auto.seed.heading_contract import (  # noqa: PLC0415
        heading_index,
        parse_heading_level,
        parse_title_kind,
    )

    out: set[str] = set()
    for sid, rec in heading_index(stylesheet).items():
        if is_table_paragraph_object(rec.get("object"), sid):
            continue
        if rec.get("heading_level") or rec.get("title_kind"):
            out.add(sid)
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if not sid or is_table_paragraph_object(item.get("object"), sid):
            continue
        if parse_heading_level(item.get("heading_level")) or parse_title_kind(item.get("title_kind")):
            out.add(sid)
    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        if is_table_paragraph_object(spec.get("object"), rid):
            continue
        if parse_heading_level(spec.get("heading_level")) or parse_title_kind(spec.get("title_kind")):
            out.add(str(rid))
    return out
