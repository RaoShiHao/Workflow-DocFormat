"""After human edit of template.docx, extract style_tree from **styles.json locators**.

Primary path (matches seed contract):
- paragraph: ``styles.paragraph[role].para_index`` → ``/body/p[N]`` (effective + remapped style)
- section: ``styles.section[role]`` → ``/section[N]`` (+ header/footer refs)
- table_structure: ``styles.table_structure[role]`` → ``/body/table[N]``（以 styles/map 绝对索引为准）
- table_cell: ``styles.table_cell[role].{table,row,col}`` → cell path（同样只认 styles/map）
- ``tables.json`` 仅作格位/样例网格辅助；其 ``table_index`` 必须与 styles/map/docx 一致，
  过期的大纲序号（exhibit 裁掉的表）写回时会跳过并剪枝
- image: ``styles.image[role]`` → N-th inline picture in body order (OOXML extent / ``p/r``)
- run: keep sample locator (optional enrichment)

Do **not** rely on ``/styles/{ParaRoleId}`` — Word often rewrites custom styleIds to
``a3``/``aa`` while keeping Chinese display names; role_id lookup then fails.

Usage:
  python LongDocFmtEval/pipeline_auto/seed/extract_style_tree.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/work/step4_seeds/field_survey_theme1
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.officecli_runner import close_docx, get_json  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: E402
    dump_json,
    load_json,
    normalize_section_role_entry,
    normalize_table_role_entry,
    table_cells_chrome_of,
    table_format_of,
)
from LongDocFmtEval.pipeline_auto.seed.table_chrome_presets import enrich_cells_chrome  # noqa: E402


def _fmt_cm(val_cm: float) -> str:
    s = f"{val_cm:.2f}".rstrip("0").rstrip(".")
    return f"{s}cm"


def _norm_cm(v: Any) -> str:
    s = str(v).strip()
    if s.endswith(".0cm"):
        return s.replace(".0cm", "cm")
    return s


def list_inline_pictures(
    doc: Path,
    *,
    with_paths: bool = False,
    max_needed: int | None = None,
) -> list[dict[str, Any]]:
    """Body-order inline pictures with width/height (and optional officecli path).

    Word/officecli usually expose pictures as ``/body/p[i]/r[j]`` (type=picture),
    **not** ``/body/image[N]``. Size comes from OOXML ``wp:extent`` (EMU→cm).
    """
    out: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(doc) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
    except Exception:  # noqa: BLE001
        xml = ""
    for attrs in re.findall(r"<wp:extent\s+([^>]+)>", xml):
        m_cx = re.search(r'cx="(\d+)"', attrs)
        m_cy = re.search(r'cy="(\d+)"', attrs)
        if not m_cx or not m_cy:
            continue
        out.append({
            "width": _fmt_cm(int(m_cx.group(1)) / 360000.0),
            "height": _fmt_cm(int(m_cy.group(1)) / 360000.0),
        })
    if max_needed is not None:
        out = out[: max(0, int(max_needed))]
    if not out or not with_paths:
        return out

    need = len(out)
    found = 0
    for pi in range(1, 600):
        if found >= need:
            break
        try:
            payload = get_json(doc, f"/body/p[{pi}]")
        except Exception:  # noqa: BLE001
            if pi > 30 and found == 0:
                break
            continue
        data = payload.get("data") or payload
        results = data.get("results") or []
        if not results or not isinstance(results[0], dict):
            continue
        para = results[0]
        para_align = (para.get("format") or {}).get("align") or (
            (para.get("format") or {}).get("effective.alignment")
        )
        for child in para.get("children") or []:
            if not isinstance(child, dict) or child.get("type") != "picture":
                continue
            if found >= need:
                break
            fmt = dict(child.get("format") or {})
            slot = out[found]
            if fmt.get("width"):
                slot["width"] = _norm_cm(fmt["width"])
            if fmt.get("height"):
                slot["height"] = _norm_cm(fmt["height"])
            if fmt.get("wrap"):
                slot["wrap"] = fmt["wrap"]
            if para_align:
                slot["hAlign"] = para_align
            path = str(child.get("path") or f"/body/p[{pi}]/r[1]")
            m = re.search(r"/r\[(\d+)\]", path)
            ri = m.group(1) if m else "1"
            slot["path"] = f"/body/p[{pi}]/r[{ri}]"
            found += 1
    return out


# Runtime paths / duplicates — not human-editable section format.
_SECTION_PROP_SKIP = {
    "headerRef",
    "headerRef.default",
    "headerRef.first",
    "headerRef.even",
    "footerRef",
    "footerRef.default",
    "footerRef.first",
    "footerRef.even",
    "sectionType",  # duplicate of type
}

# Keys we keep from prior props when readback omits them (structure / list chrome).
_PRESERVE_IF_ABSENT = {
    "outlineLvl", "keepNext", "keepLines", "pageBreakBefore",
    "listStyle", "numId", "ilvl", "widowControl",
}

# paragraph effective.* → officecli write keys
_EFFECTIVE_TO_PROP = {
    "effective.size": "size",
    "effective.bold": "bold",
    "effective.italic": "italic",
    "effective.alignment": "align",
    "effective.spaceBefore": "spaceBefore",
    "effective.spaceAfter": "spaceAfter",
    "effective.lineSpacing": "lineSpacing",
    "effective.firstLineIndent": "firstLineIndent",
    "effective.hangingIndent": "hangingIndent",
    "effective.leftIndent": "leftIndent",
    "effective.rightIndent": "rightIndent",
    "effective.outlineLvl": "outlineLvl",
    "effective.color": "color",
    "effective.underline": "underline",
    "effective.keepNext": "keepNext",
}


def _first_result(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or payload
    results = data.get("results") or []
    if not results:
        return {}
    return dict(results[0]) if isinstance(results[0], dict) else {}


def _first_format(payload: dict[str, Any]) -> dict[str, Any]:
    fmt = _first_result(payload).get("format") or {}
    return dict(fmt) if isinstance(fmt, dict) else {}


def _clean_props(fmt: dict[str, Any]) -> dict[str, Any]:
    skip_exact = {
        "path", "type", "text", "childCount", "children", "id", "name", "customStyle",
        "basedOn", "basedOn.path", "next", "linked", "uiPriority", "qFormat", "semiHidden",
        "unhideWhenUsed", "personal", "personalCompose", "personalReply", "aliases",
        "paraId", "style", "styleId", "styleName", "lineRule",
        *_SECTION_PROP_SKIP,
    }
    out = {}
    for k, v in fmt.items():
        if k in skip_exact or v in (None, "", "none"):
            continue
        if k.endswith(".path") or k.endswith(".xml") or k.startswith("markRPr"):
            continue
        if k.startswith("effective."):
            continue
        if k.startswith("headerRef") or k.startswith("footerRef"):
            continue
        out[k] = v
    # Normalize font.ascii → font.latin for write-back
    if "font.latin" not in out:
        latin = out.get("font.ascii") or out.get("font.hAnsi")
        if latin:
            out["font.latin"] = latin
            out.setdefault("font", latin)
    return out


def _props_from_effective(fmt: dict[str, Any]) -> dict[str, Any]:
    """Map officecli paragraph ``effective.*`` readback to writable props."""
    out: dict[str, Any] = {}
    for ek, pk in _EFFECTIVE_TO_PROP.items():
        v = fmt.get(ek)
        if v not in (None, "", "none"):
            out[pk] = v
    ea = fmt.get("effective.font.eastAsia")
    if ea:
        out["font.ea"] = ea
    latin = fmt.get("effective.font.ascii") or fmt.get("effective.font.hAnsi")
    if latin:
        out["font.latin"] = latin
        out.setdefault("font", latin)
    return out


def _para_index_for_role(styles: dict, maps: dict, rid: str) -> int | None:
    """Prefer styles.json exemplar; fall back to map.json first occurrence."""
    loc = (styles.get("paragraph") or {}).get(rid)
    if isinstance(loc, dict) and loc.get("para_index") is not None:
        try:
            return int(loc["para_index"])
        except (TypeError, ValueError):
            pass
    idxs = (maps.get("paragraph") or {}).get(rid) or []
    if idxs:
        try:
            return int(idxs[0])
        except (TypeError, ValueError):
            pass
    return None


def _extract_paragraph_props(doc: Path, para_index: int) -> dict[str, Any]:
    """Read exemplar paragraph by body index; merge remapped named-style + effective."""
    payload = get_json(doc, f"/body/p[{int(para_index)}]")
    result = _first_result(payload)
    fmt = dict(result.get("format") or {})
    props: dict[str, Any] = {}

    # 1) Remapped Word styleId (a3/aa/…) — clean style-level props when available
    sid = fmt.get("styleId") or fmt.get("style")
    if sid and str(sid) not in ("a", "Normal", "normal"):
        try:
            sfmt = _clean_props(_first_format(get_json(doc, f"/styles/{sid}")))
            props.update(sfmt)
        except Exception:  # noqa: BLE001
            pass

    # 2) What the user sees on this exemplar (wins over style defaults)
    props.update(_props_from_effective(fmt))
    return props


def _merge_para_props(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = {**(old or {}), **(new or {})}
    for k in _PRESERVE_IF_ABSENT:
        if k in (old or {}) and k not in (new or {}):
            merged[k] = old[k]
    return merged


def _extract_run_props(
    doc: Path,
    styles: dict[str, Any],
    maps: dict[str, Any],
    rid: str,
    loc: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort character props for a run role from its exemplar span."""
    _RUN_KEYS = (
        "bold", "italic", "underline", "superscript", "subscript",
        "color", "size", "caps", "smallcaps",
        "font", "font.ea", "font.latin",
    )
    para_idx = None
    # Prefer paragraph_objects[block_id].para_index
    oid = loc.get("object_id") or loc.get("block_id")
    pobjs = maps.get("paragraph_objects") or {}
    if oid and isinstance(pobjs.get(oid), dict):
        try:
            para_idx = int(pobjs[oid].get("para_index"))
        except (TypeError, ValueError):
            para_idx = None
    if para_idx is None:
        # Fall back: first paragraph that maps to any host role mentioned in sample
        host = loc.get("host_role") or loc.get("paragraph_role")
        if host:
            para_idx = _para_index_for_role(styles, maps, str(host))
    if para_idx is None:
        return {}

    try:
        result = _first_result(get_json(doc, f"/body/p[{int(para_idx)}]"))
    except Exception:  # noqa: BLE001
        return {}
    if not result:
        return {}

    children = result.get("children") or []
    start = loc.get("start")
    end = loc.get("end")
    chosen = None
    # Prefer a child run whose text overlaps [start,end) when indices look like offsets
    if children and isinstance(start, int) and isinstance(end, int) and end > start:
        pos = 0
        for ch in children:
            if not isinstance(ch, dict):
                continue
            t = str(ch.get("text") or "")
            n = len(t)
            if n == 0:
                continue
            if pos + n > start and pos < end:
                chosen = ch
                break
            pos += n
    if chosen is None:
        # Else: first non-empty child that differs from plain paragraph effective
        for ch in children:
            if isinstance(ch, dict) and (ch.get("text") or "").strip():
                chosen = ch
                break
    if not isinstance(chosen, dict):
        return {}

    cfmt = dict(chosen.get("format") or {})
    props: dict[str, Any] = {}
    # Direct keys
    for k in _RUN_KEYS:
        v = cfmt.get(k)
        if v not in (None, "", "none", False):
            props[k] = v
    # effective.* on the run
    eff = _props_from_effective(cfmt)
    for k in _RUN_KEYS:
        if k in eff and k not in props:
            props[k] = eff[k]
    # Booleans that may be False explicitly — skip False except we want True only
    # superscript etc. already filtered
    # font from effective
    ea = cfmt.get("effective.font.eastAsia") or cfmt.get("font.ea")
    latin = cfmt.get("effective.font.ascii") or cfmt.get("effective.font.hAnsi") or cfmt.get("font.latin")
    if ea:
        props["font.ea"] = ea
    if latin:
        props["font.latin"] = latin
        props.setdefault("font", latin)
    for flag in ("bold", "italic", "superscript", "subscript", "underline"):
        ev = cfmt.get(f"effective.{flag}")
        if ev in (True, "true", "1", 1) or str(ev).lower() == "true":
            props[flag] = True if flag != "underline" else (ev if not isinstance(ev, bool) else "single")
    return {k: v for k, v in props.items() if v not in (None, "", "none")}


def _hf_from_path(
    doc: Path,
    path: str | None,
    *,
    prior: dict[str, Any] | None = None,
    force_type: str | None = None,
) -> dict[str, Any] | None:
    """Read /header[N] or /footer[N] into style_tree chrome props (text/align/size/…).

    Live docx wins: an empty/cleared header must write ``text: ""``, not revive
    ``prior["text"]`` from an older style_tree (that bug made deleted cover headers
    reappear on the next gold render).
    """
    if not path or not isinstance(path, str):
        return None
    try:
        result = _first_result(get_json(doc, path))
    except Exception:  # noqa: BLE001
        return None
    if not result:
        return None

    fmt = dict(result.get("format") or {})
    chrome: dict[str, Any] = {}
    for k in ("align", "size", "color", "bold", "italic", "font", "type", "direction"):
        v = fmt.get(k)
        if v not in (None, "", "none"):
            chrome[k] = v

    children = result.get("children") or []
    if children and isinstance(children[0], dict):
        cfmt = children[0].get("format") or {}
        ea = cfmt.get("effective.font.eastAsia")
        latin = cfmt.get("effective.font.ascii") or cfmt.get("effective.font.hAnsi")
        if ea and "font.ea" not in chrome:
            chrome["font.ea"] = ea
        if latin:
            chrome.setdefault("font.latin", latin)
            chrome.setdefault("font", latin)

    if force_type:
        chrome["type"] = force_type

    prior = prior if isinstance(prior, dict) else {}
    raw_text = result.get("text")
    # Concatenate child texts when parent text is missing but runs still exist
    if raw_text is None and children:
        bits = []
        for ch in children:
            if isinstance(ch, dict) and ch.get("text") not in (None,):
                bits.append(str(ch.get("text") or ""))
        if bits:
            raw_text = "".join(bits)
    if raw_text is None:
        raw_text = ""
    text_stripped = str(raw_text).strip()

    is_footer = path.startswith("/footer")

    # field=page is add-only; readback often shows a digit — keep field only when
    # the footer still looks like a page field (digit or empty with prior field).
    if is_footer and prior.get("field") == "page" and (
        not text_stripped or text_stripped.isdigit()
    ):
        chrome["field"] = "page"
        # Do not also keep a stale literal page number as text
        chrome.pop("text", None)
    else:
        # Explicit live text, including "" when the user cleared the header/footer.
        chrome["text"] = str(raw_text)
        chrome.pop("field", None)

    if force_type == "first" and "type" not in chrome:
        chrome["type"] = "first"

    return chrome


def _extract_section_chrome(doc: Path, sec_fmt: dict[str, Any], entry: dict[str, Any]) -> None:
    """Fill entry header/footer/header_first from section headerRef/footerRef paths."""
    href = sec_fmt.get("headerRef.default") or sec_fmt.get("headerRef")
    fref = sec_fmt.get("footerRef.default") or sec_fmt.get("footerRef")
    href_first = sec_fmt.get("headerRef.first")

    prior_h = entry.get("header") if isinstance(entry.get("header"), dict) else None
    prior_f = entry.get("footer") if isinstance(entry.get("footer"), dict) else None
    prior_h1 = entry.get("header_first") if isinstance(entry.get("header_first"), dict) else None

    if href:
        hdr = _hf_from_path(doc, href, prior=prior_h)
        entry["header"] = hdr if hdr is not None else {"text": ""}
    else:
        # No default header part → treat as intentionally empty (do not keep stale text)
        entry["header"] = {"text": "", "align": "center", "size": "9pt", "color": "#B0B0B0"}

    if fref:
        ftr = _hf_from_path(doc, fref, prior=prior_f)
        entry["footer"] = ftr if ftr is not None else {"text": ""}
    else:
        entry["footer"] = {"text": ""}

    if href_first or entry.get("header_first") or sec_fmt.get("titlePage"):
        if href_first:
            h1 = _hf_from_path(doc, href_first, prior=prior_h1, force_type="first")
            entry["header_first"] = h1 if h1 is not None else {"text": "", "type": "first"}
        else:
            entry["header_first"] = {"text": "", "type": "first"}
    elif "header_first" in entry and not href_first:
        # Drop stale first-page header if doc no longer has one
        entry.pop("header_first", None)


def _valid_table_indices(styles: dict[str, Any], maps: dict[str, Any] | None = None) -> set[int]:
    """Absolute table indices present in the current docx (styles.json / map.json)."""
    out: set[int] = set()
    for idx in (styles.get("table_structure") or {}).values():
        try:
            out.add(int(idx))
        except (TypeError, ValueError):
            continue
    for spec in (styles.get("table") or {}).values():
        if not isinstance(spec, dict):
            continue
        idx = spec.get("table_index")
        if idx is None and isinstance(spec.get("table_structure"), dict):
            idx = spec["table_structure"].get("table")
        try:
            out.add(int(idx))
        except (TypeError, ValueError):
            continue
    for idxs in ((maps or {}).get("table_structure") or {}).values():
        seq = idxs if isinstance(idxs, list) else [idxs]
        for i in seq:
            try:
                out.add(int(i))
            except (TypeError, ValueError):
                continue
    for spec in ((maps or {}).get("table") or {}).values():
        if not isinstance(spec, dict):
            continue
        for i in (spec.get("tables") or []):
            try:
                out.add(int(i))
            except (TypeError, ValueError):
                continue
    return out


def extract(seed_dir: Path) -> dict[str, Any]:
    seed_dir = seed_dir.resolve()
    doc = seed_dir / "template.docx"
    if not doc.exists():
        raise FileNotFoundError(doc)
    styles = load_json(seed_dir / "styles.json")
    maps = load_json(seed_dir / "map.json") if (seed_dir / "map.json").exists() else {}
    tree = load_json(seed_dir / "style_tree.json")
    roles = tree.setdefault("roles", {})

    # paragraph: styles.json / map.json para_index → /body/p[N]
    para_ok = para_fail = 0
    para_locs = dict(styles.get("paragraph") or {})
    for rid in (maps.get("paragraph") or {}):
        para_locs.setdefault(rid, {"style_id": rid})

    for rid, loc in para_locs.items():
        idx = _para_index_for_role(styles, maps, rid)
        if idx is None:
            print(f"  [warn] paragraph {rid}: no para_index in styles/map")
            para_fail += 1
            continue
        try:
            new_props = _extract_paragraph_props(doc, idx)
            entry = roles.setdefault(rid, {"object": "paragraph", "props": {}})
            entry["object"] = "paragraph"
            entry["props"] = _merge_para_props(entry.get("props") or {}, new_props)
            if "display_name" not in entry:
                entry["display_name"] = rid
            styles.setdefault("paragraph", {})[rid] = {
                "style_id": rid,
                "para_index": idx,
            }
            para_ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] paragraph {rid} @ p[{idx}]: {exc}")
            para_fail += 1

    # sections: page props + header/footer chrome
    for rid, idx in (styles.get("section") or {}).items():
        try:
            payload = get_json(doc, f"/section[{int(idx)}]")
            sec_fmt = _first_format(payload)
            page_props = _clean_props(sec_fmt)
            old_props = {
                k: v
                for k, v in ((roles.get(rid) or {}).get("props") or {}).items()
                if not str(k).startswith(("headerRef", "footerRef")) and k != "sectionType"
            }
            entry = roles.setdefault(rid, {"object": "section", "props": {}})
            entry["object"] = "section"
            entry["props"] = {**old_props, **page_props}
            _extract_section_chrome(doc, sec_fmt, entry)
            roles[rid] = normalize_section_role_entry(entry)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] section {rid}: {exc}")

    # tables — layer 1 structure only（索引以 styles.json 为准，对应 docx 实表）
    # Prefer nested styles.table[Tbl*].table_index; fall back to flat table_structure.
    valid_tbl = _valid_table_indices(styles, maps)
    table_index_by_role: dict[str, int] = {}
    nested_tables = styles.get("table") or {}
    if isinstance(nested_tables, dict):
        for rid, spec in nested_tables.items():
            if not isinstance(spec, dict):
                continue
            idx = spec.get("table_index")
            if idx is None and isinstance(spec.get("table_structure"), dict):
                idx = spec["table_structure"].get("table")
            try:
                table_index_by_role[str(rid)] = int(idx)
            except (TypeError, ValueError):
                continue
    for rid, idx in (styles.get("table_structure") or {}).items():
        table_index_by_role.setdefault(str(rid), idx)

    for rid, idx in table_index_by_role.items():
        try:
            ti = int(idx)
        except (TypeError, ValueError):
            continue
        if valid_tbl and ti not in valid_tbl:
            print(f"  [warn] table {rid}: styles index {ti} not in doc map {sorted(valid_tbl)}")
            continue
        try:
            fmt = _clean_props(_first_format(get_json(doc, f"/body/table[{ti}]")))
            if not fmt:
                fmt = _clean_props(_first_format(get_json(doc, f"/body/tbl[{ti}]")))
            entry = roles.setdefault(rid, {"object": "table"})
            tf = table_format_of(entry)
            for k in ("align", "width", "layout"):
                if fmt.get(k) not in (None, "", "none"):
                    tf[k] = fmt[k]
            entry["table_format"] = tf
            entry["cells"] = table_cells_chrome_of(entry)
            entry["object"] = "table"
            roles[rid] = normalize_table_role_entry(entry)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] table {rid}: {exc}")

    # cells — layer 2 chrome per Tbl* from that table's own cells (tables.json);
    # layer 3 text → ParaTbl* props from styles.table_cell exemplars.
    # Do NOT attribute ParaTbl* chrome to the first outline table that reuses the
    # same Para role (cover info would steal project-list header fill/borders).
    # **Critical**: tables.json may still list outline-order indices (1..N full plan)
    # while preview/exhibit docx only has a subset — only probe indices in styles/map.
    _TEXT_KEYS = {
        "font", "font.latin", "font.ea", "font.ascii", "font.hAnsi",
        "size", "bold", "italic", "align", "color", "underline",
    }
    _CHROME_KEYS = {
        "valign", "fill", "shading",
        "border.all", "border.top", "border.bottom", "border.left", "border.right",
    }

    def _read_cell_fmt(t: int, r: int, c: int) -> dict[str, Any]:
        if valid_tbl and t not in valid_tbl:
            return {}
        paths = [
            f"/body/table[{t}]/row[{r}]/cell[{c}]",
            f"/body/tbl[{t}]/tr[{r}]/tc[{c}]",
            f"/body/table[{t}]/row[{r}]/cell[{c}]/p[1]",
        ]
        for path in paths:
            try:
                cell_payload = get_json(doc, path)
                fmt = _clean_props(_first_format(cell_payload))
                if not any(k in fmt for k in _TEXT_KEYS):
                    eff = _props_from_effective(_first_format(cell_payload))
                    fmt = {**fmt, **eff}
                if fmt:
                    return fmt
            except Exception:
                continue
        return {}

    def _split_text_chrome(fmt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        text_props = {k: v for k, v in fmt.items() if k in _TEXT_KEYS or k.startswith("font")}
        chrome = {
            k: v for k, v in fmt.items()
            if k in _CHROME_KEYS or str(k).startswith("border")
        }
        text_props = {
            k: v for k, v in text_props.items()
            if k not in ("width", "_widthDerived") and not str(k).startswith("_")
        }
        return text_props, chrome

    # Per-table chrome: prefer styles/map exemplars; tables.json only if index is live
    tables_path = seed_dir / "tables.json"
    tables_data = load_json(tables_path) if tables_path.exists() else {}
    skipped_stale = 0
    if tables_data:
        for _oid, tspec in tables_data.items():
            if not isinstance(tspec, dict):
                continue
            tbl_rid = str(tspec.get("role_id") or "")
            try:
                ti = int(tspec.get("table_index") or 0)
            except (TypeError, ValueError):
                continue
            if not tbl_rid or ti < 1:
                continue
            if valid_tbl and ti not in valid_tbl:
                skipped_stale += 1
                continue
            seen_pos: dict[str, tuple[int, int]] = {}
            for ri, row in enumerate(tspec.get("grid") or [], start=1):
                if not isinstance(row, list):
                    continue
                for ci, cell in enumerate(row, start=1):
                    if not isinstance(cell, dict):
                        continue
                    pos = str(cell.get("pos") or "")
                    if pos and pos not in seen_pos:
                        seen_pos[pos] = (ri, ci)
            if not seen_pos:
                continue
            tbl_entry = roles.setdefault(tbl_rid, {"object": "table"})
            cells = dict(table_cells_chrome_of(tbl_entry))
            for pos, (ri, ci) in seen_pos.items():
                fmt = _read_cell_fmt(ti, ri, ci)
                if not fmt:
                    continue
                _, chrome = _split_text_chrome(fmt)
                if chrome:
                    cells[pos] = chrome
            if cells:
                tbl_entry["cells"] = cells
                tbl_entry["table_format"] = table_format_of(tbl_entry)
                tbl_entry["object"] = "table"
                # Sanitize known presets (e.g. info_underline) so shared ParaTbl*
                # residue / Word default grid never sticks on label_value tables.
                tbl_entry["cells"] = enrich_cells_chrome(tbl_entry)
                roles[tbl_rid] = normalize_table_role_entry(tbl_entry)
    if skipped_stale:
        print(
            f"  [tables] skipped {skipped_stale} tables.json entries whose table_index "
            f"not in doc (styles/map={sorted(valid_tbl)}) — stale full-outline indices"
        )
        # Prune tables.json so reapply / later extract stay aligned with docx
        if tables_path.exists() and valid_tbl:
            pruned = {
                oid: spec for oid, spec in tables_data.items()
                if isinstance(spec, dict) and int(spec.get("table_index") or 0) in valid_tbl
            }
            if len(pruned) != len(tables_data):
                dump_json(tables_path, pruned)
                print(f"  [tables] pruned tables.json → {len(pruned)} live entries")

    # Nested styles.table[Tbl*].table_cell → read chrome back into role_params.cells
    for tbl_rid, tspec in (styles.get("table") or {}).items():
        if not isinstance(tspec, dict):
            continue
        cell_locs = tspec.get("table_cell") or {}
        if not isinstance(cell_locs, dict) or not cell_locs:
            continue
        try:
            ti = int(tspec.get("table_index") or (tspec.get("table_structure") or {}).get("table") or 0)
        except (TypeError, ValueError):
            ti = 0
        if ti < 1 or (valid_tbl and ti not in valid_tbl):
            continue
        tbl_entry = roles.setdefault(str(tbl_rid), {"object": "table"})
        cells = dict(table_cells_chrome_of(tbl_entry))
        for cell_style, loc in cell_locs.items():
            if str(cell_style).startswith("row_"):
                continue
            if not isinstance(loc, dict):
                continue
            try:
                r, c = int(loc.get("row") or 0), int(loc.get("col") or 0)
                t = int(loc.get("table") or ti)
            except (TypeError, ValueError):
                continue
            if r < 1 or c < 1:
                continue
            fmt = _read_cell_fmt(t, r, c)
            if not fmt:
                continue
            _, chrome = _split_text_chrome(fmt)
            if chrome:
                cells[str(cell_style)] = chrome
        if cells:
            tbl_entry["cells"] = enrich_cells_chrome({**tbl_entry, "cells": cells})
            tbl_entry["table_format"] = table_format_of(tbl_entry)
            tbl_entry["object"] = "table"
            roles[str(tbl_rid)] = normalize_table_role_entry(tbl_entry)

    for rid, loc in (styles.get("table_cell") or {}).items():
        if not isinstance(loc, dict):
            continue
        try:
            t, r, c = int(loc["table"]), int(loc["row"]), int(loc["col"])
        except (KeyError, TypeError, ValueError):
            continue
        if valid_tbl and t not in valid_tbl:
            continue
        fmt = _read_cell_fmt(t, r, c)
        if not fmt:
            pidx = _para_index_for_role(styles, maps, rid)
            if pidx is not None:
                try:
                    fmt = _extract_paragraph_props(doc, pidx)
                except Exception:  # noqa: BLE001
                    fmt = {}
        if not fmt:
            continue

        text_props, _chrome = _split_text_chrome(fmt)
        entry = roles.setdefault(rid, {"object": "paragraph", "props": {}})
        entry["object"] = "paragraph"
        entry["in_table_cell"] = True
        if text_props:
            entry["props"] = {
                k: v for k, v in {**(entry.get("props") or {}), **text_props}.items()
                if k not in ("width", "_widthDerived") and not str(k).startswith("_")
            }

    for rid, loc in (styles.get("run") or {}).items():
        if not isinstance(loc, dict):
            continue
        entry = roles.setdefault(rid, {"object": "run", "props": {}})
        entry["object"] = "run"
        entry.setdefault("sample", loc)
        # Try to refresh character props from the exemplar paragraph / run span
        new_run_props = _extract_run_props(doc, styles, maps, rid, loc)
        if new_run_props:
            old = dict(entry.get("props") or {})
            entry["props"] = {**old, **new_run_props}
        elif not entry.get("props"):
            entry.setdefault("props", {})

    # images — size / align / wrap (human may resize drawings in Word)
    # Index N in styles.image is the N-th inline picture in document order
    # (not /body/image[N], which officecli often cannot resolve).
    _IMAGE_KEYS = ("width", "height", "hAlign", "align", "wrap", "alt")
    img_ok = img_fail = 0
    img_locs = styles.get("image") or {}
    max_img_idx = 0
    for idx in img_locs.values():
        try:
            max_img_idx = max(max_img_idx, int(idx))
        except (TypeError, ValueError):
            continue
    pictures = list_inline_pictures(doc, with_paths=False, max_needed=max_img_idx) if img_locs else []
    for rid, idx in img_locs.items():
        try:
            ii = int(idx)
        except (TypeError, ValueError):
            continue
        if ii < 1 or ii > len(pictures):
            img_fail += 1
            print(f"  [warn] image {rid}#{idx}: no picture at body index (found {len(pictures)})")
            continue
        fmt = {k: pictures[ii - 1][k] for k in _IMAGE_KEYS if pictures[ii - 1].get(k) not in (None, "", "none")}
        if not any(fmt.get(k) for k in ("width", "height", "hAlign")):
            img_fail += 1
            print(f"  [warn] image {rid}#{idx}: no readable size/align")
            continue
        entry = roles.setdefault(rid, {"object": "image", "props": {}})
        entry["object"] = "image"
        old = dict(entry.get("props") or {})
        new_props = {**old}
        for k in _IMAGE_KEYS:
            if fmt.get(k) not in (None, "", "none"):
                new_props[k] = fmt[k]
        if new_props.get("align") and not new_props.get("hAlign"):
            new_props["hAlign"] = new_props.pop("align")
        elif "align" in new_props and "hAlign" in new_props:
            new_props.pop("align", None)
        entry["props"] = {
            k: v for k, v in new_props.items()
            if v not in (None, "", "none") and not str(k).startswith("_")
        }
        img_ok += 1

    close_docx(doc)
    dump_json(seed_dir / "styles.json", styles)
    tree["extracted_from"] = str(doc).replace("\\", "/")
    dump_json(seed_dir / "style_tree.json", tree)
    print(
        f"[extract] updated {seed_dir / 'style_tree.json'} "
        f"({len(roles)} roles; paragraphs ok={para_ok} fail={para_fail}; "
        f"images ok={img_ok} fail={img_fail})"
    )
    return tree

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True)
    args = ap.parse_args()
    extract(Path(args.seed_dir))


if __name__ == "__main__":
    main()
