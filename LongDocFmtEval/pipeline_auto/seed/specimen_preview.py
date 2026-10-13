"""Build a minimal specimen outline for preview.docx (stylesheet exemplars)."""
from __future__ import annotations

from collections import Counter
from typing import Any


def _block_role(block: dict[str, Any], roles: dict[str, Any] | None = None) -> str:
    rid = str(block.get("role_id") or "")
    if roles:
        # table_ref / image_ref may point at Tbl*/Img* while role_id is symbolic
        for key in ("table_ref", "image_ref"):
            ref = block.get(key)
            if ref and ref in roles:
                return str(ref)
        if rid in roles:
            return rid
    return rid


def build_specimen_outline_map(
    plan: dict[str, Any],
    roles: dict[str, Any] | None = None,
    *,
    per_role: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Keep ≤``per_role`` blocks per style role, preserving reading order.

    Returns (sections_kept, outline_map, stats).
    Sections that contribute no new role exemplars are dropped (except we always
    keep a section if it is the first instance of that ``Sec*`` and we still need
    its page/HF chrome — then we keep it with whatever new roles it adds, or a
    single first block if empty after filter but Sec* unseen).
    """
    roles = roles or {}
    sections = plan.get("sections") or []
    outline_map = {
        o.get("instance_id"): list(o.get("blocks") or [])
        for o in (plan.get("outline") or [])
    }
    role_seen: Counter[str] = Counter()
    sec_seen: Counter[str] = Counter()
    sections_kept: list[dict[str, Any]] = []
    out_map: dict[str, list[dict[str, Any]]] = {}

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        inst = sec.get("instance_id")
        sec_role = str(sec.get("role_id") or "")
        blocks = outline_map.get(inst) or []
        kept: list[dict[str, Any]] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            rid = _block_role(b, roles)
            if not rid:
                continue
            if role_seen[rid] < per_role:
                kept.append(b)
                role_seen[rid] += 1
        first_sec = bool(sec_role) and sec_seen[sec_role] < 1
        if kept:
            sections_kept.append(sec)
            out_map[inst] = kept
            if sec_role:
                sec_seen[sec_role] += 1
        elif first_sec:
            # Keep section chrome; take first block as anchor if any
            sections_kept.append(sec)
            out_map[inst] = blocks[:1] if blocks else []
            sec_seen[sec_role] += 1
            for b in out_map[inst]:
                rid = _block_role(b, roles)
                if rid:
                    role_seen[rid] += 1

    stats = {
        "mode": "specimen",
        "per_role": per_role,
        "sections_in": len(sections),
        "sections_out": len(sections_kept),
        "blocks_in": sum(len(v) for v in outline_map.values()),
        "blocks_out": sum(len(v) for v in out_map.values()),
        "roles_sampled": dict(role_seen),
    }
    return sections_kept, out_map, stats
