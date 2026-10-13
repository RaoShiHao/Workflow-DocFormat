"""Apply heading numbering per frozen datasets_factors.json.

Reads ``config/datasets_factors.json`` as the unique assignment source,
backs up ``content.json`` / ``specimen_plan.json`` to ``*.base.json`` on first run,
writes numbered JSON, optionally re-renders template.docx / document.docx.

Example:
  python LongDocFmtEval/pipeline_auto/scripts/apply_heading_numbering.py --ids 12
  python LongDocFmtEval/pipeline_auto/scripts/apply_heading_numbering.py --all --rerender
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS
from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR, DATASETS_DIR
from LongDocFmtEval.pipeline_auto.seed.heading_numbering import (
    apply_numbering_to_content_json,
    apply_numbering_to_specimen_plan,
    compute_numbering_map,
    load_schemes,
    targets_from_factors,
)
from LongDocFmtEval.pipeline_auto.seed.heading_numbering_spec import render_numbering_spec_md
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json
from LongDocFmtEval.pipeline_auto.seed.training_factors import (
    DEFAULT_OUTPUT,
    assignment_for,
)


def _ensure_base(path: Path, base_path: Path) -> dict:
    if base_path.is_file():
        return load_json(base_path)
    data = load_json(path)
    dump_json(base_path, data)
    return data


def apply_one_sample(
    cx_dir: Path,
    factors: dict,
    *,
    rerender: bool = False,
    dry_run: bool = False,
) -> dict:
    plan_path = cx_dir / "step3_outlines" / "plan.json"
    doc_dir = cx_dir / "step6_document"
    tpl_dir = cx_dir / "step5_template"
    content_path = doc_dir / "content.json"
    specimen_path = tpl_dir / "specimen_plan.json"
    report: dict = {
        "sample_key": factors.get("sample_key"),
        "ok": False,
        "prefixes": 0,
        "errors": [],
    }
    if not plan_path.is_file():
        report["errors"].append(f"missing {plan_path}")
        return report
    if not content_path.is_file():
        report["errors"].append(f"missing {content_path}")
        return report

    plan = load_json(plan_path)
    locale = str(factors.get("locale") or "zh")
    schemes = load_schemes()
    targets = targets_from_factors(factors)
    document_numbered = targets["document"]
    template_numbered = targets["template"]
    needs_numbering = document_numbered or template_numbered

    scheme_id = str(factors.get("scheme_id") or "")
    prefixes: dict[str, str] = {}
    if needs_numbering:
        if scheme_id not in schemes:
            report["errors"].append(
                f"numbering requested but scheme_id missing/unknown: {scheme_id!r}"
            )
            return report
        prefixes = compute_numbering_map(plan, scheme_id, schemes=schemes)
        report["prefixes"] = len(prefixes)

    content_base_path = doc_dir / "content.base.json"
    content_base = _ensure_base(content_path, content_base_path)
    base_blocks = (content_base or {}).get("blocks") or {}
    new_content = apply_numbering_to_content_json(
        load_json(content_path),
        base_blocks,
        prefixes,
        apply=targets["document"],
    )
    manifest = {
        "sample_key": factors.get("sample_key"),
        "scheme_id": scheme_id or None,
        "document_numbered": document_numbered,
        "template_numbered": template_numbered,
        "text_numbering_spec": bool(factors.get("text_numbering_spec", document_numbered)),
        "prefix_count": len(prefixes),
        "applied": targets,
        "examples": [
            {"block_id": bid, "prefix": pref}
            for bid, pref in list(prefixes.items())[:5]
        ],
    }
    numbering_md = render_numbering_spec_md(
        scheme_id or "—",
        locale=locale,
        document_numbered=document_numbered,
        schemes=schemes,
    )

    if dry_run:
        report["ok"] = True
        report["dry_run"] = True
        return report

    dump_json(content_path, new_content)
    post_dir = cx_dir / "postprocess"
    post_dir.mkdir(parents=True, exist_ok=True)
    dump_json(post_dir / "heading_manifest.json", manifest)
    (post_dir / "numbering_spec_text.md").write_text(numbering_md, encoding="utf-8")

    if specimen_path.is_file():
        spec_base_path = tpl_dir / "specimen_plan.base.json"
        spec_base = _ensure_base(specimen_path, spec_base_path)
        new_spec = apply_numbering_to_specimen_plan(
            load_json(specimen_path),
            (spec_base or {}).get("outline") or [],
            prefixes,
            apply=targets["template"],
        )
        dump_json(specimen_path, new_spec)

    if rerender:
        try:
            from LongDocFmtEval.pipeline_auto.scripts.run_step5 import (  # noqa: PLC0415
                render_template_docx_only,
            )
            from LongDocFmtEval.pipeline_auto.scripts.run_step6 import run_one  # noqa: PLC0415

            styles = cx_dir / "step4_styles"
            if tpl_dir.is_dir() and styles.is_dir():
                render_template_docx_only(
                    styles, tpl_dir, overwrite=False, figures=True,
                )
            if doc_dir.is_dir() and tpl_dir.is_dir():
                run_one(
                    tpl_dir,
                    dest=doc_dir,
                    overwrite=False,
                    rerender=True,
                    figures=True,
                )
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"rerender: {exc}")

    report["ok"] = not report["errors"]
    return report


def iter_sample_dirs(
    *,
    datasets_dir: Path | None = None,
    ids: set[int] | None = None,
    complexities: tuple[str, ...] = COMPLEXITY_TIERS,
) -> list[tuple[Path, int, str]]:
    root = (datasets_dir or DATASETS_DIR).resolve()
    out: list[tuple[Path, int, str]] = []
    if not root.is_dir():
        return out
    for topic_dir in sorted(root.iterdir()):
        if not topic_dir.is_dir():
            continue
        try:
            tid = int(topic_dir.name.split("_", 1)[0])
        except (ValueError, IndexError):
            continue
        if ids and tid not in ids:
            continue
        for cx in complexities:
            cx_dir = topic_dir / cx
            if cx_dir.is_dir():
                out.append((cx_dir, tid, cx))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply heading numbering from datasets_factors.json")
    ap.add_argument("--factors", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--ids", default="", help="Comma-separated topic ids")
    ap.add_argument("--complexity", default="", help="C1 / C2 / C3 (default: all)")
    ap.add_argument("--all", action="store_true", help="All topics under datasets/")
    ap.add_argument("--rerender", action="store_true", help="Re-render template.docx and document.docx")
    ap.add_argument("--datasets-dir", type=Path, default=DATASETS_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.factors.is_file():
        raise SystemExit(f"missing factors file: {args.factors} (run assign_training_factors.py first)")

    global_data = load_json(args.factors)
    id_set: set[int] | None = None
    if str(args.ids or "").strip():
        id_set = {int(x.strip()) for x in str(args.ids).split(",") if x.strip()}
    elif not args.all:
        id_set = id_set  # None → only dirs that exist

    cx_filter = tuple(
        c.strip().upper() for c in str(args.complexity or "").split(",") if c.strip()
    ) or COMPLEXITY_TIERS

    samples = iter_sample_dirs(
        datasets_dir=args.datasets_dir,
        ids=id_set,
        complexities=cx_filter,
    )
    if not samples:
        print("[warn] no dataset sample dirs found; only global factors were assigned.")
        print("       Frozen assignments live in config/datasets_factors.json")
        return

    ok = 0
    for cx_dir, tid, cx in samples:
        factors = assignment_for(global_data, tid, cx)
        if not factors:
            print(f"[skip] no factors for {tid}_{cx}")
            continue
        rep = apply_one_sample(cx_dir, factors, rerender=args.rerender, dry_run=args.dry_run)
        tag = "ok" if rep.get("ok") else "fail"
        err = f" ({'; '.join(rep.get('errors') or [])})" if rep.get("errors") else ""
        print(
            f"[{tag}] {rep.get('sample_key')}  prefixes={rep.get('prefixes')}  "
            f"doc={targets_from_factors(factors)['document']}  "
            f"tpl={targets_from_factors(factors)['template']}"
            f"{err}"
        )
        if rep.get("ok"):
            ok += 1
    print(f"\nDone {ok}/{len(samples)} samples")


if __name__ == "__main__":
    main()
