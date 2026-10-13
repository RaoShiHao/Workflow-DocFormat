"""Export a Seed package from pipeline_role template + outline, optionally render.

Usage:
  python LongDocFmtEval/pipeline_auto/seed/export_from_role.py \\
    --template-dir LongDocFmtEval/pipeline_auto/work/step3_templates/field_survey_report_zh__theme1 \\
    --out-dir LongDocFmtEval/pipeline_auto/work/step4_seeds/field_survey_theme1 \\
    --render
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.auto_paths import OUTLINES_DIR  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: E402
    build_outline_and_maps,
    dump_json,
    load_json,
    load_pos_para_from_template,
    style_tree_from_role_params,
)


def export_one(template_dir: Path, out_dir: Path, *, render: bool, seed_id: str | None) -> Path:
    template_dir = template_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tpl_path = template_dir / "template.json"
    rp_path = template_dir / "role_params.json"
    if not tpl_path.exists():
        raise FileNotFoundError(tpl_path)
    template = load_json(tpl_path)
    role_params = load_json(rp_path) if rp_path.exists() else {
        rid: {"props": (spec or {}).get("props"), "example": (spec or {}).get("example"),
              "display_name": (spec or {}).get("display_name"),
              "object": (spec or {}).get("object"),
              **{k: (spec or {}).get(k) for k in ("header", "footer", "header_first", "cells")
                 if (spec or {}).get(k)}}
        for rid, spec in (template.get("roles") or {}).items()
    }

    slug = template_dir.name
    plan_path = OUTLINES_DIR / slug / "plan.json"
    lib_path = OUTLINES_DIR / slug / "selected_library.yaml"
    if not plan_path.exists():
        raise FileNotFoundError(
            f"missing outline plan: {plan_path}（请先跑 pipeline_role step2）"
        )
    plan = load_json(plan_path)

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
    # prefer human-readable example from role_params when plan title is empty/id-like
    for rid, spec in role_params.items():
        if rid.startswith("Sec") and isinstance(spec, dict) and spec.get("example"):
            section_titles.setdefault(rid, spec["example"])
            if not section_titles.get(rid) or section_titles[rid] == rid:
                section_titles[rid] = spec["example"]
    style_tree = style_tree_from_role_params(role_params, section_titles=section_titles)

    meta = template.get("meta") or plan.get("meta") or {}
    sid = seed_id or slug
    brief = {
        "seed_id": sid,
        "genre": meta.get("genre") or outline["meta"].get("genre"),
        "locale": meta.get("locale") or "zh",
        "theme": meta.get("theme") or outline["meta"].get("theme"),
        "writing_requirements": meta.get("writing_requirements") or "",
        "notes_applied": meta.get("notes_applied") or [],
        "source_pipeline": "pipeline_role",
    }
    source = {
        "template_dir": str(template_dir).replace("\\", "/"),
        "outline_dir": str(plan_path.parent).replace("\\", "/"),
        "plan_json": str(plan_path).replace("\\", "/"),
        "selected_library": str(lib_path).replace("\\", "/") if lib_path.exists() else "",
        "slug": slug,
    }

    dump_json(out_dir / "brief.json", brief)
    dump_json(out_dir / "outline.json", outline)
    dump_json(out_dir / "styles.json", styles)
    dump_json(out_dir / "map.json", maps)
    dump_json(out_dir / "style_tree.json", style_tree)
    dump_json(out_dir / "source.json", source)

    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import build_table_samples
    build_table_samples(out_dir)

    print(f"[export] {out_dir}")
    print(f"  objects={len(outline.get('objects') or [])} "
          f"roles={len((style_tree.get('roles') or {}))}")

    if render:
        from LongDocFmtEval.pipeline_auto.seed.render_template import render_seed
        render_seed(out_dir)
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Export seedgen package from pipeline_role")
    ap.add_argument("--template-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed-id", default="")
    ap.add_argument("--render", action="store_true", help="also write template.docx")
    args = ap.parse_args()
    export_one(
        Path(args.template_dir),
        Path(args.out_dir),
        render=args.render,
        seed_id=args.seed_id or None,
    )


if __name__ == "__main__":
    main()
