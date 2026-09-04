"""Unit tests for LLM call budget (max_llm_step)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from LongDocFormatter.workflow.contracts import Assignment
from LongDocFormatter.workflow.llm_trace import CallLogger, LlmBudgetExceeded, TracingModel


class _FakeLM:
    model = "fake"

    def __init__(self) -> None:
        self.n = 0

    def chat_json(self, *, system, user, **kwargs):
        self.n += 1
        if "FAIL" in user:
            raise RuntimeError("boom")
        return {
            "content": f'{{"ok":{self.n}}}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def test_assignment_is_empty():
    assert Assignment().is_empty()
    assert not Assignment(by_layer={"section": {"1": "SecBody"}}).is_empty()


def test_budget_stops_after_limit(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=False, max_llm_step=2, count_cache_toward_budget=True)
    lm = TracingModel(_FakeLM(), logger)
    lm.chat_json(system="s", user="u1")
    lm.chat_json(system="s", user="u2")
    with pytest.raises(LlmBudgetExceeded):
        lm.chat_json(system="s", user="u3")
    assert logger._budget_used == 2
    assert logger.is_budget_exhausted


def test_failed_call_does_not_consume_budget(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=False, max_llm_step=1, count_cache_toward_budget=True)
    lm = TracingModel(_FakeLM(), logger)
    with pytest.raises(RuntimeError):
        lm.chat_json(system="s", user="FAIL")
    assert logger._budget_used == 0
    lm.chat_json(system="s", user="ok")
    assert logger._budget_used == 1
    with pytest.raises(LlmBudgetExceeded):
        lm.chat_json(system="s", user="again")


def test_cache_hit_counts_when_enabled(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=True, max_llm_step=2, count_cache_toward_budget=True)
    inner = _FakeLM()
    lm = TracingModel(inner, logger)
    lm.chat_json(system="s", user="same")
    assert inner.n == 1
    assert logger._budget_used == 1
    lm.chat_json(system="s", user="same")
    assert inner.n == 1
    assert logger._budget_used == 2
    with pytest.raises(LlmBudgetExceeded):
        lm.chat_json(system="s", user="same")


def test_cache_hit_optional_not_counted(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=True, max_llm_step=1, count_cache_toward_budget=False)
    inner = _FakeLM()
    lm = TracingModel(inner, logger)
    lm.chat_json(system="s", user="same")
    assert logger._budget_used == 1
    lm.chat_json(system="s", user="same")
    assert logger._budget_used == 1
    with pytest.raises(LlmBudgetExceeded):
        lm.chat_json(system="s", user="other")


def test_note_parse_failure_is_recorded(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=False, max_llm_step=10)
    logger.set_step("05_target_element_loc")
    logger.note_parse_failure(layer="table", message="table: truncated JSON", raw="{")
    usage = json.loads((tmp_path / "llm" / "usage.json").read_text(encoding="utf-8"))
    assert usage["parse_failed_calls"] == 1
    assert usage["by_step"]["05_target_element_loc"]["parse_failed_calls"] == 1
    fails = json.loads((tmp_path / "llm" / "failures.json").read_text(encoding="utf-8"))
    assert fails[0]["layer"] == "table"
    assert fails[0]["kind"] == "json_parse"
