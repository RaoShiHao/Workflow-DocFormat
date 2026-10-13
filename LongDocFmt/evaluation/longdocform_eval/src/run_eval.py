"""Evaluate LongDocFmtEval runner outputs: flatten output.docx, score checkpoints."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

from .flatten import flatten_docx
from .paths import (
    canon_agent,
    canon_skill,
    discover_result_sample_ids,
    load_json,
    resolve_output_docx,
    result_dir,
    result_model_dir,
    sample_dir,
    sample_eval_dir,
    write_json,
)
from .score import average_scores, sample_accuracy_report, score_flats

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_USAGE_SCALAR_KEYS = (
    "model_steps",
    "steps_with_usage",
    "command_executions",
    "agent_messages",
)
_USAGE_TOTAL_KEYS = (
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "reasoningTokens",
)


def load_eval_config(path: Path | None = None) -> dict[str, Any]:
    path = path or (PROJECT_ROOT / "configs" / "eval.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected mapping in {path}")
    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score LongDocFmtEval experiment outputs")
    p.add_argument("--agent", required=True, help="dp-harness | codex (alias: deepseek-harness → dp-harness)")
    p.add_argument("--skill", required=True, help="DocFmt-ToolSkill | DocFmt-Skill | officecli | officecli-orch | docxcli | longformatter | noskills")
    p.add_argument("--model", required=True, help="Model folder name under the experiment result root")
    p.add_argument(
        "--sample",
        action="append",
        dest="samples",
        help="Only these sample ids (repeatable). Default: every sample folder under the result dir",
    )
    p.add_argument("--force", action="store_true", help="Re-flatten even if eval/output.flat.json exists")
    p.add_argument("--list", action="store_true", help="List result samples and exit")
    p.add_argument("--self-check", action="store_true", help="Flatten dataset golden.docx and compare to golden.flat.json")
    return p.parse_args(argv)


def _result_model_dir(cfg: dict[str, Any], args: argparse.Namespace) -> Path:
    return result_model_dir(
        experiment_root=Path(cfg["experiment_root"]),
        agent=args.agent,
        skill=args.skill,
        model=args.model,
    )


def _sample_ids(args: argparse.Namespace, cfg: dict[str, Any]) -> list[str]:
    if args.samples:
        return [str(s).strip() for s in args.samples]
    discovered = discover_result_sample_ids(_result_model_dir(cfg, args))
    if discovered:
        return discovered
    return [str(s).strip() for s in (cfg.get("sample_ids") or [])]


def _eval_already_done(eval_folder: Path) -> bool:
    return (eval_folder / "output.flat.json").is_file()


def _list_results(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    probe = _result_model_dir(cfg, args)
    print(f"result_root: {probe}")
    if not probe.is_dir():
        print("  (directory does not exist)")
        return 1
    for sample_id in discover_result_sample_ids(probe):
        child = probe / sample_id
        has_out = resolve_output_docx(child) is not None
        done = _eval_already_done(sample_eval_dir(child))
        print(
            f"  {sample_id}: output.docx={'yes' if has_out else 'NO'} "
            f"eval={'done (will skip flatten)' if done else 'pending'}"
        )
    return 0


def _self_check(cfg: dict[str, Any], sample_id: str) -> dict[str, Any]:
    sample = sample_dir(Path(cfg["dataset_root"]), sample_id)
    golden_docx = sample / "golden.docx"
    golden_flat = load_json(sample / "eval" / "golden.flat.json")
    pred = flatten_docx(golden_docx, binary=str(cfg.get("officecli_bin") or "officecli"))
    hits = 0
    missing = 0
    mismatch = 0
    examples: list[dict[str, Any]] = []
    missing_keys: list[str] = []
    from .score import values_equal

    for key, want in golden_flat.items():
        got = pred.get(key, None)
        if key not in pred:
            missing += 1
            missing_keys.append(key)
            if len(examples) < 20:
                examples.append({"key": key, "expected": want, "actual": None, "reason": "missing"})
            continue
        if values_equal(got, want):
            hits += 1
        else:
            mismatch += 1
            if len(examples) < 20:
                examples.append({"key": key, "expected": want, "actual": got, "reason": "mismatch"})
    extra = sorted(set(pred) - set(golden_flat))
    total = len(golden_flat)
    return {
        "sample_id": sample_id,
        "golden_keys": total,
        "pred_keys": len(pred),
        "hits": hits,
        "missing": missing,
        "mismatch": mismatch,
        "extra_pred_keys": len(extra),
        "missing_keys": missing_keys,
        "recall": round(hits / total, 6) if total else None,
        "examples": examples,
    }


def slim_usage(raw: Any) -> dict[str, Any]:
    """LLM call stats only — drop per_step / event dumps."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _USAGE_SCALAR_KEYS:
        if key in raw:
            out[key] = raw[key]
    totals = raw.get("totals")
    if isinstance(totals, dict):
        out["totals"] = {k: totals[k] for k in _USAGE_TOTAL_KEYS if k in totals}
    return out


def _write_eval_usage(eval_folder: Path, usage_src: Path) -> dict[str, Any]:
    raw = load_json(usage_src) if usage_src.is_file() else {}
    slim = slim_usage(raw)
    write_json(eval_folder / "usage.json", slim)
    return slim


def _score_pred(
    *,
    pred: dict[str, Any],
    dataset_sample: Path,
    eval_folder: Path,
) -> dict[str, Any]:
    gold = load_json(dataset_sample / "eval" / "golden.flat.json")
    init_path = dataset_sample / "eval" / "source.flat.json"
    if not init_path.is_file():
        raise FileNotFoundError(
            f"missing {init_path}; run build_eval_cache before scoring "
            "(empty/missing init would invent a false need_change baseline)"
        )
    init_flat = load_json(init_path)
    scored = score_flats(pred=pred, gold=gold, init_flat=init_flat)
    report = sample_accuracy_report(
        scored,
        sample_dir=str(dataset_sample),
        integrity_status="not_checked",
    )
    write_json(eval_folder / "accuracy.json", report)
    return report


def _resolve_pred_flat(
    *,
    output_docx: Path,
    dataset_sample: Path,
    binary: str,
) -> dict[str, Any]:
    """Reuse golden/source flats when output is a byte copy (stable 0%/100% baselines)."""
    import hashlib

    golden = dataset_sample / "golden.docx"
    source = dataset_sample / "input.docx"
    if not source.is_file():
        for name in ("source.docx", "init.docx"):
            alt = dataset_sample / name
            if alt.is_file():
                source = alt
                break

    def _digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _same(a: Path, b: Path) -> bool:
        if not a.is_file() or not b.is_file():
            return False
        if a.resolve() == b.resolve():
            return True
        if a.stat().st_size != b.stat().st_size:
            return False
        return _digest(a) == _digest(b)

    if _same(output_docx, golden):
        return load_json(dataset_sample / "eval" / "golden.flat.json")
    if _same(output_docx, source):
        return load_json(dataset_sample / "eval" / "source.flat.json")
    return flatten_docx(output_docx, binary=binary)


def eval_one(
    *,
    cfg: dict[str, Any],
    agent: str,
    skill: str,
    model: str,
    sample_id: str,
    force: bool,
) -> dict[str, Any]:
    experiment_root = Path(cfg["experiment_root"])
    dataset_root = Path(cfg["dataset_root"])
    binary = str(cfg.get("officecli_bin") or "officecli")

    run_dir = result_dir(
        experiment_root=experiment_root,
        agent=agent,
        skill=skill,
        model=model,
        sample_id=sample_id,
    )
    eval_folder = sample_eval_dir(run_dir)
    dataset_sample = sample_dir(dataset_root, sample_id)
    output_docx = resolve_output_docx(run_dir)
    usage_src = run_dir / "usage.json"
    flat_path = eval_folder / "output.flat.json"

    if output_docx is None:
        return {"status": "no_output", "sample_id": sample_id}

    if cfg.get("skip_if_eval_exists", True) and not force and _eval_already_done(eval_folder):
        pred = load_json(flat_path)
        usage = _write_eval_usage(eval_folder, usage_src)
        report = _score_pred(pred=pred, dataset_sample=dataset_sample, eval_folder=eval_folder)
        return {
            "status": "skipped",
            "sample_id": sample_id,
            "scores": report.get("metrics") or {},
            "usage": usage,
        }

    t0 = time.time()
    try:
        pred = _resolve_pred_flat(
            output_docx=output_docx,
            dataset_sample=dataset_sample,
            binary=binary,
        )
    except Exception as exc:
        return {
            "status": "error",
            "sample_id": sample_id,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    elapsed = round(time.time() - t0, 2)
    write_json(flat_path, pred)
    usage = _write_eval_usage(eval_folder, usage_src)
    report = _score_pred(pred=pred, dataset_sample=dataset_sample, eval_folder=eval_folder)
    return {
        "status": "ok",
        "sample_id": sample_id,
        "elapsed_seconds": elapsed,
        "scores": report.get("metrics") or {},
        "usage": usage,
    }


def _mean_intish(values: list[Any]) -> float | None:
    nums: list[float] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            nums.append(float(value))
    if not nums:
        return None
    return round(sum(nums) / len(nums), 6)


def average_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n_with_usage": 0, "mean": {}}
    mean: dict[str, Any] = {}
    for key in _USAGE_SCALAR_KEYS:
        mean[key] = _mean_intish([r.get(key) for r in rows])
    totals_mean: dict[str, Any] = {}
    for key in _USAGE_TOTAL_KEYS:
        totals_mean[key] = _mean_intish([(r.get("totals") or {}).get(key) for r in rows])
    mean["totals"] = totals_mean
    return {"n_with_usage": len(rows), "mean": mean}


def build_summary(
    *,
    agent: str,
    skill: str,
    model: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    scored = [r for r in rows if r.get("status") in {"ok", "skipped"} and r.get("scores")]
    usages = [r["usage"] for r in scored if isinstance(r.get("usage"), dict) and r["usage"]]
    return {
        "agent": canon_agent(agent),
        "skill": canon_skill(skill),
        "model": model,
        "n_samples": len(rows),
        "n_scored": len(scored),
        "n_ok": sum(1 for r in rows if r.get("status") == "ok"),
        "n_skipped": sum(1 for r in rows if r.get("status") == "skipped"),
        "n_no_output": sum(1 for r in rows if r.get("status") == "no_output"),
        "n_failed": sum(1 for r in rows if r.get("status") not in {"ok", "skipped", "no_output"}),
        "scores": average_scores([r["scores"] for r in scored]),
        "usage": average_usage(usages),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_eval_config()
    if args.list:
        return _list_results(cfg, args)
    sample_ids = _sample_ids(args, cfg)
    if not sample_ids:
        raise SystemExit(
            f"no sample folders under {_result_model_dir(cfg, args)} "
            "(pass --sample, or run the experiment first)"
        )
    print(
        f"[eval] result_dir={_result_model_dir(cfg, args)} "
        f"samples={len(sample_ids)} {sample_ids}",
        flush=True,
    )

    if args.self_check:
        sid = sample_ids[0]
        print(f"[self-check] flatten golden.docx for sample {sid}", flush=True)
        report = _self_check(cfg, sid)
        write_json(PROJECT_ROOT / "work" / f"self_check_{sid}.json", report)
        print(
            f"[self-check] recall={report['recall']} hits={report['hits']} "
            f"missing={report['missing']} mismatch={report['mismatch']} extra={report['extra_pred_keys']}",
            flush=True,
        )
        if report.get("missing_keys"):
            print(f"  missing_keys={report['missing_keys']}", flush=True)
        for ex in report.get("examples") or []:
            print(f"  {ex['reason']}: {ex['key']} expected={ex['expected']!r} actual={ex['actual']!r}", flush=True)
        return 0

    rows: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        print(f"[eval] agent={args.agent} skill={args.skill} model={args.model} sample={sample_id}", flush=True)
        try:
            doc = eval_one(
                cfg=cfg,
                agent=args.agent,
                skill=args.skill,
                model=args.model,
                sample_id=sample_id,
                force=bool(args.force),
            )
        except Exception as exc:
            doc = {
                "status": "error",
                "sample_id": sample_id,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
            print(f"[fail] {doc['error']}", flush=True)
        else:
            scores = doc.get("scores") or {}
            if doc.get("status") == "skipped":
                print(
                    f"[skip] sample={sample_id} eval/output.flat.json exists "
                    f"accuracy={scores.get('accuracy')} "
                    f"hallucination_rate={scores.get('hallucination_rate')}",
                    flush=True,
                )
            else:
                print(
                    f"[done] status={doc.get('status')} "
                    f"accuracy={scores.get('accuracy')} "
                    f"hallucination_rate={scores.get('hallucination_rate')}",
                    flush=True,
                )
        rows.append(doc)

    summary = build_summary(agent=args.agent, skill=args.skill, model=args.model, rows=rows)
    summary_path = _result_model_dir(cfg, args) / "summary.json"
    write_json(summary_path, summary)
    print(
        f"[summary] wrote {summary_path} "
        f"accuracy={summary['scores'].get('accuracy')} "
        f"hallucination_rate={summary['scores'].get('hallucination_rate')} "
        f"usage_n={summary['usage'].get('n_with_usage')}",
        flush=True,
    )
    return 1 if summary["n_failed"] else 0


if __name__ == "__main__":
    if __package__ is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        __package__ = "src"
    raise SystemExit(main())
