"""score_flats must match LongDocFmt.workflow.eval_acc."""
from __future__ import annotations

from pathlib import Path

import pytest

from LongDocFmt.evaluation.longdocform_eval.src.flatten import _is_table_cell_element
from LongDocFmt.evaluation.longdocform_eval.src.score import (
    metrics_from_scored,
    sample_accuracy_report,
    score_flats,
    values_equal,
)
from LongDocFmt.workflow.eval_acc import score_flats as windows_score_flats
from LongDocFmt.workflow.eval_acc import values_equal as windows_values_equal


def test_values_equal_matches_windows() -> None:
    assert values_equal("justify", "both") is False
    assert windows_values_equal("justify", "both") is False
    assert values_equal("1.0cm", "1.05cm") is True
    assert values_equal(None, "none") is True
    assert values_equal(True, "true") is True


def test_score_flats_matches_windows_need_and_hallucination() -> None:
    gold = {"paragraphs.1.align": "center", "paragraphs.1.size": "12pt"}
    init = {"paragraphs.1.align": "left", "paragraphs.1.size": "12pt"}
    pred = {"paragraphs.1.align": "center"}
    a = score_flats(pred=pred, gold=gold, init_flat=init)
    b = windows_score_flats(pred=pred, gold=gold, init_flat=init)
    # Only paragraph mode has need_change → accuracy = that mode (=1.0).
    assert a["accuracy"] == b["accuracy"] == 1.0
    assert a["hallucination_rate"] == b["hallucination_rate"] == 0.0
    assert a["need_change"] == b["need_change"] == 1
    assert a["no_change"] == b["no_change"] == 1
    assert "accuracy_by_attribution" not in a
    assert "accuracy_by_attribution" not in b


def test_sample_accuracy_report_matches_evaluation_shape() -> None:
    gold = {
        "paragraphs.1.align": "center",
        "section.1.marginTop": "2.54cm",
        "tables.1.cells.r1_c1.fill": "#EEE",
        "images.1.width": "5cm",
        "runs.1.bold": True,
    }
    init = {
        "paragraphs.1.align": "left",
        "section.1.marginTop": "2.54cm",
        "tables.1.cells.r1_c1.fill": "none",
        "images.1.width": "4cm",
        "runs.1.bold": False,
    }
    pred = {
        "paragraphs.1.align": "center",
        "section.1.marginTop": "2.54cm",
        "tables.1.cells.r1_c1.fill": "#EEE",
        "images.1.width": "5cm",
        "runs.1.bold": True,
    }
    scored = score_flats(pred=pred, gold=gold, init_flat=init)
    report = sample_accuracy_report(scored, sample_dir="D:/LongDocFmtEval/1")
    metrics = report["metrics"]
    assert set(report) >= {"sample_dir", "integrity_status", "counts", "metrics", "wrong_keys", "evaluated_at"}
    assert metrics["accuracy"] == 1.0
    assert "accuracy_by_attribution" not in metrics
    assert "accuracy_by_mode_avg" not in metrics
    assert set(metrics["accuracy_by_mode"]) == {"section", "paragraph", "table", "image"}
    assert set(metrics["accuracy_by_mode_detail"]) == {"run", "table_paragraph", "table_cell"}
    assert report["integrity_status"] == "not_checked"


def test_missing_pred_key_misses_need_not_hallucination() -> None:
    gold = {"paragraphs.1.align": "center", "paragraphs.1.size": "12pt"}
    init = {"paragraphs.1.align": "left", "paragraphs.1.size": "12pt"}
    pred: dict = {}
    scored = score_flats(pred=pred, gold=gold, init_flat=init)
    assert scored["accuracy"] == 0.0
    assert scored["hallucination_rate"] == 0.0


def test_empty_init_raises() -> None:
    with pytest.raises(ValueError, match="init_flat"):
        score_flats(pred={}, gold={"a": 1}, init_flat={})


def test_table_mode_splits_cell_fonts_from_chrome() -> None:
    gold = {
        "paragraphs.1.align": "center",
        "paragraph_cells.1:1:1.font.ea": "宋体",
        "tables.1.cells.r1_c1.fill": "#EEE",
    }
    init = {
        "paragraphs.1.align": "left",
        "paragraph_cells.1:1:1.font.ea": "黑体",
        "tables.1.cells.r1_c1.fill": "none",
    }
    pred = {
        "paragraphs.1.align": "center",
        "paragraph_cells.1:1:1.font.ea": "宋体",
        "tables.1.cells.r1_c1.fill": "none",
    }
    scored = score_flats(pred=pred, gold=gold, init_flat=init)
    windows = windows_score_flats(pred=pred, gold=gold, init_flat=init)
    assert scored["accuracy_by_mode"] == windows["accuracy_by_mode"]
    assert scored["accuracy_by_mode"]["paragraph"] == 1.0
    assert scored["accuracy_by_mode"]["table_paragraph"] == 1.0
    assert scored["accuracy_by_mode"]["table_cell"] == 0.0
    assert scored["accuracy_by_mode"]["table"] == 0.5
    # accuracy = mean(paragraph=1.0, table=0.5)
    assert scored["accuracy"] == windows["accuracy"] == 0.75


def test_paragraph_mode_folds_runs_into_body() -> None:
    gold = {
        "paragraphs.1.align": "center",
        "runs.1.bold": True,
    }
    init = {
        "paragraphs.1.align": "left",
        "runs.1.bold": False,
    }
    pred = {
        "paragraphs.1.align": "center",
        "runs.1.bold": False,
    }
    scored = score_flats(pred=pred, gold=gold, init_flat=init)
    windows = windows_score_flats(pred=pred, gold=gold, init_flat=init)
    assert scored["accuracy_by_mode"] == windows["accuracy_by_mode"]
    # Body bucket = paragraphs + runs → 1/2; run alone still 0 for diagnostics.
    assert scored["accuracy_by_mode"]["paragraph"] == 0.5
    assert scored["accuracy_by_mode"]["run"] == 0.0
    metrics = metrics_from_scored(scored)
    assert metrics["accuracy"] == 0.5
    assert metrics["accuracy_by_mode"]["paragraph"] == 0.5
    assert "run" not in metrics["accuracy_by_mode"]
    assert metrics["accuracy_by_mode_detail"]["run"] == 0.0
    assert "accuracy_by_mode_avg" not in metrics
    assert "accuracy_by_attribution" not in metrics


def test_only_real_tc_nodes_count_as_cells() -> None:
    assert _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]", "cell")
    assert not _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]/p[1]/r[1]", "run")
    assert not _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]/p[@paraId=x]", "paragraph")


def test_canon_skill_keeps_officecli_orch_separate() -> None:
    from LongDocFmt.evaluation.longdocform_eval.src.paths import canon_skill, result_dir

    assert canon_skill("officecli-orch") == "officecli-orch"
    assert canon_skill("officecli_orch") == "officecli-orch"
    assert canon_skill("officecli") == "officecli"
    root = Path("D:/exp")
    orch = result_dir(experiment_root=root, agent="dp-harness", skill="officecli-orch", model="m", sample_id="5")
    docx = result_dir(experiment_root=root, agent="dp-harness", skill="officecli", model="m", sample_id="5")
    assert orch.as_posix().endswith("dp-harness/officecli-orch/m/5")
    assert docx.as_posix().endswith("dp-harness/officecli/m/5")
