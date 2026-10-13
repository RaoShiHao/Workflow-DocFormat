"""Core logic: step3d plan blocks — heading gaps (hard rules 1/2, soft 3)."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from LongDocFmtEval.pipeline_auto.auto_paths import resolve_outline_sidecar
from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index
from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level

from LongDocFmtEval.pipeline_auto.scripts.errors_clean._paths import default_datasets_dir

_TOPIC_RE = re.compile(r"^(\d+)_")
_CHAPTER_RE = re.compile(r"^C(\d+)$", re.I)


@dataclass
class PlanViolation:
    genre_id: int
    chapter: str
    topic_dir: str
    section_id: str
    rule: str  # hard_same_level | hard_leaf | soft_no_bridge
    detail: str
    block_ids: list[str] = field(default_factory=list)


@dataclass
class SampleHardHit:
    """One id×C with hard-rule violations."""

    genre_id: int
    chapter: str
    topic_dir: str
    section_ids: list[str] = field(default_factory=list)
    violations: list[PlanViolation] = field(default_factory=list)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_topic_id(name: str) -> int | None:
    m = _TOPIC_RE.match(name)
    return int(m.group(1)) if m else None


def style_maps(plan: dict) -> tuple[dict, dict, dict]:
    idx = heading_index(plan)
    name_by: dict[str, str] = {}
    obj_by: dict[str, str] = {}
    for item in plan.get("stylesheet") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if not sid:
            continue
        if item.get("display_name"):
            name_by[sid] = str(item["display_name"])
        if item.get("object"):
            obj_by[sid] = str(item["object"])
    return idx, name_by, obj_by


def block_heading_level(b: dict, idx: dict, name_by: dict) -> int | None:
    rid = str(b.get("role_id") or "")
    rec = idx.get(rid) or {}
    hl = rec.get("heading_level")
    if hl:
        return int(hl)
    return heading_level(rid, name_by.get(rid) or rid)


def is_table_block(b: dict, obj_by: dict) -> bool:
    if b.get("table_ref"):
        return True
    rid = str(b.get("role_id") or "")
    if rid.startswith("Tbl"):
        return True
    return obj_by.get(rid) == "table" or str(b.get("length_hint") or "") == "table"


def is_image_block(b: dict, obj_by: dict) -> bool:
    rid = str(b.get("role_id") or "")
    if rid.startswith("Img"):
        return True
    return obj_by.get(rid) == "image" or str(b.get("length_hint") or "") == "image"


def is_body_block(b: dict, idx: dict, name_by: dict) -> bool:
    hint = str(b.get("length_hint") or "").lower()
    if block_heading_level(b, idx, name_by) is not None:
        return False
    return hint in {"medium", "long"}


def is_bridge_block(b: dict, idx: dict, name_by: dict, obj_by: dict) -> bool:
    hint = str(b.get("length_hint") or "").lower()
    if hint not in {"short", "medium"}:
        return False
    return (
        block_heading_level(b, idx, name_by) is None
        and not is_table_block(b, obj_by)
        and not is_image_block(b, obj_by)
    )


def slot_stats(
    blocks: list[dict], idx: dict, name_by: dict, obj_by: dict
) -> tuple[int, int, int]:
    n_body = n_table = n_image = 0
    for b in blocks:
        if is_body_block(b, idx, name_by):
            n_body += 1
        elif is_table_block(b, obj_by):
            n_table += 1
        elif is_image_block(b, obj_by):
            n_image += 1
    return n_body, n_table, n_image


def content_ok(n_body: int, n_table: int, n_image: int) -> bool:
    # Audit threshold ≥1 body; step3d prompts still suggest ≥2.
    return n_body >= 1 or n_table >= 1 or n_image >= 1


def _fmt_stats(n_body: int, n_table: int, n_image: int) -> str:
    return f"正文{n_body} 表{n_table} 图{n_image}"


def _heading_label(b: dict) -> str:
    mp = str(b.get("main_point") or "").strip()
    return mp[:36] + ("…" if len(mp) > 36 else "") if mp else str(b.get("block_id") or "?")


def audit_section_blocks(
    blocks: list[dict],
    idx: dict,
    name_by: dict,
    obj_by: dict,
) -> list[tuple[str, str, list[str]]]:
    hits: list[tuple[str, str, list[str]]] = []
    n = len(blocks)
    if not n:
        return hits

    levels: list[int | None] = [block_heading_level(b, idx, name_by) for b in blocks]

    for i, b in enumerate(blocks):
        hl = levels[i]
        if hl is None:
            continue
        bid = str(b.get("block_id") or "")

        j_end = n
        has_deeper = False
        for j in range(i + 1, n):
            hj = levels[j]
            if hj is None:
                continue
            if hj > hl:
                has_deeper = True
                continue
            j_end = j
            break

        middle = blocks[i + 1 : j_end]
        nb, nt, ni = slot_stats(middle, idx, name_by, obj_by)

        if not has_deeper and not content_ok(nb, nt, ni):
            hits.append((
                "hard_leaf",
                f"叶 H{hl}「{_heading_label(b)}」下 {_fmt_stats(nb, nt, ni)}（需≥1正文或≥1表/图）",
                [bid],
            ))

        if j_end < n and levels[j_end] == hl:
            b_next = blocks[j_end]
            if not content_ok(nb, nt, ni):
                hits.append((
                    "hard_same_level",
                    f"同级 H{hl}「{_heading_label(b)}」→「{_heading_label(b_next)}」间 "
                    f"{_fmt_stats(nb, nt, ni)}（需≥1正文或≥1表/图）",
                    [bid, str(b_next.get("block_id") or "")],
                ))

        if has_deeper:
            first_child = None
            for j in range(i + 1, j_end):
                hj = levels[j]
                if hj == hl + 1:
                    first_child = j
                    break
            if first_child is not None:
                bridge = blocks[i + 1 : first_child]
                if not any(is_bridge_block(x, idx, name_by, obj_by) for x in bridge):
                    if not any(is_body_block(x, idx, name_by) for x in bridge):
                        child_b = blocks[first_child]
                        hits.append((
                            "soft_no_bridge",
                            f"H{hl}「{_heading_label(b)}」→ H{hl + 1}「{_heading_label(child_b)}」无衔接段（可选）",
                            [bid, str(child_b.get("block_id") or "")],
                        ))

    return hits


def hard_body_slot_hits(
    blocks: list[dict],
    stylesheet: list[dict],
) -> list[tuple[str, str, list[str]]]:
    """Hard rules (1)(2) only — same API as audit_section_blocks hits."""
    idx, name_by, obj_by = style_maps({"stylesheet": stylesheet})
    return [h for h in audit_section_blocks(blocks, idx, name_by, obj_by) if h[0].startswith("hard_")]


def format_body_slot_retry_feedback(hits: list[tuple[str, str, list[str]]]) -> str:
    lines = [
        "标题下正文槽位硬规则未通过（同级/叶标题之间须 ≥1 个 medium/long 正文，或 ≥1 表/图块）：",
    ]
    for rule, detail, _bids in hits:
        tag = "同级" if rule == "hard_same_level" else "叶下"
        lines.append(f"  · [{tag}] {detail}")
    lines.append(
        "请在上次 blocks 基础上改正：在违规标题之间或叶标题下插入 medium/long 正文，"
        "或 Tbl*/Img*（正文图/表须紧邻题注）。禁止连续同级标题中间无任何正文/表/图。"
    )
    return "\n".join(lines)


def find_plan_path(topic: Path, chapter: Path) -> Path | None:
    for sub in ("step3_outline", "step5_template", "step6_document"):
        base = chapter / sub
        if not base.is_dir():
            continue
        p = resolve_outline_sidecar(base, "plan.json")
        if p.is_file():
            return p
        hit = base / "plan.json"
        if hit.is_file():
            return hit
    return None


def audit_topic(topic: Path, genre_id: int) -> list[PlanViolation]:
    out: list[PlanViolation] = []
    for ch in sorted(topic.iterdir()):
        if not ch.is_dir() or not _CHAPTER_RE.match(ch.name):
            continue
        plan_path = find_plan_path(topic, ch)
        if not plan_path:
            continue
        plan = load_json(plan_path)
        idx, name_by, obj_by = style_maps(plan)
        outline_map = {
            str(o.get("instance_id")): (o.get("blocks") or [])
            for o in (plan.get("outline") or [])
            if isinstance(o, dict)
        }
        for sec_id, blocks in outline_map.items():
            if not isinstance(blocks, list):
                continue
            for rule, detail, bids in audit_section_blocks(blocks, idx, name_by, obj_by):
                out.append(PlanViolation(
                    genre_id=genre_id,
                    chapter=ch.name.upper(),
                    topic_dir=topic.name,
                    section_id=sec_id,
                    rule=rule,
                    detail=detail,
                    block_ids=bids,
                ))
    return out


def scan_datasets(
    datasets_dir: Path | None = None,
    ids: set[int] | None = None,
) -> list[PlanViolation]:
    ds = datasets_dir or default_datasets_dir()
    all_hits: list[PlanViolation] = []
    for topic in sorted(ds.iterdir()):
        if not topic.is_dir():
            continue
        gid = parse_topic_id(topic.name)
        if gid is None:
            continue
        if ids is not None and gid not in ids:
            continue
        all_hits.extend(audit_topic(topic, gid))
    return all_hits


def hard_violations(hits: list[PlanViolation]) -> list[PlanViolation]:
    return [h for h in hits if h.rule.startswith("hard_")]


def group_hard_by_sample(hits: list[PlanViolation]) -> list[SampleHardHit]:
    buckets: dict[tuple[int, str], SampleHardHit] = {}
    for h in hard_violations(hits):
        key = (h.genre_id, h.chapter.upper())
        if key not in buckets:
            buckets[key] = SampleHardHit(
                genre_id=h.genre_id,
                chapter=h.chapter.upper(),
                topic_dir=h.topic_dir,
            )
        rec = buckets[key]
        if h.section_id not in rec.section_ids:
            rec.section_ids.append(h.section_id)
        rec.violations.append(h)
    for rec in buckets.values():
        rec.section_ids.sort()
    return [buckets[k] for k in sorted(buckets)]
