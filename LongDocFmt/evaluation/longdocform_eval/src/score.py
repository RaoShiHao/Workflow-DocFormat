"""Accuracy / hallucination vs golden — same rules as ``workflow.eval_acc``.

accuracy           = equal-weight mean of object modes on need_change
hallucination_rate = same four-mode average on must-not-change keys

Key universe is always **golden.flat**. Missing pred keys:
  need_change → None ≠ gold (miss)
  no_change   → treat as unchanged (not a hallucination)
"""

from __future__ import annotations

import re
from typing import Any

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?")
# Object-level equal-weight avg: section / body (paragraph+run) / table / image.
# Fine splits (table_* / run) stay report-only and do not enter mode_avg.
EVAL_MODES = ("section", "paragraph", "table", "image")
TABLE_SPLIT_MODES = ("table_paragraph", "table_cell")
BODY_SPLIT_MODES = ("paragraph", "run")
DETAIL_MODES = ("run", "table_paragraph", "table_cell")
REPORT_MODES = ("section", "paragraph", "table", "table_paragraph", "table_cell", "image", "run")
_PREFIX_TO_MODE = {
    "section": "section",
    "paragraphs": "paragraph",
    "paragraph_cells": "table_paragraph",
    "tables": "table_cell",
    "images": "image",
    "runs": "run",
}
_LAYER_PREFIX = (
    "section",
    "paragraphs",
    "paragraph_cells",
    "tables",
    "images",
    "runs",
)


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
    by_mode: dict[str, Any],
    *,
    empty_default: float | None,
) -> float | None:
    """Equal-weight mean over EVAL_MODES (section / body / table / image)."""
    vals = [by_mode[m] for m in EVAL_MODES if isinstance(by_mode.get(m), (int, float))]
    if vals:
        return round(sum(float(v) for v in vals) / len(vals), 6)
    return empty_default


def partition_mode_scores(
    by_mode: dict[str, Any] | None,
    *,
    detail: dict[str, Any] | None = None,
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Split flat/report modes into primary EVAL_MODES + DETAIL_MODES."""
    flat = dict(by_mode or {})
    extra = dict(detail or {})
    for mode in DETAIL_MODES:
        if mode in flat and mode not in extra:
            extra[mode] = flat[mode]
    primary = {mode: flat.get(mode) for mode in EVAL_MODES}
    detail_out = {mode: extra.get(mode) for mode in DETAIL_MODES}
    return primary, detail_out


def _require_init_flat(init_flat: dict[str, Any] | None) -> dict[str, Any]:
    """FormatBench accuracy is defined only on init≠gold keys; empty init is invalid."""
    if not init_flat:
        raise ValueError(
            "init_flat (source / input flatten) is required to split need_change; "
            "refusing to treat all gold keys as need (that yields a false non-zero "
            "baseline when pred≡input). Pass eval/source.flat.json."
        )
    return init_flat


def project_on_gold(flat: dict[str, Any] | None, gold: dict[str, Any]) -> dict[str, Any]:
    """Restrict ``flat`` to golden keys; unread/unset attributes become None."""
    flat = flat or {}
    return {k: (flat[k] if k in flat else None) for k in gold}


def split_need_no_change(
    gold: dict[str, Any],
    init_flat: dict[str, Any],
    *,
    tol: float = 0.15,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split golden keys: missing init values count as unset → need_change."""
    init_flat = _require_init_flat(init_flat)
    init_on_gold = project_on_gold(init_flat, gold)
    need_change: dict[str, Any] = {}
    no_change: dict[str, Any] = {}
    for k, gv in gold.items():
        iv = init_on_gold[k]
        if values_equal(iv, gv, tol=tol):
            no_change[k] = gv
        else:
            need_change[k] = gv
    return need_change, no_change


def score_flats(
    pred: dict[str, Any] | None = None,
    *,
    gold: dict[str, Any],
    init_flat: dict[str, Any] | None = None,
    tol: float = 0.15,
) -> dict[str, Any]:
    """Same as ``LongDocFmt.workflow.eval_acc.score_flats``.

    ``pred`` may be positional (Linux runner) or keyword-only (Windows).
    """
    if pred is None:
        raise TypeError("score_flats() missing required argument: 'pred'")
    if not gold:
        raise ValueError("golden.flat is empty; cannot score need_change")
    init_flat = _require_init_flat(init_flat)
    init_on_gold = project_on_gold(init_flat, gold)
    pred_on_gold = project_on_gold(pred, gold)
    need_change, no_change = split_need_no_change(gold, init_flat, tol=tol)

    hit = sum(1 for k, gv in need_change.items() if values_equal(pred_on_gold[k], gv, tol=tol))

    halluc = 0
    for k in no_change:
        iv = init_on_gold[k]
        pv = pred[k] if k in pred else iv
        if not values_equal(pv, iv, tol=tol):
            halluc += 1

    by_layer: dict[str, dict[str, Any]] = {}
    for prefix in _LAYER_PREFIX:
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

    accuracy_by_mode: dict[str, float | None] = {}
    hallucination_by_mode: dict[str, float | None] = {}
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
    wrong_stay: list[str] = []
    for k in no_change:
        iv = init_on_gold[k]
        pv = pred[k] if k in pred else iv
        if not values_equal(pv, iv, tol=tol):
            wrong_stay.append(k)

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


def metrics_from_scored(scored: dict[str, Any]) -> dict[str, Any]:
    """Same fields as ``LongDocFmt.evaluation.metrics.metrics_from_scored``."""
    acc_primary, acc_detail = partition_mode_scores(scored.get("accuracy_by_mode"))
    hall_primary, hall_detail = partition_mode_scores(scored.get("hallucination_by_mode"))
    need_n = int(scored.get("need_change") or 0)
    no_change_n = int(scored.get("no_change") or 0)
    acc = object_mode_average(acc_primary, empty_default=1.0 if need_n == 0 else None)
    if acc is None:
        acc = scored.get("accuracy")
    hall = object_mode_average(hall_primary, empty_default=0.0 if no_change_n == 0 else None)
    if hall is None:
        hall = scored.get("hallucination_rate")
    return {
        "accuracy": acc,
        "hallucination_rate": hall,
        "accuracy_by_mode": acc_primary,
        "hallucination_by_mode": hall_primary,
        "accuracy_by_mode_detail": acc_detail,
        "hallucination_by_mode_detail": hall_detail,
        "by_layer": scored.get("by_layer") or {},
    }


def sample_accuracy_report(
    scored: dict[str, Any],
    *,
    sample_dir: str,
    integrity_status: str = "not_checked",
) -> dict[str, Any]:
    """Same JSON shape as Windows ``eval/accuracy.json`` (SampleMetrics.to_dict)."""
    from datetime import datetime, timezone

    return {
        "sample_dir": str(sample_dir),
        "integrity_status": integrity_status,
        "counts": {
            "need_check": int(scored.get("need_change") or 0),
            "no_change_check": int(scored.get("no_change") or 0),
            "wrong_on_need": len((scored.get("wrong_keys") or {}).get("need") or []),
            "error_touch": int(scored.get("hallucination_count") or 0),
        },
        "metrics": metrics_from_scored(scored),
        "wrong_keys": dict(scored.get("wrong_keys") or {}),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def slim_scores(scored: dict[str, Any]) -> dict[str, Any]:
    """Metrics block used for model-level averages (same keys as evaluation)."""
    return metrics_from_scored(scored)


def _mean(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 6)


def _metrics_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    return row


def average_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    empty_primary = {mode: None for mode in EVAL_MODES}
    empty_detail = {mode: None for mode in DETAIL_MODES}
    if not rows:
        return {
            "accuracy": None,
            "hallucination_rate": None,
            "accuracy_by_mode": dict(empty_primary),
            "hallucination_by_mode": dict(empty_primary),
            "accuracy_by_mode_detail": dict(empty_detail),
            "hallucination_by_mode_detail": dict(empty_detail),
        }
    metrics_list = [_metrics_row(r) for r in rows]
    # Normalize each row so legacy flat by_mode (with run/table_*) is partitioned.
    normalized = []
    for m in metrics_list:
        acc_p, acc_d = partition_mode_scores(
            m.get("accuracy_by_mode"), detail=m.get("accuracy_by_mode_detail")
        )
        hall_p, hall_d = partition_mode_scores(
            m.get("hallucination_by_mode"), detail=m.get("hallucination_by_mode_detail")
        )
        normalized.append(
            {
                "accuracy": m.get("accuracy"),
                "hallucination_rate": m.get("hallucination_rate"),
                "accuracy_by_mode": acc_p,
                "hallucination_by_mode": hall_p,
                "accuracy_by_mode_detail": acc_d,
                "hallucination_by_mode_detail": hall_d,
            }
        )
    acc_by_mode = {
        mode: _mean([(m.get("accuracy_by_mode") or {}).get(mode) for m in normalized])
        for mode in EVAL_MODES
    }
    hall_by_mode = {
        mode: _mean([(m.get("hallucination_by_mode") or {}).get(mode) for m in normalized])
        for mode in EVAL_MODES
    }
    acc_detail = {
        mode: _mean([(m.get("accuracy_by_mode_detail") or {}).get(mode) for m in normalized])
        for mode in DETAIL_MODES
    }
    hall_detail = {
        mode: _mean([(m.get("hallucination_by_mode_detail") or {}).get(mode) for m in normalized])
        for mode in DETAIL_MODES
    }
    acc = _mean([m.get("accuracy") for m in normalized])
    if acc is None:
        acc = _mean([acc_by_mode.get(m) for m in EVAL_MODES])
    hall = _mean([m.get("hallucination_rate") for m in normalized])
    if hall is None:
        hall = _mean([hall_by_mode.get(m) for m in EVAL_MODES])
    return {
        "accuracy": acc,
        "hallucination_rate": hall,
        "accuracy_by_mode": acc_by_mode,
        "hallucination_by_mode": hall_by_mode,
        "accuracy_by_mode_detail": acc_detail,
        "hallucination_by_mode_detail": hall_detail,
    }
