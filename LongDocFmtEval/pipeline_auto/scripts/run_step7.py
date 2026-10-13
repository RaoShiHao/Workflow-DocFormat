"""Step 7 · factor-driven instance → C*/step7_pairs/<input_mode>/.

Default: one assigned ``input_mode`` per topic×Cx (from ``datasets_factors.json``).
Pass ``--input-mode`` to force a single mode (must exist in factors).

用法：
  python LongDocFmtEval/pipeline_auto/scripts/run_step7.py --id 1 --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_step7.py --ids 1,2,3 --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_step7.py --id 66 --complexity C2 --input-mode template
  python LongDocFmtEval/pipeline_auto/scripts/run_step7.py --from-step6 <step6_document>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg  # noqa: E402
from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.build_step7_instance import (  # noqa: E402
    build_step7_instance,
    load_factors_for_step6,
    parse_sample_key_from_step6,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_json  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.training_factors import (  # noqa: E402
    DEFAULT_OUTPUT,
    INPUT_MODES,
    assignments_for_content,
)
from LongDocFmtEval.pipeline_auto.topic_layout import document_dir, resolve_topic_dir  # noqa: E402


def modes_for_step6(step6_dir: Path, factors_path: Path, *, forced: str = "") -> list[str]:
    forced_im = str(forced or "").strip().lower()
    if forced_im:
        return [forced_im]
    tid, cx = parse_sample_key_from_step6(step6_dir)
    data = load_json(factors_path)
    rows = assignments_for_content(data, tid, cx)
    modes = [str(r.get("input_mode") or "").strip().lower() for r in rows if r.get("input_mode")]
    if not modes:
        raise KeyError(f"no factors for {tid}_{cx} in {factors_path}")
    return modes


def resolve_step6_dir(
    *,
    topic_id: int | None = None,
    complexity: str = "C1",
    from_step6: str = "",
) -> Path:
    if from_step6:
        p = Path(from_step6).resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"--from-step6 不存在: {from_step6}")
        return p
    if topic_id is None:
        raise ValueError("请指定 --from-step6 或 --id")
    topic = resolve_topic_dir(int(topic_id))
    cx = str(complexity or "C1").strip().upper()
    doc = document_dir(topic, complexity=cx)
    if not (doc / "document.docx").is_file():
        raise FileNotFoundError(f"缺少 document.docx: {doc}")
    return doc.resolve()


def resolve_out_dir(step6_dir: Path, out_dir: str | None, *, input_mode: str = "") -> Path:
    if out_dir:
        return Path(out_dir).resolve()
    base = (step6_dir.parent / "step7_pairs").resolve()
    im = str(input_mode or "").strip().lower()
    if im in ("text_requirement", "template", "template_w_text"):
        return base / im
    return base


def run_one(
    step6_dir: Path,
    *,
    out_dir: Path | None = None,
    factors: dict | None = None,
    factors_path: Path | None = None,
    input_mode: str = "text_requirement",
    seed: int = 42,
    overwrite: bool = False,
) -> bool:
    step6_dir = step6_dir.resolve()
    dest = resolve_out_dir(step6_dir, str(out_dir) if out_dir else None, input_mode=input_mode)
    cached = (dest / "sample_info.json").is_file()
    if dest.exists() and cached and not overwrite:
        print(f"[cache] step7 已有：{dest}（--overwrite 重建）")
        return True

    fac = factors or load_factors_for_step6(
        step6_dir, global_factors_path=factors_path, input_mode=input_mode,
    )
    from LongDocFmtEval.pipeline_auto.seed.training_factors import (  # noqa: PLC0415
        init_heading_styles,
        init_structure_name,
    )

    init = fac.get("init") if isinstance(fac.get("init"), dict) else {}
    num = (init.get("numbering") or {}) if init else {}
    print(f"\n=== step7: {fac.get('sample_key', step6_dir.parent.name)} ===")
    print(f"  [in]  {step6_dir / 'document.docx'}")
    print(f"  [out] {dest}")
    print(
        f"  factors: heading_styles={init_heading_styles(fac)} "
        f"numbering={num.get('enabled')} scheme={num.get('scheme_id')}"
    )

    build_step7_instance(step6_dir, dest, fac, seed=seed)
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_json  # noqa: PLC0415
    info = load_json(dest / "sample_info.json") if (dest / "sample_info.json").is_file() else {}
    info_init = info.get("init") if isinstance(info.get("init"), dict) else {}
    info_num = info_init.get("numbering") if isinstance(info_init.get("numbering"), dict) else {}
    info_text = info.get("text") if isinstance(info.get("text"), dict) else {}
    print(
        f"  [ok] golden.docx  numbered={info_num.get('enabled')} "
        f"text_notes={info_text.get('target_notes')} "
        f"text_num_note={info_text.get('numbering_note')}"
    )
    print(f"  [ok] input.docx  init={init_structure_name(fac)}")
    if info.get("files", {}).get("template"):
        print("  [ok] template.docx")
    if info.get("files", {}).get("text_input"):
        print("  [ok] text_input.md")
    print("  [ok] sample_info.json")
    return True


def resolve_topic_ids(*, topic_id: int | None, ids_text: str) -> list[int]:
    out: list[int] = []
    if str(ids_text or "").strip():
        out.extend(parse_ids_arg(ids_text))
    if topic_id is not None:
        out.append(int(topic_id))
    return sorted(set(out))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Step7：factor-driven 实例 → step7_pairs/<input_mode>/（默认按 factors 分配的单一 mode）"
    )
    ap.add_argument("--id", type=int, default=None, help="单个 topic id（可与 --ids 合用）")
    ap.add_argument("--ids", default="", help="多个 topic id（逗号分隔，支持 1-3 范围）")
    ap.add_argument("--complexity", default="", help="C1/C2/C3，默认全部")
    ap.add_argument("--from-step6", default="", help="显式 step6_document 目录")
    ap.add_argument("--out-dir", default="", help="输出目录（仅单 step6 + 单 input_mode 时生效）")
    ap.add_argument("--factors", type=Path, default=DEFAULT_OUTPUT, help="datasets_factors.json")
    ap.add_argument(
        "--input-mode",
        default="",
        choices=INPUT_MODES,
        help="只生成一种 input_mode（默认：datasets_factors 为该 content 分配的 mode）",
    )
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--seed", type=int, default=42, help="input.docx plain-transform RNG seed")
    args = ap.parse_args()

    dirs: list[Path] = []
    if args.from_step6:
        dirs.append(resolve_step6_dir(from_step6=args.from_step6))
    else:
        id_list = resolve_topic_ids(topic_id=args.id, ids_text=args.ids)
        if not id_list:
            ap.error("请指定 --from-step6 或 --id / --ids")
        cx_list = [
            c.strip().upper()
            for c in str(args.complexity or "").split(",")
            if c.strip()
        ] or list(COMPLEXITY_TIERS)
        for tid in id_list:
            for cx in cx_list:
                dirs.append(resolve_step6_dir(topic_id=tid, complexity=cx))

    forced = str(args.input_mode or "").strip().lower()
    ok = 0
    total = 0
    out_override = Path(args.out_dir) if args.out_dir else None
    for step6 in dirs:
        try:
            modes = modes_for_step6(step6, args.factors, forced=forced)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {step6}: {exc}")
            total += 1
            continue
        use_out_override = out_override is not None and len(dirs) == 1 and len(modes) == 1
        for im in modes:
            total += 1
            try:
                if run_one(
                    step6,
                    out_dir=out_override if use_out_override else None,
                    factors_path=args.factors,
                    input_mode=im,
                    seed=int(args.seed),
                    overwrite=bool(args.overwrite),
                ):
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {step6} ({im}): {exc}")
    print(f"\n完成 {ok}/{total}")


if __name__ == "__main__":
    main()
