"""Assign frozen full-set dataset factors (seed=1234) once.

Writes ``config/datasets_factors.json``. Source of truth for step7:
one ``input_mode`` per topic×Cx (modes balanced across content units).
Do not regenerate unless you pass ``--force``.

Example:
  python LongDocFmtEval/pipeline_auto/scripts/assign_training_factors.py
  python LongDocFmtEval/pipeline_auto/scripts/assign_training_factors.py --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json
from LongDocFmtEval.pipeline_auto.seed.training_factors import (
    DEFAULT_OUTPUT,
    FROZEN_SEED,
    build_assignments,
    load_genre_rows,
    summarize_assignments,
)


def _filter_rows(rows: list[dict], ids: set[int] | None) -> list[dict]:
    if not ids:
        return rows
    return [r for r in rows if int(r["topic_id"]) in ids]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Write frozen datasets_factors.json (seed=1234). Refuses overwrite unless --force."
    )
    ap.add_argument("--genres-xlsx", type=Path, default=None, help="Override xlsx (default: genres.xlsx)")
    ap.add_argument("--pools", type=Path, default=CONFIG_DIR / "training_factor_pools.yaml")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--seed", type=int, default=FROZEN_SEED, help="Must stay 1234 for the frozen file")
    ap.add_argument("--ids", default="", help="Comma-separated topic ids (default: all enabled)")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing datasets_factors.json (breaks the freeze)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print summary only; do not write files")
    args = ap.parse_args()

    if args.out.is_file() and not args.force and not args.dry_run:
        raise SystemExit(
            f"{args.out} already exists and is frozen. "
            "Step7 reads this file as-is. Pass --force only if you intend to reassign."
        )

    id_set: set[int] | None = None
    if str(args.ids or "").strip():
        id_set = {int(x.strip()) for x in str(args.ids).split(",") if x.strip()}

    rows = _filter_rows(load_genre_rows(args.genres_xlsx), id_set)
    if not rows:
        raise SystemExit(f"no genre rows from {args.genres_xlsx}")

    import yaml

    pools = yaml.safe_load(args.pools.read_text(encoding="utf-8"))
    master_seed = int(args.seed)
    data = build_assignments(rows, pools=pools, master_seed=master_seed)

    print(summarize_assignments(data))
    print("\n--- sample preview (first 12) ---")
    for r in (data.get("assignments") or [])[:12]:
        print(
            f"  {r['sample_key']:32}  {r.get('locale','?'):2}  "
            f"mode={str(r.get('input_mode','')):18}  "
            f"scheme={r.get('init', {}).get('numbering', {}).get('scheme_id')}  "
            f"heading_styles={r.get('init', {}).get('heading_styles')}  "
            f"num={r.get('init', {}).get('numbering', {}).get('enabled')}  "
            f"text={r.get('text')}"
        )

    if args.dry_run:
        print("\n[dry-run] no files written")
        return

    dump_json(args.out, data)
    print(f"\n[ok] wrote {args.out} ({len(data.get('assignments') or [])} samples, seed={master_seed})")


if __name__ == "__main__":
    main()
