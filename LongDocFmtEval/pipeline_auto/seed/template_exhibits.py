"""Template/preview exhibit policy for tables & images ONLY.

Full outline may plan many table/image **styles** and many instances.
Preview/template.docx only needs a few **instances per style** (specimen aims for 1;
``build_figures`` preview still caps as a safety net even when ``specimen_plan.json`` exists):

  - **Every** Tbl*/Img* style used in the outline may appear in preview
    (no cap on how many distinct Tbl*/Img* styles exist)
  - Each style is exhibited at most ``per_style`` **instances** (default 2 pictures/tables
    of that style — not “at most 2 image styles”)
  - If the LLM marked every instance ``template_exhibit: false``, still exhibit up to
    ``per_style`` instances per style (do not emit an empty preview)
  - Prefer instances that already have a bound caption (so preview is not filled
    by early uncaptioned copies of the same Tbl*/Img* role)
  - Captions tied to non-exhibited table/image blocks are omitted together

There is **no** cap on how many distinct table/image styles the stylesheet may contain.
(Earlier ``max_styles=2`` was a misread of the per-role exhibit limit.)

**Critical (do not regress):** exhibit filtering is **preview-only**.
Step5 / ``document.docx`` / ``assets.json`` (``build_figures`` variant=document)
must collect and render the **full** outline image/table instances.
Never reuse ``assets_preview`` counts as the gold inventory — historically,
limiting preview relieved overcrowding, but gold must still grow with the outline.

**Never** limit section / body paragraph / run completeness — those stay in full
reading order. Only ``object in {table, image}`` and their caption/note paras
participate in exhibit skipping.
"""
from __future__ import annotations

from typing import Any

from LongDocFmtEval.pipeline_auto.seed.caption_bind import (
    captioned_media_block_ids,
    is_caption_role,
)

DEFAULT_PER_STYLE = 2


def _obj_of(block: dict, roles: dict) -> str:
    rid = str(block.get("role_id") or "")
    return str((roles.get(rid) or {}).get("object") or "")


def _ref_keys(block: dict) -> set[str]:
    out: set[str] = set()
    for k in ("table_ref", "image_ref", "figure_id"):
        v = block.get(k)
        if v:
            out.add(f"{k}:{v}")
    return out


def plan_template_exhibits(
    sections: list[dict],
    outline_map: dict[str, list[dict]],
    roles: dict,
    *,
    per_style: int = DEFAULT_PER_STYLE,
    max_styles: int | None = None,
) -> dict[str, Any]:
    """Decide which table/image blocks appear in preview/template.

    ``max_styles`` is retained for API compatibility but **ignored** (all styles allowed).
    Only ``per_style`` limits how many times each role_id is demonstrated.
    """
    del max_styles  # unlimited distinct styles
    per_style = max(1, int(per_style))

    ordered: list[dict] = []
    for sec in sections:
        inst = sec.get("instance_id")
        for b in outline_map.get(inst, []) or []:
            obj = _obj_of(b, roles)
            if obj in ("table", "image"):
                ordered.append(b)

    allow_tbl: set[str] = set()
    allow_img: set[str] = set()
    for b in ordered:
        rid = str(b.get("role_id") or "")
        obj = _obj_of(b, roles)
        if not rid:
            continue
        if obj == "table":
            allow_tbl.add(rid)
        elif obj == "image":
            allow_img.add(rid)
    allowed = allow_tbl | allow_img

    counts: dict[str, int] = {}
    exhibit_ids: set[str] = set()
    captioned = captioned_media_block_ids(sections, outline_map, roles)

    def _try_take(b: dict, *, ignore_false: bool = False) -> None:
        if (not ignore_false) and b.get("template_exhibit") is False:
            return
        rid = str(b.get("role_id") or "")
        bid = str(b.get("block_id") or "")
        if not bid or bid in exhibit_ids:
            return
        if rid not in allowed:
            return
        if counts.get(rid, 0) >= per_style:
            return
        exhibit_ids.add(bid)
        counts[rid] = counts.get(rid, 0) + 1

    def _priority(b: dict) -> int:
        explicit = b.get("template_exhibit") is True
        has_cap = str(b.get("block_id") or "") in captioned
        # Prefer captioned exhibits so preview is not filled by cover-info / early uncaptioned copies.
        if explicit and has_cap:
            return 0
        if has_cap:
            return 1
        if explicit:
            return 2
        return 3

    for prio in (0, 1, 2, 3):
        for b in ordered:
            if _priority(b) == prio:
                _try_take(b)

    # LLM often copies template_exhibit=false onto every instance. Preview still
    # demonstrates each Tbl*/Img* kind (≤per_style), without exceeding the cap.
    for prio in (0, 1, 2, 3):
        for b in ordered:
            rid = str(b.get("role_id") or "")
            if not rid or counts.get(rid, 0) > 0:
                continue
            if _priority(b) == prio:
                _try_take(b, ignore_false=True)

    skipped_refs: set[str] = set()
    skipped_media_ids: set[str] = set()
    for b in ordered:
        bid = str(b.get("block_id") or "")
        if bid and bid not in exhibit_ids:
            skipped_media_ids.add(bid)
            skipped_refs |= _ref_keys(b)

    skip_block_ids: set[str] = set(skipped_media_ids)
    for sec in sections:
        inst = sec.get("instance_id")
        blocks = list(outline_map.get(inst, []) or [])
        for i, b in enumerate(blocks):
            bid = str(b.get("block_id") or "")
            rid = str(b.get("role_id") or "")
            if not bid or not is_caption_role(rid):
                continue
            if b.get("template_exhibit") is False:
                adj_shown = False
                if i + 1 < len(blocks) and str(blocks[i + 1].get("block_id") or "") in exhibit_ids:
                    adj_shown = True
                if i > 0 and str(blocks[i - 1].get("block_id") or "") in exhibit_ids:
                    adj_shown = True
                if not adj_shown:
                    skip_block_ids.add(bid)
                continue
            if _ref_keys(b) & skipped_refs:
                skip_block_ids.add(bid)
                continue
            if i + 1 < len(blocks):
                nxt = blocks[i + 1]
                if _obj_of(nxt, roles) in ("table", "image") and str(nxt.get("block_id") or "") in skipped_media_ids:
                    skip_block_ids.add(bid)
                    continue
            if i > 0:
                prev = blocks[i - 1]
                if _obj_of(prev, roles) in ("table", "image") and str(prev.get("block_id") or "") in skipped_media_ids:
                    skip_block_ids.add(bid)
                    continue

    return {
        "exhibit_block_ids": exhibit_ids,
        "skip_block_ids": skip_block_ids,
        "skipped_refs": skipped_refs,
        "per_style": per_style,
        "max_styles": None,
        "allowed_table_styles": sorted(allow_tbl),
        "allowed_image_styles": sorted(allow_img),
        "counts": counts,
    }


def should_skip_block(block: dict, plan_info: dict[str, Any] | None, roles: dict | None = None) -> bool:
    """Return True only for non-exhibited tables/images or their captions.

    Body paragraphs, headings, and section structure are never skipped here.
    """
    if not plan_info:
        return False
    bid = str(block.get("block_id") or "")
    if not bid or bid not in (plan_info.get("skip_block_ids") or set()):
        return False
    # Safety: never skip non-caption paragraphs even if mis-listed
    rid = str(block.get("role_id") or "")
    obj = ""
    if roles is not None:
        obj = str((roles.get(rid) or {}).get("object") or "")
    if obj == "paragraph" and not is_caption_role(rid):
        return False
    if obj == "section":
        return False
    return True
