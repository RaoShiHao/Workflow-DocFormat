"""Accuracy / hallucination reports (officecli whitelist)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from LongDocFormatter.workflow.eval_acc import EVAL_MODES, score_flats


def metrics_from_scored(scored: dict[str, Any]) -> dict[str, Any]:
    """Sample-level metric dict written to ``eval/accuracy.json``.

    ``accuracy_by_attribution`` is micro over all need_change keys (formerly
    ``accuracy``). ``accuracy`` is kept as an alias. ``accuracy_by_mode_avg`` is
    the unweighted mean of the five mode scores.
    """
    acc = scored.get("accuracy_by_attribution", scored.get("accuracy"))
    hall = scored.get("hallucination_by_attribution", scored.get("hallucination_rate"))
    acc_modes = scored.get("accuracy_by_mode") or {}
    hall_modes = scored.get("hallucination_by_mode") or {}
    acc_vals = [v for v in acc_modes.values() if isinstance(v, (int, float))]
    hall_vals = [v for v in hall_modes.values() if isinstance(v, (int, float))]
    return {
        "accuracy_by_attribution": acc,
        "hallucination_by_attribution": hall,
        "accuracy": acc,
        "hallucination_rate": hall,
        "accuracy_by_mode": acc_modes,
        "hallucination_by_mode": hall_modes,
        "accuracy_by_mode_avg": round(sum(acc_vals) / len(acc_vals), 6) if acc_vals else None,
        "hallucination_by_mode_avg": round(sum(hall_vals) / len(hall_vals), 6) if hall_vals else None,
        "by_layer": scored.get("by_layer") or {},
    }


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
    modes = {mode: 0.0 for mode in EVAL_MODES}
    metrics = {
        "accuracy_by_attribution": 0.0,
        "hallucination_by_attribution": 0.0,
        "accuracy": 0.0,
        "hallucination_rate": 0.0,
        "accuracy_by_mode": dict(modes),
        "hallucination_by_mode": dict(modes),
        "accuracy_by_mode_avg": 0.0,
        "hallucination_by_mode_avg": 0.0,
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
    metrics_list = [r.get("metrics") or {} for r in reports]
    accuracy_by_mode: dict[str, float | None] = {}
    hallucination_by_mode: dict[str, float | None] = {}
    for mode in EVAL_MODES:
        accuracy_by_mode[mode] = average_non_null(
            [(m.get("accuracy_by_mode") or {}).get(mode) for m in metrics_list]
        )
        hallucination_by_mode[mode] = average_non_null(
            [(m.get("hallucination_by_mode") or {}).get(mode) for m in metrics_list]
        )
    acc = average_non_null(
        [m.get("accuracy_by_attribution", m.get("accuracy")) for m in metrics_list]
    )
    hall = average_non_null(
        [m.get("hallucination_by_attribution", m.get("hallucination_rate")) for m in metrics_list]
    )
    return {
        "n_samples": len(reports),
        "n_ok": sum(1 for r in reports if r.get("integrity_status") == "ok"),
        "n_tampered": sum(1 for r in reports if r.get("integrity_status") == "tampered"),
        "n_failed": sum(
            1
            for r in reports
            if r.get("integrity_status") == "failed" or (r.get("metrics") or {}).get("failed")
        ),
        "accuracy_by_attribution": acc,
        "hallucination_by_attribution": hall,
        "accuracy": acc,
        "hallucination_rate": hall,
        "accuracy_by_mode": accuracy_by_mode,
        "hallucination_by_mode": hallucination_by_mode,
        "accuracy_by_mode_avg": average_non_null(list(accuracy_by_mode.values())),
        "hallucination_by_mode_avg": average_non_null(list(hallucination_by_mode.values())),
    }
