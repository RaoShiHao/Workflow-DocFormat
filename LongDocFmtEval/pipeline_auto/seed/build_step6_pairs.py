"""Step6/7 · emit all format-adjustment **variants** (no D×T cartesian product).

D-side (待调正文，来自 step5/6 ``document.docx``):
  input_plain.docx / input_partial.docx  (+ golden pointer in manifest)

T-side (格式需求呈现):
  t_text.md                  相对 plain 的差分需求（细桶共性 + 角色例外；中/英随 locale）
  t_text_from_partial.md     相对 partial 的差分（若有）
  t_style_propose.md         各 style 用途短述（display_name + description）
  t_template_styled.docx     命名样式挂载（含 {StyleName} 标记，指向 template.docx）
  t_template_style.docx      复制 template.docx 并去掉段落前 {StyleName}
  t_template_direct.docx     Normal + 相对 Normal 的直接格式差量（隐藏自定义 style 名）

合成数据集时再挑选组合；本步只把每种变体落到磁盘。
"""
from __future__ import annotations

import copy
import random
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from lxml import etree

from LongDocFmtEval.pipeline_auto.auto_paths import (
    OUTLINES_DIR,
    SEEDS_DIR,
    TEMPLATES_DIR,
    drop_redundant_copies,
    rel_from,
    resolve_work_file,
)
from LongDocFmtEval.pipeline_auto.seed.heading_contract import (
    load_heading_index,
    word_style_for_role,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

NSMAP = {"w": W, "wp": WP}

# A4 + 1 inch margins (twips; 1440 twips = 1")
_A4_W, _A4_H = "11906", "16838"
_MARGIN = "1440"
_EMU_PER_CM = 360000
_MIN_CM = 2.0
_MAX_CM = 15.5  # ~ A4 content width with 1" margins

_HEADING_ROLE_TO_STYLE = {
    # 标题级别（结构层级）→ Word 内置样式；不是正文内容层级
    "ParaHeading1": "Heading1",
    "ParaHeading2": "Heading2",
    "ParaHeading3": "Heading3",
    "ParaHeading4": "Heading4",
    "ParaTocTitle": "Heading1",       # 目录标题
    "ParaCoverTitle": "Title",        # 封面主标题
    "ParaCoverSubtitle": "Subtitle",  # 封面副题
    "ParaAppendixTitle": "Heading1",  # 前言/附录一类结构标题
}

# Built-in-ish defaults (not step3 ParaHeading* fonts) for partial inputs
_HEADING_STYLE_SPECS = {
    "Title": {"name": "Title", "outline": None, "size_half_pt": "56", "bold": True},
    "Subtitle": {"name": "Subtitle", "outline": None, "size_half_pt": "28", "bold": False},
    "Heading1": {"name": "heading 1", "outline": "0", "size_half_pt": "32", "bold": True},
    "Heading2": {"name": "heading 2", "outline": "1", "size_half_pt": "26", "bold": True},
    "Heading3": {"name": "heading 3", "outline": "2", "size_half_pt": "24", "bold": True},
    "Heading4": {"name": "heading 4", "outline": "3", "size_half_pt": "24", "bold": True},
}


def _qn(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NSMAP[prefix]}}}{local}"


def _local(tag: str | None) -> str:
    if not tag:
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _cm_to_emu(cm: float) -> int:
    return int(round(cm * _EMU_PER_CM))


def _emu_to_cm(emu: int | str) -> float:
    return int(emu) / _EMU_PER_CM


def _fmt_cm(cm: float) -> str:
    s = f"{cm:.2f}".rstrip("0").rstrip(".")
    return f"{s}cm"


def _read_docx_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _write_docx_parts(path: Path, parts: dict[str, bytes]) -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())


def _parse(xml: bytes) -> etree._Element:
    return etree.fromstring(xml)


def _dumps(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _ensure_child(parent: etree._Element, tag: str) -> etree._Element:
    q = _qn(tag)
    el = parent.find(q)
    if el is None:
        el = etree.SubElement(parent, q)
    return el


def _wattr(el: etree._Element, name: str, value: str) -> None:
    el.set(f"{{{W}}}{name}", value)


def _make_border(tag: str, *, val: str = "single", sz: str = "4") -> etree._Element:
    el = etree.Element(_qn(f"w:{tag}"))
    _wattr(el, "val", val)
    _wattr(el, "sz", sz)
    _wattr(el, "space", "0")
    _wattr(el, "color", "auto")
    return el


def _default_borders(container_tag: str) -> etree._Element:
    """tblBorders or tcBorders with single grid."""
    box = etree.Element(_qn(f"w:{container_tag}"))
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        if container_tag == "tcBorders" and side.startswith("inside"):
            continue
        box.append(_make_border(side))
    return box


def _normalize_sect_pr(sect_pr: etree._Element) -> None:
    # Drop headers / footers / title page
    for child in list(sect_pr):
        loc = _local(child.tag)
        if loc in {
            "headerReference", "footerReference", "titlePg",
            "pgNumType", "cols",
        }:
            sect_pr.remove(child)

    pg_sz = _ensure_child(sect_pr, "w:pgSz")
    _wattr(pg_sz, "w", _A4_W)
    _wattr(pg_sz, "h", _A4_H)
    # drop orient if present → portrait
    if f"{{{W}}}orient" in pg_sz.attrib:
        del pg_sz.attrib[f"{{{W}}}orient"]

    pg_mar = _ensure_child(sect_pr, "w:pgMar")
    for k in ("top", "right", "bottom", "left", "header", "footer", "gutter"):
        _wattr(pg_mar, k, _MARGIN if k != "gutter" else "0")

    cols = etree.Element(_qn("w:cols"))
    _wattr(cols, "space", "720")
    _wattr(cols, "num", "1")
    # insert cols after pgMar if missing
    existing_cols = sect_pr.find(_qn("w:cols"))
    if existing_cols is not None:
        sect_pr.remove(existing_cols)
    # place after pgMar
    pg_mar_el = sect_pr.find(_qn("w:pgMar"))
    if pg_mar_el is not None:
        pg_mar_el.addnext(cols)
    else:
        sect_pr.append(cols)


def _strip_run_formatting(r: etree._Element) -> None:
    r_pr = r.find(_qn("w:rPr"))
    if r_pr is not None:
        r.remove(r_pr)


def _simplify_paragraph(p: etree._Element, *, style_id: str = "Normal") -> None:
    """Keep sectPr / page breaks; force style; drop direct p/r formatting & lists."""
    p_pr = p.find(_qn("w:pPr"))
    sect_pr = None
    if p_pr is not None:
        sect_el = p_pr.find(_qn("w:sectPr"))
        if sect_el is not None:
            sect_pr = copy.deepcopy(sect_el)
            _normalize_sect_pr(sect_pr)
        p.remove(p_pr)

    new_ppr = etree.Element(_qn("w:pPr"))
    p_style = etree.SubElement(new_ppr, _qn("w:pStyle"))
    _wattr(p_style, "val", style_id)
    # force left align (cancel center)
    jc = etree.SubElement(new_ppr, _qn("w:jc"))
    _wattr(jc, "val", "left")
    if sect_pr is not None:
        new_ppr.append(sect_pr)
    p.insert(0, new_ppr)

    for r in p.findall(_qn("w:r")):
        _strip_run_formatting(r)
    # hyperlink wrappers may contain runs
    for link in p.findall(_qn("w:hyperlink")):
        for r in link.findall(_qn("w:r")):
            _strip_run_formatting(r)


def _reset_table(tbl: etree._Element) -> None:
    tbl_pr = tbl.find(_qn("w:tblPr"))
    if tbl_pr is None:
        tbl_pr = etree.Element(_qn("w:tblPr"))
        tbl.insert(0, tbl_pr)

    # Remove visual style / borders / shading; install default grid
    for child in list(tbl_pr):
        loc = _local(child.tag)
        if loc in {"tblBorders", "tblStyle", "shd", "tblLook"}:
            tbl_pr.remove(child)

    style = etree.Element(_qn("w:tblStyle"))
    _wattr(style, "val", "TableGrid")
    tbl_pr.insert(0, style)
    borders = _default_borders("tblBorders")
    # after style
    style.addnext(borders)

    for tc in tbl.iter(_qn("w:tc")):
        tc_pr = tc.find(_qn("w:tcPr"))
        if tc_pr is None:
            tc_pr = etree.Element(_qn("w:tcPr"))
            tc.insert(0, tc_pr)
        for child in list(tc_pr):
            loc = _local(child.tag)
            if loc in {"tcBorders", "shd", "tcW"}:
                # keep tcW for layout stability; drop borders/shd
                if loc in {"tcBorders", "shd"}:
                    tc_pr.remove(child)
        # default cell borders
        existing = tc_pr.find(_qn("w:tcBorders"))
        if existing is not None:
            tc_pr.remove(existing)
        tc_pr.append(_default_borders("tcBorders"))
        for p in tc.findall(_qn("w:p")):
            _simplify_paragraph(p, style_id="Normal")


def _perturb_images(root: etree._Element, rng: random.Random) -> list[dict[str, Any]]:
    """Independent ±1cm on width/height; clamp; return log."""
    log: list[dict[str, Any]] = []
    for i, extent in enumerate(root.iter(_qn("wp:extent")), start=1):
        try:
            cx = int(extent.get("cx") or 0)
            cy = int(extent.get("cy") or 0)
        except ValueError:
            continue
        if cx <= 0 or cy <= 0:
            continue
        w0 = _emu_to_cm(cx)
        h0 = _emu_to_cm(cy)
        dw = rng.choice([-1.0, 1.0])
        dh = rng.choice([-1.0, 1.0])
        w1 = min(_MAX_CM, max(_MIN_CM, w0 + dw))
        h1 = min(_MAX_CM, max(_MIN_CM, h0 + dh))
        extent.set("cx", str(_cm_to_emu(w1)))
        extent.set("cy", str(_cm_to_emu(h1)))
        log.append({
            "index": i,
            "width_before": _fmt_cm(w0),
            "height_before": _fmt_cm(h0),
            "width_after": _fmt_cm(w1),
            "height_after": _fmt_cm(h1),
            "delta_w_cm": dw,
            "delta_h_cm": dh,
        })
    return log


def _empty_header_footer(xml: bytes) -> bytes:
    root = _parse(xml)
    # hdr or ftr
    for tag in ("hdr", "ftr"):
        node = root if _local(root.tag) == tag else root.find(_qn(f"w:{tag}"))
        if node is None and _local(root.tag) == tag:
            node = root
        if node is None:
            continue
        # clear children; one empty Normal paragraph
        for child in list(node):
            node.remove(child)
        p = etree.SubElement(node, _qn("w:p"))
        p_pr = etree.SubElement(p, _qn("w:pPr"))
        p_style = etree.SubElement(p_pr, _qn("w:pStyle"))
        _wattr(p_style, "val", "Normal")
        return _dumps(root)
    return xml


def apply_plain_transform(
    src: Path,
    dst: Path,
    *,
    seed: int,
) -> dict[str, Any]:
    """Copy src→dst then strip to plain input."""
    shutil.copy2(src, dst)
    parts = _read_docx_parts(dst)
    rng = random.Random(seed)
    meta: dict[str, Any] = {"seed": seed, "images": []}

    # headers / footers
    for name in list(parts):
        low = name.lower()
        if low.startswith("word/header") and low.endswith(".xml"):
            parts[name] = _empty_header_footer(parts[name])
        elif low.startswith("word/footer") and low.endswith(".xml"):
            parts[name] = _empty_header_footer(parts[name])

    doc_xml = parts.get("word/document.xml")
    if not doc_xml:
        raise FileNotFoundError(f"no word/document.xml in {dst}")
    root = _parse(doc_xml)
    body = root.find(_qn("w:body"))
    if body is None:
        raise RuntimeError("document has no w:body")

    # body-level sectPr (last child)
    for child in list(body):
        loc = _local(child.tag)
        if loc == "sectPr":
            _normalize_sect_pr(child)
        elif loc == "p":
            _simplify_paragraph(child, style_id="Normal")
        elif loc == "tbl":
            _reset_table(child)

    meta["images"] = _perturb_images(root, rng)
    parts["word/document.xml"] = _dumps(root)
    _write_docx_parts(dst, parts)
    return meta


def _body_paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(_qn("w:body"))
    if body is None:
        return []
    return [c for c in body if _local(c.tag) == "p"]


def _ensure_builtin_heading_styles(styles_xml: bytes) -> tuple[bytes, dict[str, str]]:
    """Inject Title/Subtitle/Heading1–4 when missing; use ``Heading4a`` if built-in slot taken."""
    from LongDocFmtEval.pipeline_auto.seed.word_style_resolve import (  # noqa: PLC0415
        allocate_pipeline_style,
    )

    root = _parse(styles_xml)
    resolution: dict[str, str] = {}
    for style_id, spec in _HEADING_STYLE_SPECS.items():
        existing = {
            s.get(f"{{{W}}}styleId")
            for s in root.findall(_qn("w:style"))
        }
        if style_id in existing:
            resolution[style_id] = style_id
            continue
        outline_raw = spec.get("outline")
        outline = int(outline_raw) if outline_raw is not None else None
        actual_id, display_name, is_alt = allocate_pipeline_style(
            style_id,
            str(spec["name"]),
            styles_xml=_dumps(root),
            outline=outline,
        )
        resolution[style_id] = actual_id
        if actual_id in existing:
            continue
        style = etree.SubElement(root, _qn("w:style"))
        _wattr(style, "type", "paragraph")
        _wattr(style, "styleId", actual_id)
        if is_alt:
            etree.SubElement(style, _qn("w:customStyle"))
        name_el = etree.SubElement(style, _qn("w:name"))
        _wattr(name_el, "val", display_name)
        based = etree.SubElement(style, _qn("w:basedOn"))
        _wattr(based, "val", "Normal")
        if outline is not None:
            ui = etree.SubElement(style, _qn("w:uiPriority"))
            _wattr(ui, "val", str(9 + outline))
        etree.SubElement(style, _qn("w:qFormat"))
        p_pr = etree.SubElement(style, _qn("w:pPr"))
        if outline is not None:
            outline_el = etree.SubElement(p_pr, _qn("w:outlineLvl"))
            _wattr(outline_el, "val", str(outline))
        r_pr = etree.SubElement(style, _qn("w:rPr"))
        if spec.get("bold"):
            etree.SubElement(r_pr, _qn("w:b"))
            etree.SubElement(r_pr, _qn("w:bCs"))
        sz = etree.SubElement(r_pr, _qn("w:sz"))
        _wattr(sz, "val", spec["size_half_pt"])
        sz_cs = etree.SubElement(r_pr, _qn("w:szCs"))
        _wattr(sz_cs, "val", spec["size_half_pt"])
    return _dumps(root), resolution


def _style_outline(style_el: etree._Element) -> int | None:
    p_pr = style_el.find(_qn("w:pPr"))
    if p_pr is None:
        return None
    outline = p_pr.find(_qn("w:outlineLvl"))
    if outline is None:
        return None
    try:
        return int(outline.get(f"{{{W}}}val") or "")
    except (TypeError, ValueError):
        return None


def _paragraph_style_id(p: etree._Element) -> str | None:
    ps = p.find(_qn("w:pPr"))
    if ps is None:
        return None
    style = ps.find(_qn("w:pStyle"))
    if style is None:
        return None
    return style.get(f"{{{W}}}val")


def _para_plain_text(p: etree._Element) -> str:
    return "".join((t.text or "") for t in p.iter(_qn("w:t"))).strip()


def heading_targets_from_golden(
    golden: Path,
    *,
    heading_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Read **actual** title paragraphs from golden (not stale map.json indices).

    Prefer stylesheet ``heading_level`` / ``title_kind`` → Heading1–4 / Title.
    Legacy fallback: hardcoded ParaHeading* role ids.
    """
    index = heading_map or {}
    parts = _read_docx_parts(golden)
    root = _parse(parts["word/document.xml"])
    out: list[dict[str, Any]] = []
    for i, p in enumerate(_body_paragraphs(root), start=1):
        src = _paragraph_style_id(p) or ""
        dst = word_style_for_role(src, index) if index else None
        if not dst:
            dst = _HEADING_ROLE_TO_STYLE.get(src)
        if not dst:
            continue
        out.append({
            "para_index": i,
            "source_role": src,
            "style": dst,
            "text": _para_plain_text(p)[:80],
        })
    return out


def apply_partial_headings(
    src_plain: Path,
    dst: Path,
    *,
    golden: Path,
    map_json: dict[str, Any] | None = None,
    role_params: dict[str, Any] | None = None,
    heading_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Copy plain→partial; apply built-in title styles on **golden 标题段**."""
    del map_json, role_params  # kept for call-site compat; indices from golden
    shutil.copy2(src_plain, dst)
    parts = _read_docx_parts(dst)
    style_resolution: dict[str, str] = {}
    if "word/styles.xml" in parts:
        parts["word/styles.xml"], style_resolution = _ensure_builtin_heading_styles(
            parts["word/styles.xml"],
        )
    root = _parse(parts["word/document.xml"])
    paras = _body_paragraphs(root)
    warnings: list[str] = []
    index = heading_map or {}
    targets = heading_targets_from_golden(golden, heading_map=index)
    applied: list[dict[str, Any]] = []

    if not targets:
        warnings.append("golden 中未找到 heading_level / title_kind 对应的标题段落")

    for item in targets:
        idx = int(item["para_index"])
        style_id = str(item["style"])
        style_id = style_resolution.get(style_id, style_id)
        if idx < 1 or idx > len(paras):
            warnings.append(f"heading index {idx} out of range (n={len(paras)})")
            continue
        _simplify_paragraph(paras[idx - 1], style_id=style_id)
        applied.append(item)

    word_map = {sid: rec.get("word_style") for sid, rec in index.items() if rec.get("word_style")}
    parts["word/document.xml"] = _dumps(root)
    _write_docx_parts(dst, parts)
    return {
        "applied": applied,
        "warnings": warnings,
        "heading_map": word_map or dict(_HEADING_ROLE_TO_STYLE),
        "style_resolution": style_resolution,
        "source": "stylesheet.heading_level" if index else "legacy_role_id",
    }


def resolve_template_docx(slug: str, step5_dir: Path) -> Path | None:
    """Prefer polished template.docx from step4 (no copy into step5/step6)."""
    candidates = [
        resolve_work_file(step5_dir, "template.docx"),
        resolve_work_file(step5_dir, "preview.docx"),
        SEEDS_DIR / slug / "template.docx",
        TEMPLATES_DIR / slug / "template.docx",
        TEMPLATES_DIR / slug / "preview.docx",
        step5_dir / "template.docx",
        step5_dir / "preview.docx",
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


def resolve_role_params(slug: str, step5_dir: Path) -> dict[str, Any]:
    """Load executable props from role_params.json; fill names from template.json catalog."""
    params: dict[str, Any] = {}
    for p in (
        resolve_work_file(step5_dir, "role_params.json"),
        TEMPLATES_DIR / slug / "role_params.json",
        SEEDS_DIR / slug / "role_params.json",
    ):
        if not p.is_file():
            continue
        data = load_json(p) or {}
        if isinstance(data.get("roles"), dict):
            data = data["roles"]
        if isinstance(data, dict) and data:
            params = data
            break

    # template.json often carries display_name + description (from library discriminators)
    for p in (
        resolve_work_file(step5_dir, "template.json"),
        TEMPLATES_DIR / slug / "template.json",
        SEEDS_DIR / slug / "template.json",
    ):
        if not p.is_file():
            continue
        roles = (load_json(p) or {}).get("roles") or {}
        if not isinstance(roles, dict) or not roles:
            continue
        if not params:
            from LongDocFmtEval.pipeline_auto.seed.seed_ir import ROLE_FORMAT_KEYS  # noqa: PLC0415
            params = {
                rid: {
                    k: v for k, v in spec.items()
                    if k in ROLE_FORMAT_KEYS or k in ("object", "display_name", "description", "example")
                }
                for rid, spec in roles.items()
                if isinstance(spec, dict)
            }
        else:
            for rid, spec in roles.items():
                if not isinstance(spec, dict):
                    continue
                host = params.setdefault(rid, {})
                if not isinstance(host, dict):
                    continue
                for key in ("display_name", "description", "desc", "discriminators", "object", "example"):
                    if host.get(key) in (None, "") and spec.get(key) not in (None, ""):
                        host[key] = spec[key]
        break
    return params


def resolve_stylesheet(slug: str, step5_dir: Path) -> list[dict[str, Any]]:
    """Load step2 stylesheet list (display_name / description / expectation / style_id)."""
    candidates = [
        resolve_work_file(step5_dir, "stylesheet.json"),
        Path(step5_dir).parent / "stylesheet.json",
        OUTLINES_DIR / slug / "stylesheet.json",
        TEMPLATES_DIR / slug / "stylesheet.json",
        SEEDS_DIR / slug / "stylesheet.json",
    ]
    cur = Path(step5_dir)
    for _ in range(4):
        candidates.append(cur / "stylesheet.json")
        cur = cur.parent
    for p in candidates:
        if not p.is_file():
            continue
        data = load_json(p)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("stylesheet"), list):
            return [x for x in data["stylesheet"] if isinstance(x, dict)]
    plan_path = resolve_work_file(step5_dir, "plan.json")
    if plan_path.is_file():
        plan = load_json(plan_path) or {}
        ss = plan.get("stylesheet")
        if isinstance(ss, list):
            return [x for x in ss if isinstance(x, dict)]
    return []


def resolve_selected_library(slug: str, step5_dir: Path) -> dict[str, Any]:
    for p in (
        resolve_work_file(step5_dir, "selected_library.yaml"),
        TEMPLATES_DIR / slug / "selected_library.yaml",
        SEEDS_DIR / slug / "selected_library.yaml",
        OUTLINES_DIR / slug / "selected_library.yaml",
    ):
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                return {}
    src = step5_dir / "source.json"
    if src.is_file():
        try:
            meta = load_json(src) or {}
            outline = meta.get("outline_dir") or meta.get("step2_dir")
            if outline:
                libp = Path(outline) / "selected_library.yaml"
                if libp.is_file():
                    return yaml.safe_load(libp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            pass
    return {}


def _genre_from_dirs(slug: str, step5_dir: Path) -> str:
    for p in (
        resolve_work_file(step5_dir, "brief.json"),
        TEMPLATES_DIR / slug / "brief.json",
        SEEDS_DIR / slug / "brief.json",
    ):
        if p.is_file():
            try:
                b = load_json(p) or {}
                return str(b.get("genre") or b.get("document_type") or "")
            except Exception:  # noqa: BLE001
                pass
    parts = slug.split("__")[0].split("_", 1)
    return parts[1] if len(parts) > 1 else slug


def build_t_variants(
    out_dir: Path,
    *,
    slug: str,
    step5_dir: Path,
    want: set[str],
    reinforce_map: bool = False,
    golden: Path | None = None,
    init_plain: Path | None = None,
    init_partial: Path | None = None,
) -> dict[str, Any]:
    """Build T-side artifacts. ``want`` may include text / purposes / template_styled / template_style / template_direct / t_all."""
    from LongDocFmtEval.pipeline_auto.seed.bake_docx_styles import (  # noqa: PLC0415
        bake_docx_to_direct,
        reapply_paragraph_props_via_map,
    )
    from LongDocFmtEval.pipeline_auto.seed.step6_t_text import write_t_text_artifacts  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import (  # noqa: PLC0415
        build_need_role_params,
        heading_style_ids,
    )

    if "t_all" in want:
        want = want | {"text", "purposes", "template_styled", "template_style", "template_direct"}

    role_params = resolve_role_params(slug, step5_dir)
    lib = resolve_selected_library(slug, step5_dir)
    stylesheet = resolve_stylesheet(slug, step5_dir)
    genre = _genre_from_dirs(slug, step5_dir)
    locale = str((lib or {}).get("locale") or "")
    if not locale:
        locale = "en" if ("_en_" in slug or slug.endswith("_en") or genre.endswith("_en")) else "zh"
    out: dict[str, Any] = {}

    styles_path = resolve_work_file(step5_dir, "styles.json")
    map_path = resolve_work_file(step5_dir, "map.json")
    styles_json = load_json(styles_path) if styles_path.is_file() else {}
    map_json = load_json(map_path) if map_path.is_file() else {}
    head_ids = heading_style_ids(stylesheet, role_params)

    need_text = bool({"text", "purposes"} & want)
    if need_text:
        if not role_params and not stylesheet:
            out["text_warning"] = "missing role_params/stylesheet; skipped t_text / t_style_propose"
        else:
            # Primary t_text.md: deltas vs input_plain (or full role_params if no init)
            init_for_main = init_plain if init_plain and Path(init_plain).is_file() else None
            need_main, diff_meta = build_need_role_params(
                role_params,
                golden=golden,
                init=init_for_main,
                styles=styles_json or {},
                maps=map_json or {},
                locale=locale,
                stylesheet=stylesheet,
            )
            # Keep display/purpose from full catalog when need stripped them oddly
            for rid, spec in (role_params or {}).items():
                if rid not in need_main or not isinstance(need_main.get(rid), dict):
                    continue
                for key in ("display_name", "description", "object", "example"):
                    if need_main[rid].get(key) in (None, "") and isinstance(spec, dict) and spec.get(key):
                        need_main[rid][key] = spec[key]

            note = (
                f"init_delta vs {init_for_main.name}"
                if init_for_main and diff_meta.get("mode") == "init_delta"
                else "role_params full (no init diff)"
            )
            files = write_t_text_artifacts(
                out_dir,
                need_main or role_params,
                genre=genre,
                lib=lib or None,
                stylesheet=stylesheet or None,
                locale=locale,
                heading_ids=head_ids,
                source_note=note,
                text_filename="t_text.md",
                meta_extra={"diff": diff_meta, "paired_init": "input_plain.docx" if init_for_main else ""},
            )
            if "text" in want:
                out["text"] = {
                    "file": files["t_text"],
                    "kind": "format_requirements_nl",
                    "structure": "defaults_then_overrides",
                    "buckets": [
                        "section", "paragraph_heading", "paragraph_body",
                        "paragraph_table", "run", "table", "image",
                    ],
                    "props_source": diff_meta.get("mode"),
                    "paired_init": "input_plain.docx" if init_for_main else None,
                    "locale": locale,
                    "labels_from": "stylesheet.display_name",
                    "purpose_from": "stylesheet.description",
                }

            # Optional partial-paired T
            if init_partial and Path(init_partial).is_file() and "text" in want:
                need_part, part_meta = build_need_role_params(
                    role_params,
                    golden=golden,
                    init=init_partial,
                    styles=styles_json or {},
                    maps=map_json or {},
                    locale=locale,
                    stylesheet=stylesheet,
                )
                for rid, spec in (role_params or {}).items():
                    if rid not in need_part or not isinstance(need_part.get(rid), dict):
                        continue
                    for key in ("display_name", "description", "object", "example"):
                        if need_part[rid].get(key) in (None, "") and isinstance(spec, dict) and spec.get(key):
                            need_part[rid][key] = spec[key]
                part_files = write_t_text_artifacts(
                    out_dir,
                    need_part or role_params,
                    genre=genre,
                    lib=lib or None,
                    stylesheet=stylesheet or None,
                    locale=locale,
                    heading_ids=head_ids,
                    source_note=f"init_delta vs {Path(init_partial).name}",
                    text_filename="t_text_from_partial.md",
                    meta_extra={
                        "diff": part_meta,
                        "paired_init": "input_partial.docx",
                    },
                )
                # Restore canonical propose + main meta (partial write overwrote them)
                from LongDocFmtEval.pipeline_auto.seed.step6_t_text import (  # noqa: PLC0415
                    build_t_style_purposes,
                )
                (out_dir / "t_style_propose.md").write_text(
                    build_t_style_purposes(
                        genre=genre,
                        stylesheet=stylesheet,
                        role_params=role_params,
                        locale=locale,
                        heading_ids=head_ids,
                    ),
                    encoding="utf-8",
                )
                dump_json(out_dir / "t_text_meta.json", {
                    "genre": genre,
                    "locale": locale,
                    "role_count": len(need_main or role_params or {}),
                    "files": {"text": "t_text.md", "propose": "t_style_propose.md"},
                    "diff": diff_meta,
                    "paired_init": "input_plain.docx" if init_for_main else "",
                    "buckets": [
                        "section", "paragraph_heading", "paragraph_body",
                        "paragraph_table", "run", "table", "image",
                    ],
                })
                out["text_from_partial"] = {
                    "file": part_files["t_text"],
                    "kind": "format_requirements_nl",
                    "paired_init": "input_partial.docx",
                    "props_source": part_meta.get("mode"),
                    "locale": locale,
                }

            if "purposes" in want:
                out["purposes"] = {
                    "file": files.get("t_style_propose"),
                    "kind": "style_purpose_list",
                    "fields": ["display_name", "description"],
                    "exclude": ["style_id", "expectation"],
                    "source": "step2 stylesheet.json",
                    "locale": locale,
                    "note": "pair with t_template_*.docx when composing a dataset sample",
                }

    tpl_src = resolve_template_docx(slug, step5_dir)
    direct_path = out_dir / "t_template_direct.docx"

    if "template_styled" in want or "template_direct" in want or "template_style" in want:
        if tpl_src is None:
            out["template_warning"] = (
                "no template.docx/preview.docx in step3/step4/step5; skipped template variants"
            )
        else:
            if "template_styled" in want:
                out["template_styled"] = {
                    "file": rel_from(tpl_src, out_dir),
                    "kind": "template_named_styles",
                    "source": str(tpl_src).replace("\\", "/"),
                    "shared": True,
                }
            if "template_style" in want:
                from LongDocFmtEval.pipeline_auto.seed.strip_para_markers import (  # noqa: PLC0415
                    write_clean_docx,
                )
                style_path = out_dir / "t_template_style.docx"
                _, n_stripped = write_clean_docx(tpl_src, style_path)
                out["template_style"] = {
                    "file": "t_template_style.docx",
                    "kind": "template_without_style_markers",
                    "source": str(tpl_src).replace("\\", "/"),
                    "stripped_markers": n_stripped,
                }
            if "template_direct" in want:
                bake_stats = bake_docx_to_direct(tpl_src, direct_path, strip_markers=True)
                reinforce = None
                if reinforce_map and role_params:
                    try:
                        reinforce = reapply_paragraph_props_via_map(
                            direct_path, role_params, map_json or {}, styles_json=styles_json or {},
                        )
                    except Exception as exc:  # noqa: BLE001
                        reinforce = {"error": str(exc)[:300]}
                out["template_direct"] = {
                    "file": "t_template_direct.docx",
                    "kind": "template_normal_plus_direct_deltas",
                    "base": rel_from(tpl_src, out_dir),
                    "bake": bake_stats,
                    "reinforce_map": reinforce,
                    "strip_markers": True,
                }

    return out


def build_step6_pair(
    step5_dir: Path,
    out_dir: Path,
    *,
    seed: int = 42,
    variants: tuple[str, ...] = (
        "plain", "partial", "text", "purposes", "template_styled", "template_style", "template_direct",
    ),
    document_name: str = "document.docx",
    reinforce_map: bool = False,
) -> dict[str, Any]:
    """Create work/step6_pairs/<slug>/ with D-side and T-side variants (no cartesian product)."""
    step5_dir = step5_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = out_dir.name

    src = step5_dir / document_name
    if not src.is_file():
        raise FileNotFoundError(f"step5 缺 {document_name}: {src}")

    dropped = drop_redundant_copies(out_dir)
    golden_copy = out_dir / "golden.docx"
    if golden_copy.is_file():
        try:
            golden_copy.unlink()
            dropped.append("golden.docx")
        except OSError:
            pass
    styled_copy = out_dir / "t_template_styled.docx"
    if styled_copy.is_file():
        try:
            styled_copy.unlink()
            dropped.append("t_template_styled.docx")
        except OSError:
            pass

    map_path = resolve_work_file(step5_dir, "map.json")
    map_json = load_json(map_path) if map_path.is_file() else {}
    role_params = resolve_role_params(slug, step5_dir)

    manifest: dict[str, Any] = {
        "slug": slug,
        "source_document": str(src).replace("\\", "/"),
        "seed": seed,
        "note": "Variants are listed independently; compose D×T when building the training set.",
        "d_variants": {},
        "t_variants": {},
        "variants": {},  # backward-compatible flat view
    }
    want = {v.strip().lower() for v in variants}

    # aliases
    alias = {
        "t_text": "text",
        "t_purposes": "purposes",
        "t_propose": "purposes",
        "propose": "purposes",
        "t_styled": "template_styled",
        "t_style": "template_style",
        "template_style": "template_style",
        "t_direct": "template_direct",
        "styled": "template_styled",
        "direct": "template_direct",
        "all": "all",
    }
    want = {alias.get(v, v) for v in want}
    if "all" in want:
        want = {
            "plain", "partial",
            "text", "purposes", "template_styled", "template_style", "template_direct",
        }

    # ----- D side -----
    plain_path = out_dir / "input_plain.docx"
    plain_meta: dict[str, Any] = {}
    if "plain" in want or "partial" in want:
        plain_meta = apply_plain_transform(src, plain_path, seed=seed)
        d_plain = {
            "file": "input_plain.docx",
            "side": "D",
            "page": {"paper": "A4", "margin_in": 1.0, "headers_footers": "strip"},
            "paragraph_style": "Normal",
            "strip_runs": True,
            "tables": "default_grid",
            "images": {
                "delta_cm": 1.0,
                "min_cm": _MIN_CM,
                "max_cm": _MAX_CM,
                "align": "left",
                "mode": "independent_wh",
                "log": plain_meta.get("images") or [],
            },
        }
        if "plain" not in want:
            d_plain["note"] = "built as base for partial"
        manifest["d_variants"]["plain"] = d_plain
        manifest["variants"]["plain"] = d_plain

    if "partial" in want:
        partial_path = out_dir / "input_partial.docx"
        if not plain_path.is_file():
            plain_meta = apply_plain_transform(src, plain_path, seed=seed)
        ss_idx = load_heading_index(
            resolve_work_file(step5_dir, "plan.json"),
            resolve_work_file(step5_dir, "stylesheet.json"),
            step5_dir.parent / "step3_outlines" / "plan.json",
            step5_dir.parent / "step3_outlines" / "stylesheet.json",
            step5_dir.parent / "step4_template" / "plan.json",
            step5_dir.parent / "step4_styles" / "plan.json",
            TEMPLATES_DIR / slug / "plan.json",
        )
        partial_meta = apply_partial_headings(
            plain_path,
            partial_path,
            golden=src,
            map_json=map_json,
            role_params=role_params,
            heading_map=ss_idx or None,
        )
        d_partial = {
            "file": "input_partial.docx",
            "side": "D",
            "base": "plain",
            "heading_map": partial_meta.get("heading_map"),
            "applied": partial_meta.get("applied") or [],
            "warnings": partial_meta.get("warnings") or [],
        }
        manifest["d_variants"]["partial"] = d_partial
        manifest["variants"]["partial"] = d_partial

    manifest["d_variants"]["golden"] = {
        "file": rel_from(src, out_dir),
        "side": "G",
        "shared": True,
    }
    manifest["variants"]["golden"] = manifest["d_variants"]["golden"]

    # ----- T side (after D so t_text can diff against init_plain / partial) -----
    t_want = want & {"text", "purposes", "template_styled", "template_style", "template_direct", "t_all"}
    if t_want or "all" in {v.strip().lower() for v in variants}:
        t_meta = build_t_variants(
            out_dir,
            slug=slug,
            step5_dir=step5_dir,
            want=t_want or {"text", "purposes", "template_styled", "template_style", "template_direct"},
            reinforce_map=reinforce_map,
            golden=src,
            init_plain=plain_path if plain_path.is_file() else None,
            init_partial=(out_dir / "input_partial.docx")
            if (out_dir / "input_partial.docx").is_file() else None,
        )
        for key, spec in t_meta.items():
            if key.endswith("_warning"):
                manifest.setdefault("warnings", []).append({key: spec})
                continue
            if isinstance(spec, dict):
                spec = {**spec, "side": "T"}
                manifest["t_variants"][key] = spec
                manifest["variants"][f"t_{key}" if not key.startswith("t_") else key] = spec

    dump_json(out_dir / "manifest.json", manifest)
    dump_json(
        out_dir / "source_step7.json",
        {
            "step": 7,
            "slug": slug,
            "document_dir": str(step5_dir).replace("\\", "/"),
            "step6_dir": str(step5_dir).replace("\\", "/"),
            "step5_dir": str(step5_dir).replace("\\", "/"),
            "document": document_name,
            "seed": seed,
            "template_source": str(resolve_template_docx(slug, step5_dir) or "").replace("\\", "/"),
        },
    )
    return manifest
