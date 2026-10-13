"""Pack pipeline_auto step7 outputs into a flat LongDocFmtEval bench dataset.

Reads ``pipeline_auto/datasets/`` step7 results filtered by **topic id**
(``--ids`` / ``--ids-file``). Each topic×Cx has one assigned ``input_mode``
in ``datasets_factors.json``.

Output layout::

  D:/LongDocFmtEval/1/
  D:/LongDocFmtEval/2/
  ...

Each folder is one ``step7_pairs/<input_mode>/`` sample (``golden.docx``, ``input.docx``,
``template.docx``, ``text_input.md``, ``sample_info.json``, …).

Examples::

  python LongDocFmtEval/pipeline_auto/scripts/build_bench/export_longdocform.py \\
    --ids 1,4,5 --dry-run

  python LongDocFmtEval/pipeline_auto/scripts/build_bench/export_longdocform.py \\
    --ids 1,13,24 --out D:/LongDocFmtEval

  # 强制全量重建
  python LongDocFmtEval/pipeline_auto/scripts/build_bench/export_longdocform.py \\
    --ids 1,4,5 --out D:/LongDocFmtEval --overwrite
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: E402
from LongDocFmtEval.pipeline_auto.scripts.build_bench._paths import (  # noqa: E402
    DEFAULT_IDS_FILE,
    DEFAULT_OUT_ROOT,
    resolve_topic_ids,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.training_factors import DEFAULT_OUTPUT  # noqa: E402
from LongDocFmtEval.pipeline_auto.topic_layout import find_topic_dir_by_id  # noqa: E402

_SKIP_NAMES = frozenset({".ds_store", "manifest.json"})


def _is_step7_sample_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "sample_info.json").is_file() or (path / "golden.docx").is_file()


def _list_copy_files(src_dir: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(src_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.startswith("_"):
            continue
        if p.name.lower() in _SKIP_NAMES:
            continue
        files.append(p)
    return files


def resolve_step7_dir(row: dict[str, Any]) -> Path | None:
    tid = int(row.get("topic_id") or 0)
    topic_dir = find_topic_dir_by_id(tid)
    if topic_dir is None:
        return None
    cx = str(row.get("complexity") or "").strip().upper()
    im = str(row.get("input_mode") or "").strip().lower()
    step7 = topic_dir / cx / "step7_pairs" / im
    return step7 if _is_step7_sample_dir(step7) else None


def collect_samples(
    factors_path: Path,
    *,
    topic_ids: list[int] | None,
    sample_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (samples, skip_notes). Preserves ``datasets_factors.json`` order.

    Each assignment row is one export folder (one ``input_mode`` per topic×Cx).
    ``sample_keys`` further restricts to exact ``topic_Cx_mode`` keys.
    """
    data = load_json(factors_path)
    rows = list(data.get("assignments") or [])
    allow = set(topic_ids) if topic_ids is not None else None
    key_allow = {str(k).strip() for k in sample_keys} if sample_keys is not None else None
    out: list[dict[str, Any]] = []
    notes: list[str] = []
    seen_topics: set[int] = set()
    seen_keys: set[str] = set()

    for row in rows:
        tid = int(row.get("topic_id") or 0)
        sample_key = str(row.get("sample_key") or "")
        if allow is not None and tid not in allow:
            continue
        if key_allow is not None and sample_key not in key_allow:
            continue
        seen_topics.add(tid)
        if sample_key:
            seen_keys.add(sample_key)
        src = resolve_step7_dir(row)
        if src is None:
            notes.append(f"missing step7: {sample_key}")
            continue
        out.append({
            "seq": 0,
            "sample_key": sample_key,
            "factors": dict(row),
            "source_dir": str(src.resolve()),
        })

    if allow is not None and key_allow is None:
        for tid in sorted(allow - seen_topics):
            notes.append(f"topic {tid}: no rows in datasets_factors.json")
    if key_allow is not None:
        for k in sorted(key_allow - seen_keys):
            notes.append(f"sample_key not in factors: {k}")

    return out, notes


def _enrich_sample_info(
    dest: Path,
    *,
    seq: int,
    source_dir: Path,
    factors_row: dict[str, Any],
    factors_path: Path,
) -> None:
    """Merge export metadata + datasets_factors assignment into per-sample sample_info.json."""
    info_path = dest / "sample_info.json"
    info: dict[str, Any] = {}
    if info_path.is_file():
        loaded = load_json(info_path)
        if isinstance(loaded, dict):
            info = loaded
    info["export"] = {
        "seq": int(seq),
        "source_dir": str(source_dir.resolve()),
        "factors_file": str(factors_path.resolve()),
    }
    info["factors"] = dict(factors_row)
    dump_json(info_path, info)


def _export_dir_complete(dest: Path) -> bool:
    if not dest.is_dir():
        return False
    return (dest / "golden.docx").is_file() or (dest / "sample_info.json").is_file()


def build_dataset(
    *,
    out_root: Path,
    factors_path: Path,
    topic_ids: list[int] | None,
    sample_keys: set[str] | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    start_id: int = 1,
) -> int:
    samples, notes = collect_samples(
        factors_path, topic_ids=topic_ids, sample_keys=sample_keys,
    )
    if not samples:
        msg = "no step7 samples found for the given topic id filter"
        if notes:
            msg += "\n  " + "\n  ".join(notes[:20])
        raise SystemExit(msg)

    copied_files = 0
    exported = 0
    skipped = 0

    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    for offset, sample in enumerate(samples):
        seq_id = int(start_id) + offset
        sample["seq"] = seq_id
        dest = out_root / str(seq_id)
        src = Path(sample["source_dir"])
        file_names = _list_copy_files(src)
        cached = _export_dir_complete(dest)
        if dry_run:
            tag = "skip" if cached and not overwrite else "copy"
            print(
                f"[dry-run:{tag}] {seq_id:4d}  {sample['sample_key']:36}  "
                f"files={len(file_names)}  dest={dest}"
            )
            if tag == "skip":
                skipped += 1
            else:
                exported += 1
            continue
        if cached and not overwrite:
            print(f"[skip] {seq_id:4d}  {sample['sample_key']:36}  {dest}")
            skipped += 1
            continue
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for fp in file_names:
            shutil.copy2(fp, dest / fp.name)
            copied_files += 1
        _enrich_sample_info(
            dest,
            seq=seq_id,
            source_dir=src,
            factors_row=sample["factors"],
            factors_path=factors_path,
        )
        exported += 1
        print(f"[ok]   {seq_id:4d}  {sample['sample_key']:36}  {dest}")

    if notes:
        print(f"[skip] {len(notes)} assignment(s) without step7 on disk")
        for line in notes[:30]:
            print(f"  - {line}")
        if len(notes) > 30:
            print(f"  … and {len(notes) - 30} more")

    if not dry_run:
        print(
            f"[ok] exported {exported}, skipped {skipped} (cached), "
            f"{copied_files} files copied"
        )

    return len(samples)


def _load_sample_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for part in line.replace("，", ",").split(","):
            k = part.strip()
            if k:
                keys.add(k)
    return keys


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Export step7 samples to D:/LongDocFmtEval/{1,2,3…}. "
            "One folder per factors assignment (1 input_mode per topic×Cx)."
        ),
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT, help="Dataset root")
    ap.add_argument("--factors", type=Path, default=DEFAULT_OUTPUT, help="datasets_factors.json")
    ap.add_argument("--datasets-dir", type=Path, default=DATASETS_DIR, help="(reserved) datasets root")
    ap.add_argument("--ids", default="", help="Topic ids: 1,4,5 or 1-3")
    ap.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help=f"Topic ids file (default when neither --ids/--keys/--all: {DEFAULT_IDS_FILE.name})",
    )
    ap.add_argument(
        "--sample-keys",
        default="",
        help="Exact sample_key list (e.g. 1_C1_text_requirement,6_C2_template)",
    )
    ap.add_argument(
        "--keys-file",
        type=Path,
        default=None,
        help="File of sample_key lines/commas (e.g. config/eval_export_keys.txt)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Export every assignment with step7 (no topic filter; use with care)",
    )
    ap.add_argument("--start-id", type=int, default=1, help="First numeric folder id")
    ap.add_argument("--overwrite", action="store_true", help="Rebuild existing sample folders (default: skip)")
    ap.add_argument("--dry-run", action="store_true", help="List planned copies only")
    args = ap.parse_args()

    del args.datasets_dir  # reserved

    sample_keys: set[str] | None = None
    if str(args.sample_keys or "").strip():
        sample_keys = {
            p.strip()
            for p in str(args.sample_keys).replace("，", ",").split(",")
            if p.strip()
        }
    if args.keys_file is not None:
        loaded = _load_sample_keys(args.keys_file)
        sample_keys = loaded if sample_keys is None else (sample_keys | loaded)

    if args.all:
        topic_ids = None
    elif sample_keys is not None:
        # Keys already pin exact assignments; do not also require topic filter.
        topic_ids = None
    elif str(args.ids or "").strip():
        topic_ids = resolve_topic_ids(ids_text=args.ids)
    else:
        ids_file = args.ids_file if args.ids_file is not None else DEFAULT_IDS_FILE
        topic_ids = resolve_topic_ids(ids_file=ids_file)

    n = build_dataset(
        out_root=args.out.resolve(),
        factors_path=args.factors.resolve(),
        topic_ids=topic_ids,
        sample_keys=sample_keys,
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        start_id=int(args.start_id),
    )
    if args.dry_run:
        print(f"\n[dry-run] would export {n} samples → {args.out}")
    else:
        print(f"\nDone: {n} samples → {args.out}")


if __name__ == "__main__":
    main()
