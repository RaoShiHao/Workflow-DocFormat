"""Sample-level remediation for pipeline_auto (incomplete id+Cx only).

Unit = one sample ``{topic_id}_{Cx}`` (e.g. ``12_C2``).

Only **incomplete** samples are recorded (no ``document.docx`` yet).
Intermediate / recovered issues inside a finished sample are ignored.

Three operator actions:

  - ``retry``               → 直接重跑（样本 + 原因）
  - ``manual_role_params``  → 人工改 role_params（格式碰撞挡住成稿）
  - ``delete_and_retry``    → 删指定缓存后再跑

Writes JSON only under ``datasets/_batch_logs/``:

  - ``{id}_{Cx}.json``  per incomplete sample
  - ``summary.json``    three-way rollup
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json
from LongDocFmtEval.pipeline_auto.topic_layout import (
    document_dir,
    outline_dir,
    styles_dir,
    template_dir,
)

# Public action keys (summary buckets)
ACTION_RETRY = "retry"
ACTION_MANUAL = "manual_role_params"
ACTION_DELETE = "delete_and_retry"
ACTIONS = (ACTION_RETRY, ACTION_MANUAL, ACTION_DELETE)

_API_HINT = re.compile(
    r"unavailable|temporarily|upstream|timeout|timed?\s*out|503|429|"
    r"connection|rate.?limit|no upstream|resource",
    re.I,
)


def _read_json(path: Path) -> dict[str, Any] | list | None:
    if not path.is_file():
        return None
    try:
        return load_json(path)
    except Exception:  # noqa: BLE001
        return None


def _docx_ready(path: Path, *, min_bytes: int = 10_000) -> bool:
    return path.is_file() and path.stat().st_size > min_bytes


def _topic_id_from_dir(topic: Path) -> str:
    m = re.match(r"^(\d+)_", topic.name)
    return m.group(1) if m else topic.name


def sample_id(topic_id: str | int, cx: str) -> str:
    """Canonical sample key used in filenames and summary: ``12_C2``."""
    return f"{topic_id}_{cx}"


def _plan_ok(plan: Any) -> bool:
    if not isinstance(plan, dict) or not plan:
        return False
    secs = plan.get("sections") or []
    outline = plan.get("outline") or []
    ss = plan.get("stylesheet") or []
    n_blocks = sum(len(o.get("blocks") or []) for o in outline if isinstance(o, dict))
    return bool(secs) and n_blocks > 0 and bool(ss)


def _rel(topic: Path, path: Path) -> str:
    try:
        return str(path.relative_to(topic)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _entry(
    *,
    sample: str,
    topic_id: str,
    complexity: str,
    action: str,
    step: int,
    reason: str,
    where: str,
    delete_paths: list[str] | None = None,
    collision: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "sample": sample,
        "topic_id": str(topic_id),
        "complexity": complexity,
        "action": action,
        "step": step,
        "reason": reason[:320],
        "where": where,
    }
    if delete_paths:
        out["delete_paths"] = delete_paths
    if collision:
        out["collision"] = collision
    return out


def classify_sample(topic: Path, cx: str) -> dict[str, Any] | None:
    """Return a remediation entry if the sample is incomplete; else ``None``."""
    topic = Path(topic)
    tid = _topic_id_from_dir(topic)
    sid = sample_id(tid, cx)
    od = outline_dir(topic, complexity=cx)
    st = styles_dir(topic, complexity=cx)
    tpl = template_dir(topic, complexity=cx)
    doc = document_dir(topic, complexity=cx)

    # Finished sample → no log (even if intermediate validation/chunks looked bad).
    if _docx_ready(doc / "document.docx"):
        return None

    err3 = _read_json(od / "error.json")
    plan = _read_json(od / "plan.json")
    plan_good = _plan_ok(plan)

    if isinstance(err3, dict) and err3.get("marker") == "STEP3_FAILED":
        msg = str(err3.get("error") or err3.get("reason") or "STEP3_FAILED")
        stage = str(err3.get("stage") or "")
        where = _rel(topic, od / "error.json")
        if _API_HINT.search(msg) or _API_HINT.search(str(err3.get("error_type") or "")):
            return _entry(
                sample=sid, topic_id=tid, complexity=cx,
                action=ACTION_RETRY, step=3,
                reason=f"step3 API/上游失败 ({stage or '?'}): {msg}",
                where=where,
            )
        deletes = [_rel(topic, od / "error.json")]
        if (od / "plan.json").is_file() and not plan_good:
            deletes.append(_rel(topic, od / "plan.json"))
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_DELETE, step=3,
            reason=f"step3 失败 ({stage or '?'}): {msg}",
            where=where,
            delete_paths=deletes,
        )

    if not plan_good:
        if (od / "plan.json").is_file() or (od / "writing_outline.json").is_file():
            deletes = []
            for name in ("plan.json", "error.json"):
                p = od / name
                if p.is_file():
                    deletes.append(_rel(topic, p))
            return _entry(
                sample=sid, topic_id=tid, complexity=cx,
                action=ACTION_DELETE, step=3,
                reason="plan.json 缺失或无效（sections/blocks/stylesheet 不足）",
                where=_rel(topic, od),
                delete_paths=deletes,
            )
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_RETRY, step=3,
            reason="尚未生成大纲",
            where=_rel(topic, od),
        )

    val = _read_json(st / "validation.json") if (st / "validation.json").is_file() else None
    rp_ok = (st / "role_params.json").is_file()
    tj_ok = (st / "template.json").is_file()
    tpl_docx = tpl / "template.docx"
    tpl_ok = _docx_ready(tpl_docx)

    def _collision_label(v: dict | None) -> str:
        if not isinstance(v, dict):
            return ""
        parts: list[str] = []
        for c in v.get("collisions") or []:
            if isinstance(c, dict):
                parts.append(f"{c.get('a')}≈{c.get('b')}")
        for g in v.get("style_shares") or []:
            if isinstance(g, dict):
                members = g.get("members") or []
                parts.append("+".join(str(m) for m in members[:4]))
        return "; ".join(parts)[:200]

    def _has_collision(v: dict | None) -> bool:
        if not isinstance(v, dict):
            return False
        if v.get("collisions") or v.get("style_shares"):
            return True
        return any(
            "同格式" in str(p) or "collision" in str(p).lower()
            for p in (v.get("problems") or [])
        )

    # step4 hard-stop: no usable T* yet
    if not rp_ok or not tj_ok:
        if _has_collision(val if isinstance(val, dict) else None):
            return _entry(
                sample=sid, topic_id=tid, complexity=cx,
                action=ACTION_MANUAL, step=4,
                reason="同格式碰撞，需人工改 role_params 后 --overwrite 重跑 step4+",
                where=_rel(topic, st / "validation.json") if (st / "validation.json").is_file() else _rel(topic, st),
                collision=_collision_label(val if isinstance(val, dict) else None),
            )
        problems = [str(p) for p in ((val or {}).get("problems") or [])] if isinstance(val, dict) else []
        missing = [p for p in problems if "缺少角色" in p or "missing" in p.lower()]
        if missing:
            return _entry(
                sample=sid, topic_id=tid, complexity=cx,
                action=ACTION_DELETE, step=4,
                reason=missing[0],
                where=_rel(topic, st),
                delete_paths=[
                    _rel(topic, st / n)
                    for n in ("role_params.json", "template.json", "validation.json")
                    if (st / n).is_file()
                ],
            )
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_RETRY, step=4,
            reason="缺 role_params.json / template.json",
            where=_rel(topic, st),
        )

    # Collision only matters if it still blocked template/docx (warn_continue + finished → ignored)
    if not tpl_ok and _has_collision(val if isinstance(val, dict) else None):
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_MANUAL, step=4,
            reason="同格式碰撞，template.docx 未产出",
            where=_rel(topic, st / "validation.json"),
            collision=_collision_label(val if isinstance(val, dict) else None),
        )

    if not tpl_ok:
        if tpl_docx.is_file():
            return _entry(
                sample=sid, topic_id=tid, complexity=cx,
                action=ACTION_DELETE, step=5,
                reason="template.docx 过小/损坏",
                where=_rel(topic, tpl_docx),
                delete_paths=[_rel(topic, tpl_docx)],
            )
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_RETRY, step=5,
            reason="缺 template.docx",
            where=_rel(topic, tpl),
        )

    # step6 incomplete
    chunks = doc / "chunks"
    if chunks.is_dir() and any(chunks.glob("*.json")):
        return _entry(
            sample=sid, topic_id=tid, complexity=cx,
            action=ACTION_RETRY, step=6,
            reason="document 未齐，chunks/ 可续写（勿删）",
            where=_rel(topic, doc),
        )
    return _entry(
        sample=sid, topic_id=tid, complexity=cx,
        action=ACTION_RETRY, step=6,
        reason="document.docx 缺失或过小",
        where=_rel(topic, doc),
    )


def scan_topic(
    topic: Path,
    *,
    complexities: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Classify incomplete samples under one topic."""
    topic = Path(topic)
    if complexities:
        cxs = tuple(complexities)
    else:
        found = [c for c in COMPLEXITY_TIERS if (topic / c).is_dir()]
        cxs = tuple(found) if found else COMPLEXITY_TIERS
    out: list[dict[str, Any]] = []
    for cx in cxs:
        item = classify_sample(topic, cx)
        if item is not None:
            out.append(item)
    return out


def build_summary(items: list[dict[str, Any]], *, label: str = "") -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {k: [] for k in ACTIONS}
    for it in items:
        action = str(it.get("action") or ACTION_RETRY)
        if action not in by:
            action = ACTION_RETRY
            it = {**it, "action": action}
        # slim rows in summary
        row = {
            "sample": it.get("sample"),
            "step": it.get("step"),
            "reason": it.get("reason"),
            "where": it.get("where"),
        }
        if it.get("collision"):
            row["collision"] = it["collision"]
        if it.get("delete_paths"):
            row["delete_paths"] = it["delete_paths"]
        by[action].append(row)
    return {
        "label": label,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {k: len(by[k]) for k in ACTIONS},
        "retry": by[ACTION_RETRY],
        "manual_role_params": by[ACTION_MANUAL],
        "delete_and_retry": by[ACTION_DELETE],
    }


def write_sample_logs(
    items: list[dict[str, Any]],
    out_dir: Path,
    *,
    label: str = "",
) -> Path:
    """Write ``{id}_{Cx}.json`` per incomplete sample + ``summary.json``. Returns summary path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Drop legacy dual-format batch reports
    for p in out_dir.glob("batch_remediation_*"):
        try:
            p.unlink()
        except OSError:
            pass

    for it in items:
        sid = str(it.get("sample") or "unknown")
        dump_json(out_dir / f"{sid}.json", it)

    summary = build_summary(items, label=label)
    summary_path = out_dir / "summary.json"
    dump_json(summary_path, summary)
    return summary_path


def _load_existing_sample_logs(log_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not log_dir.is_dir():
        return items
    for p in sorted(log_dir.glob("*_C*.json")):
        data = _read_json(p)
        if isinstance(data, dict) and data.get("sample") and data.get("action") in ACTIONS:
            items.append(data)
    return items


def _clear_completed_logs(topic: Path, log_dir: Path, incomplete: set[str], complexities: tuple[str, ...]) -> None:
    tid = _topic_id_from_dir(topic)
    for cx in complexities:
        sid = sample_id(tid, cx)
        path = log_dir / f"{sid}.json"
        if sid not in incomplete and path.is_file():
            path.unlink()


def _drop_legacy_topic_reports(topic: Path) -> None:
    for legacy in ("remediation.json", "remediation.md"):
        lp = topic / legacy
        if lp.is_file():
            try:
                lp.unlink()
            except OSError:
                pass


def write_topic_remediation(
    topic: Path,
    *,
    complexities: tuple[str, ...] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Scan one topic; refresh ``{id}_{Cx}.json`` + ``summary.json`` (JSON only)."""
    from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: PLC0415

    topic = Path(topic)
    log_dir = Path(out_dir) if out_dir else (DATASETS_DIR / "_batch_logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    cxs = complexities or tuple(
        c for c in COMPLEXITY_TIERS if (topic / c).is_dir()
    ) or COMPLEXITY_TIERS
    found = scan_topic(topic, complexities=cxs)
    incomplete = {str(it["sample"]) for it in found}
    _clear_completed_logs(topic, log_dir, incomplete, cxs)
    _drop_legacy_topic_reports(topic)

    # Write/overwrite this topic's incomplete samples
    for it in found:
        dump_json(log_dir / f"{it['sample']}.json", it)

    # Summary = all sample logs still on disk (other topics kept)
    all_items = _load_existing_sample_logs(log_dir)
    summary_path = log_dir / "summary.json"
    dump_json(summary_path, build_summary(all_items, label=f"topic={topic.name}"))
    data = _read_json(summary_path)
    return data if isinstance(data, dict) else build_summary(all_items, label=topic.name)


def write_batch_remediation(
    reports: list[Any] | None = None,
    out_dir: Path | None = None,
    *,
    label: str = "",
    items: list[dict[str, Any]] | None = None,
) -> Path:
    """Refresh ``summary.json`` from per-sample logs (and optional ``items``)."""
    from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: PLC0415

    log_dir = Path(out_dir) if out_dir else (DATASETS_DIR / "_batch_logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    if items is not None:
        for it in items:
            if it.get("sample"):
                dump_json(log_dir / f"{it['sample']}.json", it)
    elif reports:
        # legacy: ignore topic rollup dicts; rebuild from disk
        pass

    all_items = _load_existing_sample_logs(log_dir)
    return write_sample_logs(all_items, log_dir, label=label or "batch")


def scan_topics_and_write(
    topics: list[Path],
    *,
    complexities: tuple[str, ...] | None = None,
    out_dir: Path | None = None,
    label: str = "",
) -> dict[str, Any]:
    """Scan many topics; write incomplete ``{id}_{Cx}.json`` + ``summary.json``."""
    from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: PLC0415

    log_dir = Path(out_dir) if out_dir else (DATASETS_DIR / "_batch_logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    scanned_ids: set[str] = set()
    for topic in topics:
        topic = Path(topic)
        if not topic.is_dir():
            continue
        cxs = complexities or tuple(
            c for c in COMPLEXITY_TIERS if (topic / c).is_dir()
        ) or COMPLEXITY_TIERS
        found = scan_topic(topic, complexities=cxs)
        incomplete = {str(it["sample"]) for it in found}
        _clear_completed_logs(topic, log_dir, incomplete, cxs)
        _drop_legacy_topic_reports(topic)
        for it in found:
            dump_json(log_dir / f"{it['sample']}.json", it)
            scanned_ids.add(str(it["sample"]))

    all_items = _load_existing_sample_logs(log_dir)
    summary_path = write_sample_logs(all_items, log_dir, label=label)
    data = _read_json(summary_path)
    return data if isinstance(data, dict) else build_summary(all_items, label=label)
