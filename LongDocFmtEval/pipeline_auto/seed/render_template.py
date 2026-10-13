"""Render seed template.docx using Word named paragraph styles + style_tree.

Paragraphs: create /styles/{role_id}, emit paragraphs with style=role_id.
Section/table/image: apply props from style_tree; indices follow outline.

Usage:
  python LongDocFmtEval/pipeline_auto/seed/render_template.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/work/step4_seeds/field_survey_theme1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.officecli_runner import (  # noqa: E402
    _render_prop_value,
    close_docx,
    create_docx,
    get_json,
    run_officecli,
    validate_docx,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: E402
    char_props, dump_json, load_json, para_marker, role_kind,
    apply_render_prop_guards,
    shift_run_spans_for_marker, style_props_for_paragraph,
    table_format_of, with_para_marker,
)
from LongDocFmtEval.pipeline_auto.seed.table_chrome_presets import (  # noqa: E402
    assign_declared_cell_styles,
    chrome_for_declared_style,
    llm_cells_chrome,
)

from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import (  # noqa: E402
    _SET_ONLY_SECTION_KEYS,
    _chrome_props,
    _finalize_cell_chrome,
    _sanitize_page_start,
    _sanitize_section_break_type,
    _section_hf_props,
    _table_is_open,
    apply_cell_chrome_and_paragraph_style,
    apply_table_repeat_header,
)


def _nonempty(d: dict | None) -> dict:
    # Keep literal "none" (valid officecli border value).
    return {k: v for k, v in (d or {}).items() if v is not None and v != ""}


def _demo_table_grid(
    cell_bindings: list[dict],
    *,
    rexample,
    main_point: str = "",
    object_id: str = "",
    locale: str = "zh",
) -> list[list[tuple[str, str, str]]]:
    """Fallback demo grid (prefer tables.json from build_table_samples)."""
    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import build_grid_for_table

    sample = build_grid_for_table({
        "object_id": object_id or "tmp",
        "role_id": "",
        "main_point": main_point,
        "cells": cell_bindings,
        "locale": locale,
    })
    return [
        [(c["pos"], c["role_id"], c["text"]) for c in row]
        for row in (sample.get("grid") or [])
    ]


def _grid_from_tables_json(
    tables: dict,
    obj: dict,
    bindings: list[dict],
    *,
    rexample,
    locale: str = "zh",
) -> list[list[tuple[str, str, str]]]:
    oid = obj.get("object_id") or obj.get("block_id") or ""
    sample = (tables or {}).get(oid) if oid else None
    if not sample:
        # also try by table_index
        ti = obj.get("table_index")
        for v in (tables or {}).values():
            if isinstance(v, dict) and v.get("table_index") == ti:
                sample = v
                break
    if sample and sample.get("grid"):
        return [
            [(c.get("pos") or "", c.get("role_id") or "", c.get("text") or "") for c in row]
            for row in sample["grid"]
            if isinstance(row, list)
        ]
    return _demo_table_grid(
        bindings,
        rexample=rexample,
        main_point=obj.get("main_point") or "",
        object_id=str(oid),
        locale=locale,
    )


def _apply_table_three_layers(
    doc: Path,
    *,
    tbl_spec: dict,
    grid: list[list[tuple[str, str, str]]],
    rprops,
) -> None:
    """Render table from LLM-declared cell_style + named Para*Cell styles."""
    del rprops  # look comes from named styles, not cell-direct text props
    tf = table_format_of(tbl_spec)
    chrome_by_pos = llm_cells_chrome(tbl_spec)
    open_tbl = _table_is_open(tf)
    grid = assign_declared_cell_styles(grid, tbl_spec)
    nrows = len(grid)
    ncols = max((len(r) for r in grid), default=1)
    add_props(doc, "/body", typ="table", props={"rows": nrows, "cols": ncols})
    tbl = "/body/table[last()]"
    tf_set = {k: v for k, v in tf.items() if k != "repeat_header"}
    if tf_set:
        try:
            set_props(doc, tbl, tf_set)
        except RuntimeError:
            soft = {k: v for k, v in tf_set.items()
                    if k in ("align", "width", "layout") or k.startswith("border")}
            if soft:
                set_props(doc, tbl, soft)
    apply_table_repeat_header(doc, tbl, tf, grid)

    for ri, row in enumerate(grid, start=1):
        for ci, (cell_style, para_rid, text) in enumerate(row, start=1):
            chrome = _chrome_props(
                _finalize_cell_chrome(
                    chrome_for_declared_style(chrome_by_pos, cell_style),
                    table_open=open_tbl,
                )
            )
            marked = with_para_marker(para_rid, text)
            apply_cell_chrome_and_paragraph_style(
                doc, tbl=tbl, ri=ri, ci=ci,
                chrome=chrome, para_rid=para_rid, text=marked,
            )

def add_props(doc: Path, path: str, *, typ: str, props: dict) -> None:
    """Like officecli_runner.add_props but tolerate UNSUPPORTED-prop warnings (exit 2)."""
    args = ["add", str(doc), path, "--type", typ]
    for key, value in props.items():
        rendered = _render_prop_value(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    proc = run_officecli(args, check=False)
    if proc.returncode == 0:
        return
    err = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode == 2 and "UNSUPPORTED" in err.upper():
        return
    raise RuntimeError(
        f"officecli failed ({proc.returncode}): {' '.join(args)}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def set_props(doc: Path, path: str, props: dict) -> None:
    args = ["set", str(doc), path]
    for key, value in props.items():
        rendered = _render_prop_value(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    proc = run_officecli(args, check=False)
    if proc.returncode == 0:
        return
    err = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode == 2 and "UNSUPPORTED" in err.upper():
        return
    raise RuntimeError(
        f"officecli failed ({proc.returncode}): {' '.join(args)}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def _hf_ref_path(sec_fmt: dict, typ: str, hf_type: str) -> str | None:
    """Resolve /header[N] or /footer[N] from section get/format headerRef/footerRef."""
    if typ == "header":
        key = f"headerRef.{hf_type}" if hf_type != "default" else "headerRef.default"
        return sec_fmt.get(key) or (sec_fmt.get("headerRef") if hf_type == "default" else None)
    key = f"footerRef.{hf_type}" if hf_type != "default" else "footerRef.default"
    return sec_fmt.get(key) or (sec_fmt.get("footerRef") if hf_type == "default" else None)


def _apply_section_chrome(doc: Path, sec_index: int, key: str, hf: dict) -> None:
    """Add header/footer on /section[N] (role order). If already present, set via headerRef path."""
    typ = "header" if key.startswith("header") else "footer"
    props = dict(hf or {})
    hf_type = "default"
    if key == "header_first":
        props.setdefault("type", "first")
        typ = "header"
        hf_type = "first"
    elif props.get("type") in ("first", "even"):
        hf_type = str(props["type"])

    try:
        add_props(doc, f"/section[{sec_index}]", typ=typ, props=props)
        return
    except RuntimeError as exc:
        if "already exists" not in str(exc):
            print(f"  [warn] section {sec_index} {key}: {exc}")
            return

    set_hf = {k: v for k, v in props.items() if k != "type"}
    candidates: list[str] = []
    try:
        data = get_json(doc, f"/section[{sec_index}]")
        results = ((data.get("data") or {}).get("results") or [])
        fmt = (results[0].get("format") or {}) if results else {}
        ref = _hf_ref_path(fmt, typ, hf_type)
        if ref:
            candidates.append(ref)
    except Exception:  # noqa: BLE001
        pass
    candidates.append(f"/{typ}[{sec_index}]")
    for path in candidates:
        try:
            set_props(doc, path, set_hf)
            return
        except RuntimeError:
            continue
    print(f"  [warn] section {sec_index} {key}: could not add or set existing {typ}")


def _prepare_section_props(raw: dict) -> dict:
    sp = apply_render_prop_guards(
        {k: v for k, v in (raw or {}).items() if v not in (None, "", "none")}
    )
    sp.pop("vAlign", None)
    sp.setdefault("type", "nextPage")
    sp["type"] = _sanitize_section_break_type(sp.get("type"))
    if "pageStart" in sp:
        cleaned = _sanitize_page_start(sp["pageStart"])
        if cleaned is None:
            sp.pop("pageStart", None)
        else:
            sp["pageStart"] = cleaned
    return sp


def _finalize_section(
    doc: Path,
    *,
    sec_index: int,
    n_sections: int,
    rid: str,
    title: str,
    spec: dict,
    new_map: dict,
    new_styles: dict,
) -> None:
    """Apply section props + header/footer AFTER that section's body content (role order).

    officecli keeps sectPr at the end of each section. Adding a break too early moves
    previously attached headerRefs onto the *next* trailing section, so 'add header'
    then fails with 'already exists' and wrong paths like /section[N]/header[1].
    """
    props = _prepare_section_props(dict(spec.get("props") or {}))
    if sec_index < n_sections:
        add_sp = {k: v for k, v in props.items() if k not in _SET_ONLY_SECTION_KEYS}
        add_props(doc, "/", typ="section", props=add_sp or {"type": "nextPage"})
        secpath_i = sec_index
        extra = {k: v for k, v in props.items() if k in _SET_ONLY_SECTION_KEYS}
        if extra:
            set_props(doc, f"/section[{secpath_i}]", extra)
    else:
        if props:
            set_props(doc, f"/section[{n_sections}]", props)
        secpath_i = n_sections

    for key in ("header", "footer", "header_first"):
        if key == "header_first" and not spec.get("header_first") and not props.get("titlePage"):
            continue
        hf = _section_hf_props(spec, key, title)
        _apply_section_chrome(doc, secpath_i, key, hf)

    new_map["section"].setdefault(rid, [])
    if secpath_i not in new_map["section"][rid]:
        new_map["section"][rid].append(secpath_i)
    if rid not in new_styles["section"]:
        new_styles["section"][rid] = secpath_i


def _ensure_para_styles(doc: Path, style_tree: dict) -> None:
    roles = style_tree.get("roles") or {}
    for rid, spec in roles.items():
        if not (rid.startswith("Para") or role_kind(rid) == "paragraph"):
            continue
        props = style_props_for_paragraph(spec.get("props") or {})
        try:
            add_props(
                doc,
                "/styles",
                typ="style",
                props={
                    "id": rid,
                    "name": spec.get("display_name") or rid,
                    "type": "paragraph",
                    "basedOn": "Normal",
                    **props,
                },
            )
        except RuntimeError as exc:
            # retry without optional formatting if officecli rejects a prop
            msg = str(exc)
            print(f"  [warn] style {rid}: {msg.splitlines()[-1][:120]}")
            add_props(
                doc,
                "/styles",
                typ="style",
                props={
                    "id": rid,
                    "name": spec.get("display_name") or rid,
                    "type": "paragraph",
                    "basedOn": "Normal",
                    **{k: v for k, v in props.items()
                       if k in ("font.ea", "font.latin", "font", "size", "bold", "italic",
                                "align", "spaceBefore", "spaceAfter", "lineSpacing",
                                "outlineLvl", "color")},
                },
            )


def _ensure_char_styles(doc: Path, style_tree: dict) -> None:
    roles = style_tree.get("roles") or {}
    for rid, spec in roles.items():
        if not rid.startswith("Run"):
            continue
        props = char_props(spec.get("props") or {})
        add_props(
            doc,
            "/styles",
            typ="style",
            props={
                "id": rid,
                "name": spec.get("display_name") or rid,
                "type": "character",
                "basedOn": "DefaultParagraphFont",
                **props,
            },
        )


def render_seed(seed_dir: Path, *, out_name: str = "template.docx") -> Path:
    seed_dir = seed_dir.resolve()
    outline = load_json(seed_dir / "outline.json")
    style_tree = load_json(seed_dir / "style_tree.json")
    styles = load_json(seed_dir / "styles.json") if (seed_dir / "styles.json").exists() else {}
    maps = load_json(seed_dir / "map.json") if (seed_dir / "map.json").exists() else {}
    roles = style_tree.get("roles") or {}
    source = load_json(seed_dir / "source.json") if (seed_dir / "source.json").exists() else {}

    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import infer_locale  # noqa: PLC0415
    locale = infer_locale(seed_dir=seed_dir, obj=source)

    assets = {}
    tpl_dir = Path(source.get("template_dir") or "")
    for cand in ("assets_preview.json", "assets.json"):
        p = tpl_dir / cand if tpl_dir else seed_dir / cand
        if p.exists():
            assets = load_json(p)
            break

    tables_path = seed_dir / "tables.json"
    tables = load_json(tables_path) if tables_path.exists() else {}
    if not tables:
        try:
            from LongDocFmtEval.pipeline_auto.seed.build_table_samples import (
                build_table_samples,
            )
            tables = build_table_samples(seed_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] build_table_samples: {exc}")
            tables = {}

    # optional position map from role library sidecar
    pos_para: dict[str, dict[str, str]] = {}
    lib_path = Path(source.get("selected_library") or "")
    if lib_path.exists():
        lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
        for t in (lib.get("roles", {}) or {}).get("table") or []:
            tl = t.get("table_layout") or {}
            pos_para[t.get("role_id")] = {
                pos: (m or {}).get("paragraph")
                for pos, m in (tl.get("position_map") or {}).items()
            }

    doc = seed_dir / out_name
    create_docx(doc)
    _ensure_para_styles(doc, style_tree)
    _ensure_char_styles(doc, style_tree)

    # refresh map paragraph indices while emitting
    new_map = {
        "section": {k: list(v) for k, v in (maps.get("section") or {}).items()},
        "paragraph": {},
        "table_structure": {},
        "table_cell": {},
        "image": {},
        "runs": dict(maps.get("runs") or {}),
        "paragraph_objects": {},
    }
    new_styles = {
        "section": dict(styles.get("section") or {}),
        "paragraph": {},
        "table_structure": {},
        "table_cell": {},
        "image": {},
        "run": dict(styles.get("run") or {}),
    }

    para_i = 0
    tbl_i = 0
    img_i = 0
    objects = outline.get("objects") or []

    def rprops(rid: str) -> dict:
        return apply_render_prop_guards((roles.get(rid) or {}).get("props") or {})

    def rexample(rid: str) -> str:
        return str((roles.get(rid) or {}).get("example") or "")

    # bullet list
    add_props(doc, "/numbering", typ="num", props={"id": 902, "format": "bullet", "start": 1})

    def emit_styled_para(rid: str, text: str, runs: list | None, object_id: str) -> int:
        nonlocal para_i
        para_i += 1
        raw = str(text or "")
        # Body carries {style-name} so human edits (insert/delete paras) can resync map
        # without hand-editing paragraph index lists.
        runs = shift_run_spans_for_marker(runs, rid, raw)
        text = with_para_marker(rid, raw)
        base = {"text": text, "style": rid}
        # list styles still need numId on paragraph
        props = rprops(rid)
        if props.get("listStyle") == "bullet":
            base["numId"] = 902
        elif props.get("listStyle") == "ordered":
            add_props(doc, "/numbering", typ="num",
                      props={"id": 900 + para_i, "format": "decimal", "text": "%1.", "start": 1})
            base["numId"] = 900 + para_i

        run_spans = []
        for r in runs or []:
            start, end = int(r.get("start", 0)), int(r.get("end", 0))
            if 0 <= start < end <= len(text):
                run_spans.append((start, end, r.get("role")))
        run_spans.sort()

        if not run_spans:
            add_props(doc, "/body", typ="paragraph", props=base)
        else:
            # split text
            parts: list[tuple[str, str | None]] = []
            cur = 0
            for start, end, role in run_spans:
                if start > cur:
                    parts.append((text[cur:start], None))
                parts.append((text[start:end], role))
                cur = end
            if cur < len(text):
                parts.append((text[cur:], None))
            first_txt, first_role = parts[0]
            padd = {"text": first_txt, "style": rid}
            if first_role:
                padd.update(char_props(rprops(first_role)))
            if base.get("numId"):
                padd["numId"] = base["numId"]
            add_props(doc, "/body", typ="paragraph", props=padd)
            for seg, role in parts[1:]:
                rp = char_props(rprops(role)) if role else {}
                add_props(doc, "/body/p[last()]", typ="run", props={"text": seg, **rp})

        new_map["paragraph"].setdefault(rid, []).append(para_i)
        if rid not in new_styles["paragraph"]:
            new_styles["paragraph"][rid] = {"style_id": rid, "para_index": para_i}
        new_map["paragraph_objects"][object_id] = {
            "para_index": para_i,
            "style_id": rid,
            "marker": para_marker(rid),
        }
        # keep run spans in map relative to marked body text
        if runs:
            new_map["runs"][object_id] = [
                {"role": r.get("role"), "start": int(r.get("start", 0)), "end": int(r.get("end", 0))}
                for r in runs if r.get("role")
            ]
        return para_i

    # Match role renderer: emit each section's body first, then section break + HF.
    # Outline lists section_break *before* content; we buffer until content is written.
    section_metas: list[dict] = []
    body_by_sec: dict[int, list] = {}
    pending_sec = 0
    for obj in objects:
        if obj.get("kind") == "section_break":
            pending_sec = int(obj.get("section_index") or (pending_sec + 1))
            section_metas.append(obj)
            body_by_sec.setdefault(pending_sec, [])
            continue
        if pending_sec == 0:
            pending_sec = 1
            body_by_sec.setdefault(1, [])
        body_by_sec.setdefault(pending_sec, []).append(obj)

    n_sections = len(section_metas) or 1
    if not section_metas:
        # no explicit breaks — treat whole doc as one section without chrome
        section_metas = [{"role_id": "", "section_index": 1, "main_point": ""}]
        body_by_sec.setdefault(1, list(objects))

    for si, sec_obj in enumerate(section_metas, start=1):
        rid = sec_obj.get("role_id") or ""
        title = sec_obj.get("main_point") or rid
        spec = roles.get(rid) or {}
        for obj in body_by_sec.get(si) or []:
            kind = obj.get("kind")
            orid = obj.get("role_id") or ""
            oid = obj.get("object_id") or ""
            text = obj.get("main_point") or rexample(orid)

            if kind == "paragraph":
                if str(orid).startswith("Sec"):
                    continue
                runs = obj.get("expected_runs") or (maps.get("runs") or {}).get(oid) or []
                emit_styled_para(orid, text, runs, oid)
                continue

            if kind == "table":
                tbl_i += 1
                tbl_spec = roles.get(orid) or {}
                bindings = obj.get("cells") or []
                if not bindings:
                    # Fall back to library position_map → paragraph roles
                    pmap = pos_para.get(orid) or {}
                    bindings = [
                        {"pos": pos, "role_id": rid}
                        for pos, rid in pmap.items() if rid
                    ]
                grid = _grid_from_tables_json(
                    tables, obj, bindings, rexample=rexample, locale=locale,
                )
                _apply_table_three_layers(
                    doc,
                    tbl_spec=tbl_spec,
                    grid=grid,
                    rprops=rprops,
                )
                for ri, row in enumerate(grid, start=1):
                    for ci, (_pos, para_rid, _txt) in enumerate(row, start=1):
                        coord = f"{tbl_i}:{ri}:{ci}"
                        new_map["table_cell"].setdefault(para_rid, []).append(coord)
                        if para_rid not in new_styles["table_cell"]:
                            new_styles["table_cell"][para_rid] = {
                                "table": tbl_i, "row": ri, "col": ci, "pos": _pos,
                            }
                new_map["table_structure"].setdefault(orid, []).append(tbl_i)
                if orid not in new_styles["table_structure"]:
                    new_styles["table_structure"][orid] = tbl_i
                continue

            if kind == "image":
                img_i += 1
                fid = obj.get("figure_id") or ""
                asset = None
                for k, v in assets.items():
                    if k == fid or (isinstance(v, dict) and v.get("role_id") == orid):
                        asset = v
                        if k == fid:
                            break
                if not asset:
                    for v in assets.values():
                        if isinstance(v, dict) and v.get("role_id") == orid:
                            asset = v
                            break
                iprops = rprops(orid)
                w = (asset or {}).get("width") or iprops.get("width") or "10cm"
                h = (asset or {}).get("height") or iprops.get("height") or "6cm"
                align = (asset or {}).get("align") or iprops.get("align") or "center"
                rel = (asset or {}).get("asset")
                img_path = None
                if rel and tpl_dir:
                    cand = tpl_dir / rel
                    if cand.exists():
                        img_path = cand
                if img_path and img_path.exists():
                    add_props(doc, "/body", typ="image", props={
                        "path": str(img_path),
                        "width": w,
                        "height": h,
                    })
                    if align:
                        try:
                            set_props(doc, "/body/p[last()]", {"align": align})
                        except RuntimeError:
                            pass
                else:
                    emit_styled_para("ParaFigureCaption", f"[图占位:{orid}] {text}", None, f"{oid}_ph")
                new_map["image"].setdefault(orid, []).append(img_i)
                if orid not in new_styles["image"]:
                    new_styles["image"][orid] = img_i
                continue

        if rid:
            _finalize_section(
                doc,
                sec_index=si,
                n_sections=n_sections,
                rid=rid,
                title=title,
                spec=spec,
                new_map=new_map,
                new_styles=new_styles,
            )

    try:
        validate_docx(doc)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] validate: {exc}")
    close_docx(doc)

    dump_json(seed_dir / "styles.json", new_styles)
    dump_json(seed_dir / "map.json", new_map)
    print(f"[docx] {doc}")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True)
    ap.add_argument("--out-name", default="template.docx")
    args = ap.parse_args()
    render_seed(Path(args.seed_dir), out_name=args.out_name)


if __name__ == "__main__":
    main()
