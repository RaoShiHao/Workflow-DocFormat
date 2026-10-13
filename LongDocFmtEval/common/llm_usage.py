"""Accumulate LLM token usage across LongDocFmtEval step scripts."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

_records: ContextVar[list[dict[str, Any]] | None] = ContextVar("adb_llm_usage_records", default=None)
_step: ContextVar[str] = ContextVar("adb_llm_usage_step", default="")


def _as_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def estimate_tokens(text: str) -> int:
    """Rough token estimate when the gateway omits usage (CJK-friendly ~chars/2)."""
    n = len(text or "")
    if n <= 0:
        return 0
    return max(1, (n + 1) // 2)


def normalize_usage_obj(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if isinstance(usage, dict):
        pt = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        ct = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        tt = _as_int(usage.get("total_tokens")) or (pt + ct)
        return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    pt = _as_int(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None))
    ct = _as_int(getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None))
    tt = _as_int(getattr(usage, "total_tokens", None)) or (pt + ct)
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


def record_llm_call(
    *,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    source: str = "api",
    stream: bool = False,
    label: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one call into the active usage scope (no-op if none)."""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": _step.get() or "",
        "label": label or "",
        "model": model or "",
        "stream": bool(stream),
        "source": source,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or (int(prompt_tokens or 0) + int(completion_tokens or 0))),
    }
    if meta:
        rec["meta"] = meta
    bucket = _records.get()
    if bucket is not None:
        bucket.append(rec)
    return rec


def summarize_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    pt = ct = tt = 0
    by_step: dict[str, dict[str, int]] = {}
    for r in records:
        p = int(r.get("prompt_tokens") or 0)
        c = int(r.get("completion_tokens") or 0)
        t = int(r.get("total_tokens") or (p + c))
        pt += p
        ct += c
        tt += t
        sk = str(r.get("step") or "unknown")
        slot = by_step.setdefault(sk, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0})
        slot["prompt_tokens"] += p
        slot["completion_tokens"] += c
        slot["total_tokens"] += t
        slot["calls"] += 1
    return {
        "calls": len(records),
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "by_step": by_step,
    }


@contextmanager
def usage_scope(step: str) -> Iterator[list[dict[str, Any]]]:
    """Collect LLM usage for one step (or sub-run). Nested scopes replace the active list."""
    bucket: list[dict[str, Any]] = []
    token_r = _records.set(bucket)
    token_s = _step.set(str(step or ""))
    try:
        # NOTE: must yield ``bucket`` itself. Do NOT use ``_records.get() or []`` —
        # an empty list is falsy, so ``or []`` would yield a *new* list and drop all
        # ``record_llm_call`` appends (which write to the ContextVar list).
        yield bucket
    finally:
        _records.reset(token_r)
        _step.reset(token_s)


def get_active_records() -> list[dict[str, Any]]:
    return list(_records.get() or [])


def write_usage_report(
    path: Path,
    records: list[dict[str, Any]] | None = None,
    *,
    step: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write llm_usage.json with per-call rows + totals."""
    rows = list(records if records is not None else get_active_records())
    payload: dict[str, Any] = {
        "step": step or (_step.get() or ""),
        "summary": summarize_usage(rows),
        "calls": rows,
    }
    if extra:
        payload["extra"] = extra
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def step_usage_dir(step_dir: Path) -> Path:
    """Per-step usage folder: ``{step_dir}/usage/``."""
    d = Path(step_dir) / "usage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_step_usage_report(
    step_dir: Path,
    records: list[dict[str, Any]] | None = None,
    *,
    step: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write usage under ``{step_dir}/usage/`` (llm_usage.json + calls.jsonl).

    Also mirrors ``{step_dir}/llm_usage.json`` for older aggregators/tools.
    """
    rows = list(records if records is not None else get_active_records())
    udir = step_usage_dir(step_dir)
    # Full rewrite of this run's call log (jsonl = one call per line)
    jsonl_path = udir / "calls.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = write_usage_report(udir / "llm_usage.json", rows, step=step, extra=extra)
    # Legacy mirror at step root
    try:
        write_usage_report(Path(step_dir) / "llm_usage.json", rows, step=step, extra=extra)
    except Exception:  # noqa: BLE001
        pass
    return report


def collect_topic_usage_paths(topic: Path) -> list[Path]:
    """Find step-level llm_usage.json files (prefer usage/, skip topic totals)."""
    topic = Path(topic)
    found: list[Path] = []
    seen: set[str] = set()
    topic_usage = (topic / "usage").resolve()
    # Prefer canonical per-step usage dirs
    for p in sorted(topic.rglob("usage/llm_usage.json")):
        try:
            # Skip topic-level usage aggregate folder
            if p.parent.resolve() == topic_usage:
                continue
        except OSError:
            if p.parent == topic / "usage":
                continue
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        found.append(p)
    # Legacy: step-root llm_usage.json (exclude topic root and nested under usage/)
    for p in sorted(topic.rglob("llm_usage.json")):
        if p.parent == topic:
            continue
        if p.parent.name == "usage":
            continue
        if "llm_usage_total" in p.name:
            continue
        # Skip mirror if usage/ copy already collected for same step dir
        usage_sibling = p.parent / "usage" / "llm_usage.json"
        if usage_sibling.is_file():
            continue
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        found.append(p)
    return found


def merge_usage_reports(paths: list[Path]) -> dict[str, Any]:
    """Merge multiple llm_usage.json files into one topic-level summary."""
    all_calls: list[dict[str, Any]] = []
    sources: list[str] = []
    for p in paths:
        pp = Path(p)
        if not pp.is_file():
            continue
        try:
            data = json.loads(pp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        sources.append(str(pp).replace("\\", "/"))
        calls = data.get("calls") if isinstance(data, dict) else None
        if isinstance(calls, list):
            all_calls.extend([c for c in calls if isinstance(c, dict)])
    return {
        "sources": sources,
        "summary": summarize_usage(all_calls),
        "calls": all_calls,
    }


def print_usage_summary(summary: dict[str, Any], *, prefix: str = "[tokens]") -> None:
    print(
        f"{prefix} calls={summary.get('calls', 0)} "
        f"in={summary.get('prompt_tokens', 0)} "
        f"out={summary.get('completion_tokens', 0)} "
        f"total={summary.get('total_tokens', 0)}"
    )
