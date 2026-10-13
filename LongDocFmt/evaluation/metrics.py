"""Accuracy / hallucination reports (LongDocFmtEval officecli whitelist)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from LongDocFmt.workflow.eval_acc import (
    DETAIL_MODES,
    EVAL_MODES,
    object_mode_average,
    partition_mode_scores,
    score_flats,
)

_KEEP_METRIC_KEYS = (
    "failed",
    "fail_reason",
    "integrity_warning",
    "integrity_diff_count",
    "output_docx",
)


def normalize_metrics_block(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Force object-mode scores; keep only four primary modes in ``*_by_mode``.

    Fine splits (``run`` / ``table_paragraph`` / ``table_cell``) go under
    ``accuracy_by_mode_detail`` / ``hallucination_by_mode_detail``.
    """
    raw = dict(metrics or {})
    acc_primary, acc_detail = partition_mode_scores(
        raw.get("accuracy_by_mode"),
        detail=raw.get("accuracy_by_mode_detail"),
    )
    hall_primary, hall_detail = partition_mode_scores(
        raw.get("hallucination_by_mode"),
        detail=raw.get("hallucination_by_mode_detail"),
    )
    failed = bool(raw.get("failed"))
    acc = object_mode_average(acc_primary, empty_default=0.0 if failed else None)
    hall = object_mode_average(hall_primary, empty_default=0.0 if failed else None)
    out: dict[str, Any] = {
        "accuracy": acc if acc is not None else raw.get("accuracy"),
        "hallucination_rate": hall if hall is not None else raw.get("hallucination_rate"),
        "accuracy_by_mode": acc_primary,
        "hallucination_by_mode": hall_primary,
        "accuracy_by_mode_detail": acc_detail,
        "hallucination_by_mode_detail": hall_detail,
        "by_layer": raw.get("by_layer") or {},
    }
    for key in _KEEP_METRIC_KEYS:
        if key in raw:
            out[key] = raw[key]
    return out


def normalize_accuracy_report(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize a full ``accuracy.json`` / SampleMetrics.to_dict() payload."""
    out = dict(report)
    out["metrics"] = normalize_metrics_block(out.get("metrics") or {})
    return out


def slim_sample_summary_row(report: dict[str, Any]) -> dict[str, Any]:
    """Compact per-sample row for ``summary.json`` (ablation + main experiment)."""
    report = normalize_accuracy_report(report)
    metrics = report.get("metrics") or {}
    return {
        "sample_dir": report.get("sample_dir"),
        "integrity_status": report.get("integrity_status"),
        "accuracy": metrics.get("accuracy"),
        "failed": metrics.get("failed"),
        "fail_reason": metrics.get("fail_reason"),
        "hallucination_rate": metrics.get("hallucination_rate"),
    }


def metrics_from_scored(scored: dict[str, Any]) -> dict[str, Any]:
    """Sample-level metric dict written to ``eval/accuracy.json``.

    ``accuracy`` / ``hallucination_rate`` are the equal-weight means over the
    four object modes in ``accuracy_by_mode``. Fine splits live in
    ``*_by_mode_detail`` (``run`` / ``table_paragraph`` / ``table_cell``).
    """
    return normalize_metrics_block(
        {
            "accuracy": scored.get("accuracy"),
            "hallucination_rate": scored.get("hallucination_rate"),
            "accuracy_by_mode": scored.get("accuracy_by_mode") or {},
            "hallucination_by_mode": scored.get("hallucination_by_mode") or {},
            "by_layer": scored.get("by_layer") or {},
        }
    )


@dataclass
class SampleMetrics:
    sample_dir: str
    integrity_status: str
    counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    wrong_keys: dict[str, list[str]] = field(default_factory=dict)
    evaluated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_dir": self.sample_dir,
            "integrity_status": self.integrity_status,
            "counts": self.counts,
            "metrics": self.metrics,
            "wrong_keys": self.wrong_keys,
            "evaluated_at": self.evaluated_at,
        }


def metrics_from_flats(
    *,
    pred: dict[str, Any],
    gold: dict[str, Any],
    init_flat: dict[str, Any],
    sample_dir: Path,
    integrity_status: str = "ok",
    tol: float = 0.15,
) -> SampleMetrics:
    scored = score_flats(pred=pred, gold=gold, init_flat=init_flat, tol=tol)
    metrics = metrics_from_scored(scored)
    return SampleMetrics(
        sample_dir=str(Path(sample_dir).resolve()),
        integrity_status=integrity_status,
        counts={
            "need_check": int(scored.get("need_change") or 0),
            "no_change_check": int(scored.get("no_change") or 0),
            "wrong_on_need": len((scored.get("wrong_keys") or {}).get("need") or []),
            "error_touch": int(scored.get("hallucination_count") or 0),
        },
        metrics=metrics,
        wrong_keys=dict(scored.get("wrong_keys") or {}),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_metrics_report(report: SampleMetrics, path: Path) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def failed_sample_metrics(
    *,
    sample_dir: Path | str,
    reason: str,
) -> SampleMetrics:
    """Score a sample that never produced a usable output as accuracy 0 (not null)."""
    metrics = {
        "accuracy": 0.0,
        "hallucination_rate": 0.0,
        "accuracy_by_mode": {mode: 0.0 for mode in EVAL_MODES},
        "hallucination_by_mode": {mode: 0.0 for mode in EVAL_MODES},
        "accuracy_by_mode_detail": {mode: 0.0 for mode in DETAIL_MODES},
        "hallucination_by_mode_detail": {mode: 0.0 for mode in DETAIL_MODES},
        "by_layer": {},
        "failed": True,
        "fail_reason": str(reason or "no_output"),
    }
    return SampleMetrics(
        sample_dir=str(Path(sample_dir).resolve()),
        integrity_status="failed",
        counts={
            "need_check": 0,
            "no_change_check": 0,
            "wrong_on_need": 0,
            "error_touch": 0,
        },
        metrics=metrics,
        wrong_keys={"need": [], "no_change": []},
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def average_non_null(values: list[float | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 6)


def aggregate_accuracy_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    metrics_list = [normalize_metrics_block(r.get("metrics") or {}) for r in reports]
    accuracy_by_mode: dict[str, float | None] = {}
    hallucination_by_mode: dict[str, float | None] = {}
    accuracy_by_mode_detail: dict[str, float | None] = {}
    hallucination_by_mode_detail: dict[str, float | None] = {}
    for mode in EVAL_MODES:
        accuracy_by_mode[mode] = average_non_null(
            [(m.get("accuracy_by_mode") or {}).get(mode) for m in metrics_list]
        )
        hallucination_by_mode[mode] = average_non_null(
            [(m.get("hallucination_by_mode") or {}).get(mode) for m in metrics_list]
        )
    for mode in DETAIL_MODES:
        accuracy_by_mode_detail[mode] = average_non_null(
            [(m.get("accuracy_by_mode_detail") or {}).get(mode) for m in metrics_list]
        )
        hallucination_by_mode_detail[mode] = average_non_null(
            [(m.get("hallucination_by_mode_detail") or {}).get(mode) for m in metrics_list]
        )
    acc = average_non_null([m.get("accuracy") for m in metrics_list])
    if acc is None:
        acc = average_non_null([accuracy_by_mode.get(m) for m in EVAL_MODES])
    hall = average_non_null([m.get("hallucination_rate") for m in metrics_list])
    if hall is None:
        hall = average_non_null([hallucination_by_mode.get(m) for m in EVAL_MODES])
    return {
        "n_samples": len(reports),
        "n_ok": sum(1 for r in reports if r.get("integrity_status") == "ok"),
        "n_tampered": sum(1 for r in reports if r.get("integrity_status") == "tampered"),
        "n_failed": sum(
            1
            for r in reports
            if r.get("integrity_status") == "failed" or (r.get("metrics") or {}).get("failed")
        ),
        "accuracy": acc,
        "hallucination_rate": hall,
        "accuracy_by_mode": accuracy_by_mode,
        "hallucination_by_mode": hallucination_by_mode,
        "accuracy_by_mode_detail": accuracy_by_mode_detail,
        "hallucination_by_mode_detail": hallucination_by_mode_detail,
    }
