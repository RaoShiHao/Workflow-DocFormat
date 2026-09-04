"""Persist every LLM call (prompt, raw content, tokens) and replay on resume.

Also enforces optional ``max_llm_step`` budget: only **successful** calls count
(failed API/errors do not). Cache hits count when ``count_cache_toward_budget``
is true (default), so resume stays fair vs a cold run.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from LongDocFormatter.workflow.json_util import write_json  # noqa: E402


class LlmBudgetExceeded(RuntimeError):
    """Raised when successful LLM call count would exceed ``max_llm_step``."""

    def __init__(self, *, used: int, limit: int, step: str = ""):
        self.used = int(used)
        self.limit = int(limit)
        self.step = str(step or "")
        super().__init__(
            f"LLM budget exceeded: used={self.used} limit={self.limit} step={self.step}"
        )


def _usage_of(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    usage = result.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_usage() -> dict[str, Any]:
    return {
        "calls": 0,
        "cached_calls": 0,
        "failed_calls": 0,
        "parse_failed_calls": 0,
        "budget_used": 0,
        "budget_limit": None,
        "count_cache_toward_budget": True,
        "budget_exhausted": False,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        # Provider prompt-cache hits (subset of prompt_tokens; still counted as input).
        "prompt_cached_tokens": 0,
        # Local by_hash replay totals (separate from provider cache).
        "cached_total_tokens": 0,
        "by_step": {},
    }


def logger_of(model: Any) -> "CallLogger | None":
    if isinstance(model, TracingModel):
        return model.logger
    return None


def budget_exhausted(model: Any) -> bool:
    log = logger_of(model)
    return bool(log and log.is_budget_exhausted)


class TracingModel:
    def __init__(self, inner: Any, logger: "CallLogger"):
        self.inner = inner
        self.logger = logger
        self.model = getattr(inner, "model", "")

    def chat_json(self, *, system: str, user: str, images: list | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.logger.call(
            self.inner,
            system=system,
            user=user,
            images=images,
            **kwargs,
        )


class CallLogger:
    def __init__(
        self,
        artifacts_dir: str | Path,
        *,
        reuse: bool = True,
        save_by_hash: bool = True,
        max_llm_step: int | None = None,
        count_cache_toward_budget: bool = True,
    ):
        self.root = Path(artifacts_dir)
        self.llm_dir = self.root / "llm"
        self.hash_dir = self.llm_dir / "by_hash"
        self.log_path = self.llm_dir / "log.jsonl"
        self.usage_path = self.llm_dir / "usage.json"
        self.reuse = bool(reuse)
        self.save_by_hash = bool(save_by_hash)
        self.max_llm_step = None if max_llm_step is None else int(max_llm_step)
        self.count_cache_toward_budget = bool(count_cache_toward_budget)
        self.step = "00"
        self._seq = 0
        self._lock = threading.Lock()
        self._budget_used = 0
        self._budget_reserved = 0
        self.llm_dir.mkdir(parents=True, exist_ok=True)
        self.hash_dir.mkdir(parents=True, exist_ok=True)
        self._usage = _empty_usage()
        self._usage["budget_limit"] = self.max_llm_step
        self._usage["count_cache_toward_budget"] = self.count_cache_toward_budget
        self._load_prior_state()

    @property
    def is_budget_exhausted(self) -> bool:
        if self.max_llm_step is None:
            return False
        with self._lock:
            return (self._budget_used + self._budget_reserved) >= self.max_llm_step

    def set_step(self, step: str) -> None:
        self.step = str(step or "00")

    def note_parse_failure(self, *, layer: str = "", message: str = "", raw: str = "") -> None:
        """Record an LLM reply that HTTP-succeeded but was not usable JSON."""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "step": self.step,
            "layer": str(layer or ""),
            "kind": "json_parse",
            "message": str(message or "")[:500],
            "raw": str(raw or "")[:800],
        }
        with self._lock:
            self._usage["parse_failed_calls"] = _int(self._usage.get("parse_failed_calls")) + 1
            bucket = self._usage["by_step"].setdefault(
                self.step,
                {
                    "calls": 0,
                    "cached_calls": 0,
                    "failed_calls": 0,
                    "parse_failed_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "prompt_cached_tokens": 0,
                },
            )
            bucket["parse_failed_calls"] = _int(bucket.get("parse_failed_calls")) + 1
            write_json(self.usage_path, dict(self._usage))
            fail_path = self.llm_dir / "failures.json"
            prior: list = []
            if fail_path.is_file():
                try:
                    loaded = json.loads(fail_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        prior = loaded
                    elif isinstance(loaded, dict) and isinstance(loaded.get("failures"), list):
                        prior = loaded["failures"]
                except (json.JSONDecodeError, OSError):
                    prior = []
            prior.append(row)
            write_json(fail_path, prior)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "seq": None,
                            "ts": row["ts"],
                            "step": self.step,
                            "cached": False,
                            "success": False,
                            "kind": "json_parse",
                            "layer": row["layer"],
                            "error": row["message"],
                            "hash": None,
                            "usage": {},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def wrap(self, model: Any) -> Any:
        if model is None:
            return None
        if isinstance(model, TracingModel):
            return model
        return TracingModel(model, self)

    def _reserve_budget_slot(self, *, counts_toward_budget: bool) -> None:
        if not counts_toward_budget or self.max_llm_step is None:
            return
        with self._lock:
            if (self._budget_used + self._budget_reserved) >= self.max_llm_step:
                self._usage["budget_exhausted"] = True
                write_json(self.usage_path, dict(self._usage))
                raise LlmBudgetExceeded(
                    used=self._budget_used,
                    limit=self.max_llm_step,
                    step=self.step,
                )
            self._budget_reserved += 1

    def _release_budget_slot(self, *, success: bool, counts_toward_budget: bool) -> None:
        if not counts_toward_budget or self.max_llm_step is None:
            return
        with self._lock:
            if self._budget_reserved > 0:
                self._budget_reserved -= 1
            if success:
                self._budget_used += 1
                self._usage["budget_used"] = self._budget_used
                if self._budget_used >= self.max_llm_step:
                    self._usage["budget_exhausted"] = True
            write_json(self.usage_path, dict(self._usage))

    def call(
        self,
        inner: Any,
        *,
        system: str,
        user: str,
        images: list | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        image_count = len(images) if images else 0
        digest = hashlib.sha1(
            f"{self.step}\n{system}\n{user}\n{image_count}".encode("utf-8", errors="replace")
        ).hexdigest()
        cache_path = self.hash_dir / f"{digest}.json"
        if self.reuse and cache_path.is_file():
            counts = self.count_cache_toward_budget
            self._reserve_budget_slot(counts_toward_budget=counts)
            try:
                record = json.loads(cache_path.read_text(encoding="utf-8"))
                result = {
                    "content": record.get("content") or "",
                    "usage": record.get("usage") or {},
                }
                self._commit(record, result, cached=True, digest=digest, success=True)
                self._release_budget_slot(success=True, counts_toward_budget=counts)
                return result
            except LlmBudgetExceeded:
                raise
            except Exception:
                self._release_budget_slot(success=False, counts_toward_budget=counts)
                raise

        # Live call always counts toward budget on success.
        self._reserve_budget_slot(counts_toward_budget=True)
        kwargs.pop("_trace_tag", None)
        try:
            if images is not None:
                result = inner.chat_json(system=system, user=user, images=images, **kwargs)
            else:
                result = inner.chat_json(system=system, user=user, **kwargs)
        except LlmBudgetExceeded:
            self._release_budget_slot(success=False, counts_toward_budget=True)
            raise
        except Exception as exc:
            self._commit(
                {
                    "step": self.step,
                    "model": getattr(inner, "model", ""),
                    "system": system,
                    "user": user,
                    "image_count": image_count,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                {"content": "", "usage": {}},
                cached=False,
                digest=digest,
                save_hash=False,
                success=False,
            )
            self._release_budget_slot(success=False, counts_toward_budget=True)
            raise

        if not isinstance(result, dict):
            result = {"content": str(result or ""), "usage": {}}
        record = {
            "step": self.step,
            "model": getattr(inner, "model", ""),
            "system": system,
            "user": user,
            "image_count": image_count,
            "content": result.get("content") or "",
            "usage": _usage_of(result),
        }
        self._commit(record, result, cached=False, digest=digest, save_hash=True, success=True)
        self._release_budget_slot(success=True, counts_toward_budget=True)
        return result

    def flush_summary(self) -> None:
        with self._lock:
            self._usage["budget_used"] = self._budget_used
            self._usage["budget_limit"] = self.max_llm_step
            self._usage["count_cache_toward_budget"] = self.count_cache_toward_budget
            if self.max_llm_step is not None:
                self._usage["budget_exhausted"] = self._budget_used >= self.max_llm_step
            write_json(self.usage_path, dict(self._usage))

    def _load_prior_state(self) -> None:
        if self.usage_path.is_file():
            try:
                prior = json.loads(self.usage_path.read_text(encoding="utf-8"))
                if isinstance(prior, dict):
                    merged = _empty_usage()
                    merged.update(prior)
                    self._usage = merged
                    # Resume budget from prior successful usage so limit stays fair.
                    prior_budget = _int(prior.get("budget_used"))
                    if prior_budget <= 0:
                        prior_budget = _int(prior.get("calls"))
                        if self.count_cache_toward_budget:
                            prior_budget += _int(prior.get("cached_calls"))
                    self._budget_used = prior_budget
                    self._usage["budget_used"] = self._budget_used
                    self._usage["budget_limit"] = self.max_llm_step
                    self._usage["count_cache_toward_budget"] = self.count_cache_toward_budget
                    if self.max_llm_step is not None:
                        self._usage["budget_exhausted"] = self._budget_used >= self.max_llm_step
            except (json.JSONDecodeError, OSError):
                pass
        max_seq = 0
        if self.log_path.is_file():
            try:
                for line in self.log_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    max_seq = max(max_seq, _int(row.get("seq")))
            except (json.JSONDecodeError, OSError):
                pass
        self._seq = max_seq

    def _commit(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
        *,
        cached: bool,
        digest: str,
        save_hash: bool = False,
        success: bool = True,
    ) -> None:
        usage = _usage_of(result) or record.get("usage") or {}
        with self._lock:
            self._seq += 1
            seq = self._seq
            step = str(record.get("step") or self.step)
            row = {
                "seq": seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "step": step,
                "cached": cached,
                "hash": digest,
                "model": record.get("model") or "",
                "image_count": record.get("image_count") or 0,
                "usage": usage,
                "error": record.get("error"),
                "success": success,
                "content": record.get("content") or "",
                "system": record.get("system") or "",
                "user": record.get("user") or "",
            }
            if save_hash and self.save_by_hash and success:
                write_json(self.hash_dir / f"{digest}.json", row)
            step_dir = self.llm_dir / step
            step_dir.mkdir(parents=True, exist_ok=True)
            write_json(step_dir / f"{seq:04d}.json", row)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "seq": seq,
                            "ts": row["ts"],
                            "step": step,
                            "cached": cached,
                            "success": success,
                            "hash": digest,
                            "usage": usage,
                            "error": row.get("error"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            bucket = self._usage["by_step"].setdefault(
                step,
                {
                    "calls": 0,
                    "cached_calls": 0,
                    "failed_calls": 0,
                    "parse_failed_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "prompt_cached_tokens": 0,
                },
            )
            prompt_tokens = _int(usage.get("prompt_tokens"))
            completion_tokens = _int(usage.get("completion_tokens"))
            total_tokens = _int(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
            # Provider cache hits remain part of prompt_tokens; tracked separately.
            prompt_cached_tokens = _int(usage.get("prompt_cached_tokens"))
            if not success:
                self._usage["failed_calls"] = _int(self._usage.get("failed_calls")) + 1
                bucket["failed_calls"] = _int(bucket.get("failed_calls")) + 1
            elif cached:
                self._usage["cached_calls"] += 1
                self._usage["cached_total_tokens"] += total_tokens
                bucket["cached_calls"] += 1
            else:
                self._usage["calls"] += 1
                self._usage["prompt_tokens"] += prompt_tokens
                self._usage["completion_tokens"] += completion_tokens
                self._usage["total_tokens"] += total_tokens
                self._usage["prompt_cached_tokens"] = (
                    _int(self._usage.get("prompt_cached_tokens")) + prompt_cached_tokens
                )
                bucket["calls"] += 1
                bucket["prompt_tokens"] += prompt_tokens
                bucket["completion_tokens"] += completion_tokens
                bucket["total_tokens"] += total_tokens
                bucket["prompt_cached_tokens"] = (
                    _int(bucket.get("prompt_cached_tokens")) + prompt_cached_tokens
                )
            self._usage["budget_used"] = self._budget_used
            self._usage["budget_limit"] = self.max_llm_step
            write_json(self.usage_path, dict(self._usage))
