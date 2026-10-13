"""检测 step3d plan.json 中标题下正文槽位是否符合硬规则。

仅读 plan（step3_outline/plan.json 或 step5/6 旁 copy），不读 step6 content。

用法:
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/audit_plan_body_slots.py --ids 64 --brief
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/audit_plan_body_slots.py --ids 64,65 --soft
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/audit_plan_body_slots.py --plan-dir path/to/step3_outline
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from LongDocFmtEval.pipeline_auto.auto_paths import resolve_outline_sidecar
from LongDocFmtEval.pipeline_auto.scripts.errors_clean._paths import default_datasets_dir
from LongDocFmtEval.pipeline_auto.scripts.errors_clean.plan_body_slots import (
    PlanViolation,
    audit_section_blocks,
    group_hard_by_sample,
    load_json,
    scan_datasets,
    style_maps,
)


def print_brief(hits: list[PlanViolation]) -> None:
    for rec in group_hard_by_sample(hits):
        print(f"{rec.genre_id} {rec.chapter}")


def print_summary(hits: list[PlanViolation]) -> None:
    from collections import Counter

    hard = [h for h in hits if h.rule.startswith("hard_")]
    soft = [h for h in hits if h.rule.startswith("soft_")]
    by_sample = Counter((h.genre_id, h.chapter) for h in hard)
    by_rule = Counter(h.rule for h in hard)
    bad_sections = sorted({(h.genre_id, h.chapter, h.section_id) for h in hard})

    print("\n[summary] step3d plan 硬规则检测（仅 plan.json blocks）")
    print(f"  硬违规: {len(hard)} 条，涉及 {len(by_sample)} 个 id×C，{len(bad_sections)} 个节")
    for rule, n in by_rule.most_common():
        label = {"hard_same_level": "(1)同级间", "hard_leaf": "(2)叶下"}.get(rule, rule)
        print(f"    {label}: {n}")
    print(f"  软提示 (3) 跨级无衔接: {len(soft)} 条")
    if bad_sections:
        sec_str = ", ".join(f"id{g}{c}/{s}" for g, c, s in bad_sections[:20])
        if len(bad_sections) > 20:
            sec_str += f" …共{len(bad_sections)}节"
        print(f"  待修节: {sec_str}")


def print_details(hits: list[PlanViolation], *, include_soft: bool) -> None:
    hard = [h for h in hits if h.rule.startswith("hard_")]
    soft = [h for h in hits if h.rule.startswith("soft_")]

    if hard:
        print("\n[hard] 必须修复（step3d 重规划该节 blocks）")
        for h in hard:
            tag = "同级" if h.rule == "hard_same_level" else "叶下"
            print(f"  id{h.genre_id:>3} {h.chapter} {h.section_id} [{tag}] {h.detail}")
    else:
        print("\n[hard] 无硬违规。")

    if include_soft and soft:
        print("\n[soft] 可选改进（不 gate）")
        for h in soft[:30]:
            print(f"  id{h.genre_id:>3} {h.chapter} {h.section_id} {h.detail}")
        if len(soft) > 30:
            print(f"  … 还有 {len(soft) - 30} 条 soft")


def main() -> None:
    ap = argparse.ArgumentParser(description="检测 step3d plan 标题下正文槽位（硬规则 1/2）")
    ap.add_argument("--datasets-dir", default="", help="datasets 根目录")
    ap.add_argument("--plan-dir", default="", help="单个 step3_outline（或含 plan.json 的目录）")
    ap.add_argument("--ids", default="", help="过滤 id，逗号分隔")
    ap.add_argument("--brief", action="store_true", help="只列出有硬违规的 id 与章 C，一行一个")
    ap.add_argument("--soft", action="store_true", help="明细中包含软规则 (3)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    id_set: set[int] | None = None
    if args.ids.strip():
        id_set = {int(x.strip()) for x in args.ids.split(",") if x.strip()}

    scan_label = ""
    if args.plan_dir.strip():
        plan_dir = Path(args.plan_dir)
        plan_path = plan_dir / "plan.json" if plan_dir.is_dir() else plan_dir
        if not plan_path.is_file():
            plan_path = resolve_outline_sidecar(plan_dir, "plan.json")
        if not plan_path.is_file():
            ap.error(f"未找到 plan.json：{args.plan_dir}")
        plan = load_json(plan_path)
        idx, name_by, obj_by = style_maps(plan)
        hits: list[PlanViolation] = []
        for sec_id, blocks in {
            str(o.get("instance_id")): (o.get("blocks") or [])
            for o in (plan.get("outline") or [])
        }.items():
            for rule, detail, bids in audit_section_blocks(blocks, idx, name_by, obj_by):
                hits.append(PlanViolation(0, "?", plan_dir.name, sec_id, rule, detail, bids))
        scan_label = str(plan_path)
    else:
        ds = Path(args.datasets_dir) if args.datasets_dir.strip() else default_datasets_dir()
        if not ds.is_dir():
            ap.error(f"datasets 不存在：{ds}")
        hits = scan_datasets(ds, id_set)
        scan_label = str(ds)

    if args.json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2))
        return

    if args.brief:
        print_brief(hits)
        return

    print(f"[scan] {scan_label}")
    if id_set and not args.plan_dir.strip():
        print(f"[filter] ids={sorted(id_set)}")

    print_summary(hits)
    print_details(hits, include_soft=bool(args.soft))


if __name__ == "__main__":
    main()
