"""Accuracy / hallucination vs golden on officecli whitelist keys.

accuracy           = equal-weight mean of object modes
                     (section / paragraph(+run) / table / image) on need_change
hallucination_rate = same four-mode average on must-not-change keys

Key universe is always **golden.flat**: keys come from golden.docx readback.
Missing keys on input/output are treated as unset (None) — not as “skip this check”.
Baselines (same snapshot / same file bytes):
  pred ≡ init  → accuracy 0 on non-empty need_change
  pred ≡ gold  → accuracy 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from LongDocFmt.workflow.officecli_doc import close_docx, inventory_bundle, open_docx
from LongDocFmt.workflow.whitelist import filter_props, whitelist_keys

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?")
_PAGE_NUM_TEXT_RE = re.compile(r"^(\d+|[ivxlcdm]+|[IVXLCDM]+)$")
_LAYER_PREFIX = {
    "section": "section",
    "paragraph.body": "paragraphs",
    "paragraph.table_cell": "paragraph_cells",
    "table": "tables",
    "image": "images",
    "run": "runs",
}

# Object-level equal-weight avg: section / body (paragraph+run) / table / image.
# paragraph mode folds runs into body text; table folds table_cell + table_paragraph.
# DETAIL_MODES are diagnostics only (not in accuracy / accuracy_by_mode).
EVAL_MODES = ("section", "paragraph", "table", "image")
TABLE_SPLIT_MODES = ("table_paragraph", "table_cell")
BODY_SPLIT_MODES = ("paragraph", "run")
DETAIL_MODES = ("run", "table_paragraph", "table_cell")
REPORT_MODES = ("section", "paragraph", "table", "table_paragraph", "table_cell", "image", "run")
# Flat JSON layout unchanged; densify-on-gold happens at score time only.
# 3: eval snapshots use inventory profile=eval (same flatten keys as full).
FLAT_SCHEMA = 3

_PREFIX_TO_MODE = {
    "section": "section",
    "paragraphs": "paragraph",
    "paragraph_cells": "table_paragraph",
    "tables": "table_cell",
    "images": "image",
    "runs": "run",
}


def _to_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    m = _NUM_RE.match(s)
    if not m:
        return None
    n = float(m.group(0))
    if s.endswith("cm"):
        return n
    if s.endswith("pt"):
        return n * 0.0352778
    if s.endswith("mm"):
        return n / 10.0
    return n


def values_equal(a: Any, b: Any, *, tol: float = 0.15) -> bool:
    if a in (None, "", "none") and b in (None, "", "none"):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    fa, fb = _to_float(a), _to_float(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= tol
    return str(a).strip().lower() == str(b).strip().lower()


def project_on_gold(flat: Dict[str, Any] | None, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Restrict ``flat`` to golden keys; unread/unset attributes become None."""
    flat = flat or {}
    return {k: (flat[k] if k in flat else None) for k in gold}


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def same_document(a: Path, b: Path) -> bool:
    """True when paths resolve equal or file bytes match (copy of input/golden as output)."""
    pa, pb = Path(a).resolve(), Path(b).resolve()
    if pa == pb:
        return True
    if not pa.is_file() or not pb.is_file():
        return False
    if pa.stat().st_size != pb.stat().st_size:
        return False
    return file_digest(pa) == file_digest(pb)


def flatten_doc(elements_by_layer: Dict[str, List[Any]]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for layer, els in (elements_by_layer or {}).items():
        keys = whitelist_keys(layer)
        prefix = _LAYER_PREFIX.get(layer, layer)
        for el in els:
            loc = el.location_id
            if layer == "table":
                tf = el.props.get("table_format") or {}
                for k, v in filter_props(tf, keys).items():
                    flat[f"{prefix}.{loc}.table_format.{k}"] = v
                for slot, chrome in (el.props.get("cells") or {}).items():
                    if not isinstance(chrome, dict):
                        continue
                    for k, v in chrome.items():
                        flat[f"{prefix}.{loc}.cells.{slot}.{k}"] = v
                continue
            props = filter_props(el.props, keys)
            props.pop("_header_footer", None)
            for k, v in props.items():
                if isinstance(v, dict):
                    continue
                flat[f"{prefix}.{loc}.{k}"] = v
            hf = (el.meta or {}).get("header_footer") or {}
            if layer == "section" and isinstance(hf, dict):
                for part, bag in hf.items():
                    if not isinstance(bag, dict):
                        continue
                    for k, v in bag.items():
                        if v in (None, ""):
                            continue
                        if part == "footer" and k == "text" and _PAGE_NUM_TEXT_RE.match(str(v).strip()):
                            continue
                        flat[f"{prefix}.{loc}.{part}.{k}"] = v
    return flat


def mode_of_key(key: str) -> str:
    prefix = key.split(".", 1)[0]
    return _PREFIX_TO_MODE.get(prefix, prefix)


def key_in_mode(key: str, mode: str) -> bool:
    leaf = mode_of_key(key)
    if mode == "table":
        return leaf in TABLE_SPLIT_MODES
    if mode == "paragraph":
        # Body object bucket: paragraphs.* + runs.* (run remains reportable alone).
        return leaf in BODY_SPLIT_MODES
    return leaf == mode


def object_mode_average(
    by_mode: Dict[str, Any],
    *,
    empty_default: float | None,
) -> float | None:
    """Equal-weight mean over EVAL_MODES (section / body / table / image)."""
    vals = [by_mode[m] for m in EVAL_MODES if isinstance(by_mode.get(m), (int, float))]
    if vals:
        return round(sum(float(v) for v in vals) / len(vals), 6)
    return empty_default


def partition_mode_scores(
    by_mode: Dict[str, Any] | None,
    *,
    detail: Dict[str, Any] | None = None,
) -> tuple[Dict[str, float | None], Dict[str, float | None]]:
    """Split flat/report modes into primary EVAL_MODES + DETAIL_MODES."""
    flat = dict(by_mode or {})
    extra = dict(detail or {})
    for mode in DETAIL_MODES:
        if mode in flat and mode not in extra:
            extra[mode] = flat[mode]
    primary = {mode: flat.get(mode) for mode in EVAL_MODES}
    detail_out = {mode: extra.get(mode) for mode in DETAIL_MODES}
    return primary, detail_out


def _require_init_flat(init_flat: Dict[str, Any] | None) -> Dict[str, Any]:
    """FormatBench accuracy is defined only on init≠gold keys; empty init is invalid."""
    if not init_flat:
        raise ValueError(
            "init_flat (source / input flatten) is required to split need_change; "
            "refusing to treat all gold keys as need (that yields a false non-zero "
            "baseline when pred≡input). Pass source.flat.json / --init input.docx."
        )
    return init_flat


def split_need_no_change(
    gold: Dict[str, Any],
    init_flat: Dict[str, Any],
    *,
    tol: float = 0.15,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Split golden keys: missing init values count as unset → need_change."""
    init_flat = _require_init_flat(init_flat)
    init_on_gold = project_on_gold(init_flat, gold)
    need_change: Dict[str, Any] = {}
    no_change: Dict[str, Any] = {}
    for k, gv in gold.items():
        iv = init_on_gold[k]
        if values_equal(iv, gv, tol=tol):
            no_change[k] = gv
        else:
            need_change[k] = gv
    return need_change, no_change


def score_flats(
    *,
    pred: Dict[str, Any],
    gold: Dict[str, Any],
    init_flat: Dict[str, Any] | None = None,
    tol: float = 0.15,
) -> Dict[str, Any]:
    """Score pred vs gold on need_change; key set is always gold.

    Init/pred are projected onto gold keys (absent → None). By construction:
    ``pred is init`` ⇒ accuracy 0 when need_change non-empty;
    ``pred is gold`` ⇒ accuracy 1.
    """
    if not gold:
        raise ValueError("golden.flat is empty; cannot score need_change")
    init_flat = _require_init_flat(init_flat)
    init_on_gold = project_on_gold(init_flat, gold)
    pred_on_gold = project_on_gold(pred, gold)
    need_change, no_change = split_need_no_change(gold, init_flat, tol=tol)

    hit = sum(1 for k, gv in need_change.items() if values_equal(pred_on_gold[k], gv, tol=tol))

    # Hallucination: missing pred key ⇒ treat as unchanged (sparse officecli readback).
    # Need hits: missing pred key ⇒ None ≠ gold (must explicitly match golden).
    halluc = 0
    for k in no_change:
        iv = init_on_gold[k]
        pv = pred[k] if k in pred else iv
        if not values_equal(pv, iv, tol=tol):
            halluc += 1

    by_layer: Dict[str, Dict[str, Any]] = {}
    for prefix in _LAYER_PREFIX.values():
        need = {k: v for k, v in need_change.items() if k.startswith(prefix + ".")}
        stay = {k: v for k, v in no_change.items() if k.startswith(prefix + ".")}
        h = sum(1 for k, gv in need.items() if values_equal(pred_on_gold[k], gv, tol=tol))
        w = 0
        for k in stay:
            iv = init_on_gold[k]
            pv = pred[k] if k in pred else iv
            if not values_equal(pv, iv, tol=tol):
                w += 1
        by_layer[prefix] = {
            "accuracy": (h / len(need)) if need else None,
            "hallucination_rate": (w / len(stay)) if stay else 0.0,
            "need_change": len(need),
            "no_change": len(stay),
        }

    accuracy_by_mode: Dict[str, float | None] = {}
    hallucination_by_mode: Dict[str, float | None] = {}
    for mode in REPORT_MODES:
        need = {k: v for k, v in need_change.items() if key_in_mode(k, mode)}
        stay = {k: v for k, v in no_change.items() if key_in_mode(k, mode)}
        h = sum(1 for k, gv in need.items() if values_equal(pred_on_gold[k], gv, tol=tol))
        w = 0
        for k in stay:
            iv = init_on_gold[k]
            pv = pred[k] if k in pred else iv
            if not values_equal(pv, iv, tol=tol):
                w += 1
        accuracy_by_mode[mode] = (h / len(need)) if need else None
        hallucination_by_mode[mode] = (w / len(stay)) if stay else None

    wrong_need = [k for k, gv in need_change.items() if not values_equal(pred_on_gold[k], gv, tol=tol)]
    wrong_stay = []
    for k in no_change:
        iv = init_on_gold[k]
        pv = pred[k] if k in pred else iv
        if not values_equal(pv, iv, tol=tol):
            wrong_stay.append(k)

    # Primary scores = object-level equal-weight avg (not key micro).
    return {
        "accuracy": object_mode_average(
            accuracy_by_mode, empty_default=1.0 if not need_change else None
        ),
        "hallucination_rate": object_mode_average(
            hallucination_by_mode, empty_default=0.0 if not no_change else None
        ),
        "need_change": len(need_change),
        "need_change_hit": hit,
        "no_change": len(no_change),
        "hallucination_count": halluc,
        "by_layer": by_layer,
        "accuracy_by_mode": accuracy_by_mode,
        "hallucination_by_mode": hallucination_by_mode,
        "wrong_keys": {"need": sorted(wrong_need), "no_change": sorted(wrong_stay)},
    }


def snapshot_document(doc_path: Path) -> Tuple[Dict[str, Any], List[Any]]:
    """One officecli open: eval inventory flatten + integrity stream from the same queries."""
    from LongDocFmt.evaluation.integrity import content_stream_from_query_rows
    from LongDocFmt.workflow.officecli_lock import officecli_exclusive

    with officecli_exclusive():
        open_docx(doc_path)
        try:
            bundle = inventory_bundle(doc_path, profile="eval")
        finally:
            close_docx(doc_path)
    stream = content_stream_from_query_rows(bundle["para_rows"], bundle["picture_rows"])
    return flatten_doc(bundle["elements"]), stream


def snapshot(doc_path: Path) -> Dict[str, Any]:
    """Format flatten only (golden / CLI). Same eval inventory as ``snapshot_document``."""
    flat, _stream = snapshot_document(doc_path)
    return flat


def evaluate(
    *,
    golden: Path,
    output: Path,
    init: Path,
    tol: float = 0.15,
) -> Dict[str, Any]:
    gold_path = Path(golden).resolve()
    out_path = Path(output).resolve()
    init_path = Path(init).resolve()
    if not init_path.is_file():
        raise FileNotFoundError(f"init docx required for need_change split: {init_path}")
    gold = snapshot(golden)
    if same_document(init_path, gold_path):
        init_flat = gold
    else:
        init_flat = snapshot(init)

    # Reuse snapshots when output is a byte-identical copy of golden/input
    # (avoids officecli re-read drift breaking 0%/100% baselines).
    if same_document(out_path, gold_path):
        pred = gold
    elif same_document(out_path, init_path):
        pred = init_flat
    else:
        pred = snapshot(output)
    return score_flats(pred=pred, gold=gold, init_flat=init_flat, tol=tol)


def main(argv: List[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="LongDocFmt accuracy / hallucination eval.")
    p.add_argument("--golden", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--init", required=True, help="input/source docx (required for need_change)")
    p.add_argument("--save", default="")
    p.add_argument("--tol", type=float, default=0.15)
    args = p.parse_args(argv)
    result = evaluate(
        golden=Path(args.golden),
        output=Path(args.output),
        init=Path(args.init),
        tol=args.tol,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
