"""Delete step3/step6 caches for plan heading-gap violations (remediation).

CLI:
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/cleanup_plan_body_slots.py --ids 64 --brief
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/cleanup_plan_body_slots.py --ids 64,70
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/cleanup_plan_body_slots.py --ids 64 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from LongDocFmtEval.pipeline_auto.auto_paths import (
    resolve_outline_sidecar,
    resolve_step3_block_json,
    resolve_step3_block_raw,
    step3_block_attempt_path,
)
from LongDocFmtEval.pipeline_auto.scripts.build_figures import assign_figure_ids
from LongDocFmtEval.pipeline_auto.scripts.errors_clean._paths import default_datasets_dir
from LongDocFmtEval.pipeline_auto.scripts.errors_clean.plan_body_slots import (
    group_hard_by_sample,
    load_json,
    scan_datasets,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_json as seed_load_json
from LongDocFmtEval.pipeline_auto.topic_layout import document_dir, outline_dir

# step3 artifacts invalidated when any section in a chapter needs re-planning
_STEP3_CHAPTER_ARTIFACTS = (
    "plan.json",
    "outline_tree.md",
    "outline_metrics.json",
    "stylesheet_scope.json",
)

# step6 whole-chapter artifacts when any section is bad
_STEP6_CHAPTER_ARTIFACTS = ("content.json", "document.docx")


@dataclass
class CleanupAction:
    genre_id: int
    chapter: str
    topic_dir: str
    section_ids: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


def _load_roles_for_figures(doc_dir: Path) -> dict:
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415

    tpl_path = resolve_outline_sidecar(doc_dir, "template.json")
    if not tpl_path.is_file():
        return {}
    template = seed_load_json(tpl_path)
    return load_merged_roles(doc_dir, template)


def _image_block_ids(plan: dict, section_id: str, roles: dict) -> set[str]:
    outline_map = {
        str(o.get("instance_id")): (o.get("blocks") or [])
        for o in (plan.get("outline") or [])
        if isinstance(o, dict)
    }
    out: set[str] = set()
    for b in outline_map.get(section_id) or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "")
        if roles.get(rid, {}).get("object") == "image" or rid.startswith("Img"):
            bid = str(b.get("block_id") or "")
            if bid:
                out.add(bid)
    return out


def _unlink(path: Path, rel_paths: list[str], topic: Path, *, apply: bool) -> None:
    if not path.is_file():
        return
    rel_paths.append(str(path.relative_to(topic)))
    if apply:
        path.unlink(missing_ok=True)


def _patch_figures_for_sections(
    doc_dir: Path,
    topic: Path,
    plan: dict,
    roles: dict,
    section_ids: list[str],
    *,
    apply: bool,
) -> list[str]:
    touched: list[str] = []
    if not plan or not roles:
        return touched

    img_blocks: set[str] = set()
    for sid in section_ids:
        img_blocks |= _image_block_ids(plan, sid, roles)
    if not img_blocks:
        return touched

    fid_by_block = assign_figure_ids(plan, roles)
    remove_fids = {str(fid_by_block[bid]) for bid in img_blocks if fid_by_block.get(bid)}

    fig_path = doc_dir / "figures" / "figures.json"
    assets_dir = doc_dir / "assets"
    manifest_path = doc_dir / "assets.json"
    if not fig_path.is_file():
        return touched

    specs = seed_load_json(fig_path)
    if not isinstance(specs, dict):
        return touched

    for fid, spec in list(specs.items()):
        if not isinstance(spec, dict):
            continue
        bid = str(spec.get("block_id") or "")
        if bid in img_blocks or str(fid) in remove_fids:
            remove_fids.add(str(fid))

    if not remove_fids:
        return touched

    for fid in sorted(remove_fids):
        touched.append(str(fig_path.relative_to(topic)))
        png = assets_dir / f"{fid}.png"
        if png.is_file():
            touched.append(str(png.relative_to(topic)))
        if apply:
            specs.pop(fid, None)
            png.unlink(missing_ok=True)

    if apply:
        if specs:
            fig_path.write_text(
                json.dumps(specs, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            fig_path.unlink(missing_ok=True)
        if manifest_path.is_file():
            try:
                manifest = seed_load_json(manifest_path)
            except Exception:  # noqa: BLE001
                manifest = {}
            if isinstance(manifest, dict):
                for fid in remove_fids:
                    manifest.pop(fid, None)
                if manifest:
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                else:
                    manifest_path.unlink(missing_ok=True)
    return touched


def _cleanup_section_blocks(
    od: Path,
    topic: Path,
    section_id: str,
    rel_paths: list[str],
    *,
    apply: bool,
) -> None:
    for resolver in (
        lambda: resolve_step3_block_json(od, section_id),
        lambda: resolve_step3_block_raw(od, section_id),
        lambda: step3_block_attempt_path(od, section_id),
    ):
        p = resolver()
        if p is not None:
            _unlink(p, rel_paths, topic, apply=apply)


def _cleanup_section_chunks(
    doc: Path,
    topic: Path,
    section_id: str,
    rel_paths: list[str],
    *,
    apply: bool,
) -> None:
    chunks_dir = doc / "chunks"
    if not chunks_dir.is_dir():
        return
    for p in sorted(chunks_dir.glob(f"{section_id}_c*.json")):
        rel_paths.append(str(p.relative_to(topic)))
        if apply:
            p.unlink(missing_ok=True)


def collect_cleanup_actions(
    datasets_dir: Path,
    id_set: set[int] | None,
    *,
    apply: bool,
) -> list[CleanupAction]:
    hits = scan_datasets(datasets_dir, id_set)
    actions: list[CleanupAction] = []

    for sample in group_hard_by_sample(hits):
        topic = datasets_dir / sample.topic_dir
        od = outline_dir(topic, complexity=sample.chapter)
        doc = document_dir(topic, complexity=sample.chapter)
        rel_paths: list[str] = []

        plan_path = od / "plan.json"
        plan: dict = {}
        roles: dict = {}
        if plan_path.is_file():
            try:
                plan = load_json(plan_path)
                roles = _load_roles_for_figures(doc)
            except Exception:  # noqa: BLE001
                plan = {}

        for sid in sample.section_ids:
            _cleanup_section_blocks(od, topic, sid, rel_paths, apply=apply)
            _cleanup_section_chunks(doc, topic, sid, rel_paths, apply=apply)

        for name in _STEP3_CHAPTER_ARTIFACTS:
            _unlink(od / name, rel_paths, topic, apply=apply)

        for name in _STEP6_CHAPTER_ARTIFACTS:
            _unlink(doc / name, rel_paths, topic, apply=apply)

        rel_paths.extend(
            _patch_figures_for_sections(
                doc, topic, plan, roles, sample.section_ids, apply=apply,
            )
        )

        actions.append(CleanupAction(
            genre_id=sample.genre_id,
            chapter=sample.chapter,
            topic_dir=sample.topic_dir,
            section_ids=list(sample.section_ids),
            paths=sorted(set(rel_paths)),
        ))
    return actions


def print_cleanup_report(actions: list[CleanupAction], *, apply: bool) -> None:
    mode = "已删除" if apply else "将删除"
    if not actions:
        print("[cleanup] 无硬违规样本，无需清理。")
        return
    print(f"[cleanup] {mode} {len(actions)} 个 id×C 样本：")
    for a in actions:
        secs = ",".join(a.section_ids)
        print(f"  id{a.genre_id} {a.chapter}  节={secs}  ({len(a.paths)} 项)")
        for p in a.paths:
            print(f"    - {p}")
    if not apply:
        print("\n[hint] 预览模式；加 --apply 执行删除。")
        print("[hint] 删除后请重跑 step3 → step6（该 id×C）。")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="[errors_clean] 清理 plan 标题空档违规节的 step3/step6 缓存",
    )
    ap.add_argument("--datasets-dir", default="", help="datasets 根目录")
    ap.add_argument("--ids", default="", help="过滤 id，逗号分隔（空=全库扫描）")
    ap.add_argument("--brief", action="store_true", help="只列出将清理的 id 与章 C")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认仅预览）")
    args = ap.parse_args()

    id_set: set[int] | None = None
    if args.ids.strip():
        id_set = {int(x.strip()) for x in args.ids.split(",") if x.strip()}

    ds = Path(args.datasets_dir) if args.datasets_dir.strip() else default_datasets_dir()
    if not ds.is_dir():
        ap.error(f"datasets 不存在：{ds}")

    if args.brief and not args.apply:
        for rec in group_hard_by_sample(scan_datasets(ds, id_set)):
            print(f"{rec.genre_id} {rec.chapter}")
        return

    actions = collect_cleanup_actions(ds, id_set, apply=bool(args.apply))
    print_cleanup_report(actions, apply=bool(args.apply))


if __name__ == "__main__":
    main()
