"""Write seed-compatible IR sidecars into a template directory.

Produces the same artifacts humans need for edit / re-render as ``seed/export_from_role``:
  outline.json, styles.json, map.json, style_tree.json, tables.json, source.json

``styles.json`` / ``map.json`` are later refreshed by ``render_template_docx.render``
with actual document indices (paragraph / table coordinates).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from LongDocFmtEval.pipeline_auto.auto_paths import OUTLINES_DIR, resolve_outline_sidecar, resolve_work_file
from LongDocFmtEval.pipeline_auto.seed.build_table_samples import build_table_samples
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (
    build_outline_and_maps,
    dump_json,
    load_json,
    load_pos_para_from_template,
    style_tree_from_role_params,
)


def write_step3_seed_ir(template_dir: Path) -> dict:
    """Export seed IR into ``template_dir`` (in-place). Returns summary counts."""
    template_dir = template_dir.resolve()
    tpl_path = resolve_work_file(template_dir, "template.json")
    template = load_json(tpl_path) if tpl_path.is_file() else {}
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_role_params_map  # noqa: PLC0415
    role_params = load_role_params_map(template_dir)
    if not role_params:
        raise FileNotFoundError(
            f"missing role_params.json (or legacy template.json roles): {template_dir}"
        )

    slug = template_dir.name
    plan_path = resolve_outline_sidecar(template_dir, "plan.json")
    lib_path = resolve_outline_sidecar(template_dir, "selected_library.yaml")
    if not plan_path.exists():
        raise FileNotFoundError(
            f"missing outline plan: tried {template_dir / 'plan.json'} "
            f"and {OUTLINES_DIR / slug / 'plan.json'}"
        )
    plan = load_json(plan_path)

    from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import prune_empty_sections, strip_abstract_roles
    prune_notes = prune_empty_sections(plan)
    lib = {}
    if lib_path.exists():
        try:
            lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            lib = {}
    prune_notes.extend(strip_abstract_roles(plan, lib))
    if prune_notes:
        dump_json(plan_path, plan)
        for msg in prune_notes:
            print(f"[fix] {msg}")

    pos_para = load_pos_para_from_template(template)
    if lib_path.exists():
        lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
        for t in (lib.get("roles", {}) or {}).get("table") or []:
            tl = t.get("table_layout") or {}
            pmap = {
                pos: (m or {}).get("paragraph")
                for pos, m in (tl.get("position_map") or {}).items()
            }
            if pmap:
                pos_para[t.get("role_id")] = pmap

    outline, styles, maps = build_outline_and_maps(plan, role_params, pos_para=pos_para)
    section_titles = {
        (s.get("role_id") or ""): (s.get("title") or s.get("role_id") or "")
        for s in (plan.get("sections") or [])
        if s.get("role_id")
    }
    for rid, spec in role_params.items():
        if rid.startswith("Sec") and isinstance(spec, dict) and spec.get("example"):
            section_titles.setdefault(rid, spec["example"])
            if not section_titles.get(rid) or section_titles[rid] == rid:
                section_titles[rid] = spec["example"]
    style_tree = style_tree_from_role_params(role_params, section_titles=section_titles)

    meta = template.get("meta") or plan.get("meta") or {}
    brief = {
        "seed_id": slug,
        "genre": meta.get("genre") or outline["meta"].get("genre"),
        "locale": meta.get("locale") or "zh",
        "theme": meta.get("theme") or outline["meta"].get("theme"),
        "writing_requirements": meta.get("writing_requirements") or "",
        "notes_applied": meta.get("notes_applied") or [],
        "source_pipeline": "pipeline_role_step3",
    }
    source = {
        "template_dir": str(template_dir).replace("\\", "/"),
        "outline_dir": str(plan_path.parent).replace("\\", "/"),
        "plan_json": str(plan_path).replace("\\", "/"),
        "selected_library": str(lib_path).replace("\\", "/") if lib_path.exists() else "",
        "slug": slug,
        "preview_docx": "preview.docx",
    }

    dump_json(template_dir / "brief.json", brief)
    dump_json(template_dir / "outline.json", outline)
    dump_json(template_dir / "styles.json", styles)
    dump_json(template_dir / "map.json", maps)
    dump_json(template_dir / "style_tree.json", style_tree)
    dump_json(template_dir / "source.json", source)

    tables = build_table_samples(template_dir)

    return {
        "objects": len(outline.get("objects") or []),
        "roles": len((style_tree.get("roles") or {})),
        "tables": len(tables or {}),
    }
