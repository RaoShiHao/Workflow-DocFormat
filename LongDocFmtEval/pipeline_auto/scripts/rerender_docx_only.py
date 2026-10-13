"""仅用 officecli 重渲 step5 ``template.docx`` 与 step6 ``document.docx``。

- **不**跑 LLM、specimen、图规划/重画
- 走与 step5/step6 相同的 ``render_template_docx.render()``，含渲染护栏：
  margin 裸数字→cm；lineSpacing 裸数字 <3→x、>10→pt
- officecli / ``render()`` 抛错时记录该样本，继续下一个
- 结束写出失败清单 JSON（默认 ``datasets/_batch_logs/rerender_docx_failures.json``）

Examples::

  python LongDocFmtEval/pipeline_auto/scripts/rerender_docx_only.py --ids 1,12,56
  python LongDocFmtEval/pipeline_auto/scripts/rerender_docx_only.py --ids 1,12 --complexity C1,C2,C3
  python LongDocFmtEval/pipeline_auto/scripts/rerender_docx_only.py --all
  python LongDocFmtEval/pipeline_auto/scripts/rerender_docx_only.py --ids-file LongDocFmtEval/pipeline_auto/config/ids.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg  # noqa: E402
from LongDocFmtEval.common.llm import load_json  # noqa: E402
from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS  # noqa: E402
from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR, DATASETS_DIR  # noqa: E402
from LongDocFmtEval.pipeline_auto.scripts.build_bench._paths import (  # noqa: E402
    load_ids_from_file,
)
from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import render  # noqa: E402
from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: E402
    document_dir,
    find_topic_dir_by_id,
    parse_complexity_arg,
    template_dir,
)


def _topic_id_from_dirname(name: str) -> int | None:
    head = str(name or "").split("_", 1)[0]
    return int(head) if head.isdigit() else None


def _list_topic_ids(*, ids_text: str, ids_file: str, all_topics: bool) -> list[int]:
    if all_topics:
        if not DATASETS_DIR.is_dir():
            return []
        out: list[int] = []
        for p in sorted(DATASETS_DIR.iterdir()):
            if not p.is_dir() or p.name.startswith("_"):
                continue
            tid = _topic_id_from_dirname(p.name)
            if tid is not None:
                out.append(tid)
        return sorted(set(out))
    ids: list[int] = []
    if str(ids_text or "").strip():
        ids.extend(parse_ids_arg(ids_text))
    if str(ids_file or "").strip():
        ids.extend(load_ids_from_file(Path(ids_file)))
    return sorted(set(ids))


def _has_styles(tpl: Path) -> bool:
    for name in ("role_params.json", "template.json", "style_tree.json"):
        if (tpl / name).is_file():
            return True
    # often only on step4_styles; render resolves via resolve_work_file / load_merged_roles
    styles = tpl.parent / "step4_styles"
    return any((styles / n).is_file() for n in ("role_params.json", "template.json"))


def _render_step5(tpl: Path) -> Path:
    return render(
        tpl,
        out_name="template.docx",
        assets_name="assets_preview.json",
        annotated=True,
    )


def _render_step6(doc: Path) -> Path:
    content = load_json(doc / "content.json")
    blocks = content.get("blocks") if isinstance(content, dict) else None
    if not isinstance(blocks, dict):
        raise RuntimeError("content.json 缺少 blocks 字典")
    return render(
        doc,
        content=blocks,
        annotated=False,
        out_name="document.docx",
        assets_name="assets.json",
    )


def _run_one(
    topic_id: int,
    complexity: str,
    *,
    steps: set[str],
) -> list[dict[str, Any]]:
    """Return list of event records (ok / skip / fail)."""
    events: list[dict[str, Any]] = []
    topic = find_topic_dir_by_id(topic_id)
    if topic is None or not topic.is_dir():
        events.append({
            "status": "skip",
            "id": topic_id,
            "complexity": complexity,
            "step": None,
            "reason": "topic dir not found",
        })
        return events

    base = {
        "id": topic_id,
        "complexity": complexity,
        "topic": topic.name,
    }

    if "5" in steps:
        tpl = template_dir(topic, complexity=complexity)
        rec = {**base, "step": 5, "path": str(tpl).replace("\\", "/")}
        if not tpl.is_dir():
            events.append({**rec, "status": "skip", "reason": "step5_template missing"})
        elif not _has_styles(tpl):
            events.append({**rec, "status": "skip", "reason": "no role_params/template.json"})
        else:
            try:
                out = _render_step5(tpl)
                events.append({
                    **rec,
                    "status": "ok",
                    "docx": str(out).replace("\\", "/"),
                    "size": out.stat().st_size if out.is_file() else 0,
                })
                print(f"  [ok] step5 {topic.name}/{complexity} → {out.name}")
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                events.append({
                    **rec,
                    "status": "fail",
                    "error": err,
                    "traceback": traceback.format_exc(limit=8),
                })
                print(f"  [FAIL] step5 {topic.name}/{complexity}: {err}")

    if "6" in steps:
        doc = document_dir(topic, complexity=complexity)
        rec = {**base, "step": 6, "path": str(doc).replace("\\", "/")}
        content_path = doc / "content.json"
        if not content_path.is_file():
            events.append({**rec, "status": "skip", "reason": "no content.json"})
        else:
            try:
                out = _render_step6(doc)
                events.append({
                    **rec,
                    "status": "ok",
                    "docx": str(out).replace("\\", "/"),
                    "size": out.stat().st_size if out.is_file() else 0,
                })
                print(f"  [ok] step6 {topic.name}/{complexity} → {out.name}")
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                events.append({
                    **rec,
                    "status": "fail",
                    "error": err,
                    "traceback": traceback.format_exc(limit=8),
                })
                print(f"  [FAIL] step6 {topic.name}/{complexity}: {err}")

    return events


def main() -> None:
    ap = argparse.ArgumentParser(
        description="只重渲 step5/step6 docx（无 LLM）；officecli 失败写入报告",
    )
    ap.add_argument("--ids", default="", help="topic id，如 1,12,56 或 1-3")
    ap.add_argument(
        "--ids-file",
        default="",
        help=f"从文件解析 id（默认也可不传）。例: {CONFIG_DIR / 'ids.txt'}",
    )
    ap.add_argument("--all", action="store_true", help="datasets/ 下全部 topic")
    ap.add_argument(
        "--complexity", "--c",
        dest="complexity",
        default="",
        help="C1 / C2 / C1,C3（默认 C1,C2,C3）",
    )
    ap.add_argument(
        "--steps",
        default="5,6",
        help="要渲的步骤，默认 5,6；可只写 5 或 6",
    )
    ap.add_argument(
        "--out",
        default="",
        help="失败报告 JSON 路径（默认 datasets/_batch_logs/rerender_docx_failures.json）",
    )
    args = ap.parse_args()

    try:
        ids = _list_topic_ids(
            ids_text=args.ids,
            ids_file=args.ids_file,
            all_topics=bool(args.all),
        )
    except (FileNotFoundError, ValueError) as exc:
        ap.error(str(exc))
    if not ids:
        ap.error("请指定 --ids / --ids-file / --all")

    complexities = parse_complexity_arg(args.complexity) or list(COMPLEXITY_TIERS)
    steps: set[str] = set()
    for part in str(args.steps).replace(" ", "").split(","):
        if part in ("5", "6"):
            steps.add(part)
    if not steps:
        ap.error("--steps 只能包含 5 和/或 6")

    out_path = Path(args.out) if args.out else (
        DATASETS_DIR / "_batch_logs" / "rerender_docx_failures.json"
    )

    print(
        f"[rerender-docx] topics={len(ids)} complexities={','.join(complexities)} "
        f"steps={','.join(sorted(steps))} (no LLM)"
    )
    print(f"[datasets] {DATASETS_DIR.resolve()}")

    all_events: list[dict[str, Any]] = []
    for tid in ids:
        print(f"\n=== topic {tid} ===")
        for cx in complexities:
            all_events.extend(_run_one(tid, cx, steps=steps))

    failed = [e for e in all_events if e.get("status") == "fail"]
    skipped = [e for e in all_events if e.get("status") == "skip"]
    ok = [e for e in all_events if e.get("status") == "ok"]

    report = {
        "mode": "rerender_docx_only",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ids": ids,
        "complexities": complexities,
        "steps": sorted(steps),
        "counts": {
            "ok": len(ok),
            "fail": len(failed),
            "skip": len(skipped),
            "total_jobs": len(all_events),
        },
        "failed": failed,
        "skipped": skipped,
        "ok": [
            {k: e[k] for k in ("id", "complexity", "step", "docx", "size") if k in e}
            for e in ok
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Compact failed sample list for quick retry
    fail_ids_path = out_path.with_name(out_path.stem + "_ids.txt")
    fail_keys = sorted({
        f"{e['id']}/{e['complexity']}/step{e['step']}"
        for e in failed
        if e.get("id") is not None and e.get("complexity") and e.get("step")
    })
    fail_ids_path.write_text("\n".join(fail_keys) + ("\n" if fail_keys else ""), encoding="utf-8")

    print(
        f"\n[done] ok={len(ok)} fail={len(failed)} skip={len(skipped)} "
        f"→ {out_path}"
    )
    if failed:
        print(f"[failed list] {fail_ids_path}")
        for e in failed:
            print(
                f"  - id={e.get('id')} {e.get('complexity')} step{e.get('step')}: "
                f"{e.get('error', '')[:200]}"
            )
        raise SystemExit(1)


# if __name__ == "__main__":
#     main()

if __name__ == "__main__":
    import sys
    # 模拟命令行参数 - 在这里修改 ID 列表
    sys.argv = [
        "rerender_docx_only.py",
        # 1, 4, 5,
        "--ids",
        "93,94,10,11,13,14,17,22,24,25,26,28,30,32,35,36,37,40,47,48,49,54,56,62"
    ]
    main()