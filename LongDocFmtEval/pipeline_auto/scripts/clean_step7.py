"""Remove step7_pairs outputs (all files under C*/step7_pairs/).

Does not touch step5/step6. Use before a full step7 rebuild.

Examples:
  python LongDocFmtEval/pipeline_auto/scripts/clean_step7.py --ids 1,4,5 --dry-run
  python LongDocFmtEval/pipeline_auto/scripts/clean_step7.py --ids 1 --apply
  python LongDocFmtEval/pipeline_auto/scripts/clean_step7.py --apply
  python LongDocFmtEval/pipeline_auto/scripts/clean_step7.py --id 1 --complexity C2 --input-mode template_w_text --apply
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg
from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS
from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR
from LongDocFmtEval.pipeline_auto.seed.training_factors import INPUT_MODES
from LongDocFmtEval.pipeline_auto.topic_layout import resolve_topic_dir


def _parse_complexities(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return list(COMPLEXITY_TIERS)
    out: list[str] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        cx = part.upper()
        if not cx.startswith("C"):
            cx = f"C{int(cx)}"
        out.append(cx)
    return out or list(COMPLEXITY_TIERS)


def iter_step7_targets(
    topic_dir: Path,
    *,
    complexities: list[str],
    input_mode: str = "",
) -> list[Path]:
    mode = str(input_mode or "").strip().lower()
    out: list[Path] = []
    for cx in complexities:
        pairs = topic_dir / cx / "step7_pairs"
        if not pairs.is_dir():
            continue
        if mode:
            target = pairs / mode
            if target.is_dir():
                out.append(target)
            continue
        out.append(pairs)
    return out


def _count_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete C*/step7_pairs/ outputs")
    ap.add_argument("--id", type=int, default=None)
    ap.add_argument("--ids", default="", help="Comma-separated topic ids (supports ranges)")
    ap.add_argument("--complexity", default="", help="C1/C2/C3; default all")
    ap.add_argument(
        "--input-mode",
        default="",
        choices=["", *INPUT_MODES],
        help="Only remove one mode subfolder; default entire step7_pairs",
    )
    ap.add_argument("--datasets-dir", type=Path, default=DATASETS_DIR)
    ap.add_argument("--dry-run", action="store_true", help="List targets only")
    ap.add_argument("--apply", action="store_true", help="Actually delete (required)")
    args = ap.parse_args()

    ids: list[int] = []
    if args.id is not None:
        ids.append(int(args.id))
    if str(args.ids or "").strip():
        ids.extend(parse_ids_arg(args.ids))
    ids = sorted(set(ids))
    if not ids:
        ids = sorted(
            int(p.name.split("_", 1)[0])
            for p in Path(args.datasets_dir).iterdir()
            if p.is_dir() and p.name.split("_", 1)[0].isdigit()
        )

    complexities = _parse_complexities(args.complexity)
    targets: list[Path] = []
    for tid in ids:
        try:
            topic = resolve_topic_dir(int(tid))
        except FileNotFoundError:
            print(f"[skip] topic {tid}: not found under {args.datasets_dir}")
            continue
        targets.extend(
            iter_step7_targets(
                topic, complexities=complexities, input_mode=args.input_mode,
            )
        )

    if not targets:
        print("No step7_pairs directories matched.")
        return

    total_files = sum(_count_files(t) for t in targets)
    print(f"Matched {len(targets)} step7 target(s), ~{total_files} file(s)")
    for t in targets:
        print(f"  {t}")

    if args.dry_run:
        print("\n[dry-run] pass --apply to delete")
        return
    if not args.apply:
        print("\nRefusing to delete without --apply (use --dry-run to preview)")
        return

    removed_dirs = 0
    removed_files = 0
    for t in targets:
        n = _count_files(t)
        if t.name in INPUT_MODES:
            shutil.rmtree(t, ignore_errors=True)
        else:
            shutil.rmtree(t, ignore_errors=True)
        removed_dirs += 1
        removed_files += n
        print(f"[ok] removed {t} ({n} files)")

    print(f"\nDone: {removed_dirs} dir(s), {removed_files} file(s)")


if __name__ == "__main__":
    main()
