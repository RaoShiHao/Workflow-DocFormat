"""Resolve outline heading levels from several signals (not just ParaHeadingN).

Priority per block (first hit wins for the assigned level, all hits are recorded):
1. Word ``outlineLvl`` on the style/role_params if already filled (0 → H1)
2. Numbering in ``main_point``: 第一章 / Chapter 1 / 1.1 / 1.2.1
3. Ordinal labels: 一级/二级/三级, 章标题/节标题, organization_note, stylesheet description,
   role-library discriminators
4. role_id / display_name aliases (ParaHeading2, ParaChapterTitle, ParaSectionHeading, …)

Captions (表题/图题) are never headings. Cover/TOC titles may show as H1 in the tree
but do **not** count toward document heading_depth unless they carry outline_level.
"""
from __future__ import annotations

import re
from typing import Any


HEADING_RANK_EXACT: dict[str, int] = {
    "ParaHeading1": 1,
    "ParaHeading2": 2,
    "ParaHeading3": 3,
    "ParaHeading4": 4,
    "ParaHeading5": 5,
    "ParaChapterHeading": 1,
    "ParaSectionHeading": 2,
    "ParaSubsectionHeading": 3,
    "ParaSubsubsectionHeading": 4,
}

_SKIP = re.compile(
    r"(?i)("
    r"table.{0,3}title|figure.{0,3}title|tbl.{0,8}title|img.{0,8}title|"
    r"caption|题注|"
    r"toc.{0,3}entry|toc.{0,3}item|目录条目"
    r")"
)

_COVER_OR_TOC = re.compile(
    r"(?i)cover.{0,8}(title|heading)|titlepage|封面题名|封面标题|toc.{0,8}title|目录标题"
)

_NUMBERED_NAME = re.compile(
    r"(?i)(?:heading|标题)\s*([1-5])(?!\d)|(?:^|[^A-Za-z0-9])H([1-5])(?!\d)"
)

_SEMANTIC: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?i)sub[-_]?sub[-_]?section|subsubsection|四级标题"), 4),
    (re.compile(r"(?i)sub[-_]?section|subsection|三级标题|小节标题"), 3),
    (re.compile(r"(?i)(?<!sub)section[-_]?(title|heading)|二级标题|(?<!小)节标题"), 2),
    (re.compile(r"(?i)chapter[-_]?(title|heading)|一级标题|章标题"), 1),
    (re.compile(r"(?i)cover[-_]?(title|heading)|titlepage[-_]?title|封面题名|封面标题"), 1),
]

_ORDINAL_LABEL: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"四级|第四级"), 4),
    (re.compile(r"三级|第三级"), 3),
    (re.compile(r"二级|第二级"), 2),
    (re.compile(r"一级|第一级"), 1),
]

_CHAPTER_OPEN = re.compile(
    r"(?i)^(第[一二三四五六七八九十百千0-9]+章|Chapter\s+\d+)\b"
)
_DOTTED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})(?=\s|[:：]|$)")


def numbering_level(text: str) -> int | None:
    """Heading depth from visible numbering in the block text (第一章 / 1.1 / 1.2.1)."""
    t = str(text or "").strip()
    if not t:
        return None
    if _CHAPTER_OPEN.match(t):
        return 1
    m = _DOTTED.match(t)
    if not m:
        return None
    parts = m.group(1).split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    first = int(parts[0])
    if first < 1 or first > 40:
        return None
    return min(len(parts), 5)  # 1.1 → H2; 1.2.1 → H3


def _ordinal_from_text(text: str) -> int | None:
    t = str(text or "").strip()
    if not t:
        return None
    for pat, lvl in _ORDINAL_LABEL:
        if pat.search(t):
            return lvl
    # Bare display names (avoid scanning long expectation sentences that mention 章标题)
    if t in ("章标题", "章題", "Chapter heading", "Chapter title"):
        return 1
    if t in ("节标题", "節標題", "Section heading", "Section title"):
        return 2
    if t in ("小节标题", "小節標題", "Subsection heading"):
        return 3
    return None


def heading_level(role_id: str, display: str = "") -> int | None:
    """Return 1..5 from role_id / display_name only (used by outline_tree)."""
    rid = str(role_id or "").strip()
    dn = str(display or "").strip()
    if not rid and not dn:
        return None
    if rid in HEADING_RANK_EXACT:
        return HEADING_RANK_EXACT[rid]
    if rid and _SKIP.search(rid):
        return None
    for text in (rid, dn):
        if not text:
            continue
        m = _NUMBERED_NAME.search(text)
        if m:
            return int(m.group(1) or m.group(2))
        for pat, lvl in _SEMANTIC:
            if pat.search(text):
                return lvl
    return None


def _stylesheet_records(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    recs: dict[str, dict[str, Any]] = {}
    ss = plan.get("stylesheet")
    items: list[Any] = []
    if isinstance(ss, list):
        items = ss
    elif isinstance(ss, dict):
        for bucket in ss.values():
            if isinstance(bucket, list):
                items.extend(bucket)
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or item.get("id") or "")
        if sid:
            recs[sid] = item
    return recs


def _index_library_roles(library: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(library, dict):
        return out

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            rid = node.get("role_id")
            if rid:
                out[str(rid)] = node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(library)
    return out


def _role_params_outline_lvl(plan: dict[str, Any], role_id: str) -> int | None:
    """Word outlineLvl 0..8 → heading rank 1..9, if step4 params already sit on the plan."""
    blobs: list[Any] = [plan.get("role_params"), plan.get("params")]
    ss = plan.get("stylesheet")
    if isinstance(ss, list):
        blobs.extend(ss)
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        spec = blob.get(role_id) if role_id in blob else None
        if not isinstance(spec, dict):
            # stylesheet item
            if str(blob.get("style_id") or "") == role_id:
                spec = blob
            else:
                continue
        props = spec.get("props") if isinstance(spec.get("props"), dict) else spec
        ol = props.get("outlineLvl") if isinstance(props, dict) else None
        if ol is not None and str(ol).isdigit():
            lvl = int(ol)
            if 0 <= lvl <= 8:
                return lvl + 1
    return None


def _has_outline_focus(lib_node: dict[str, Any] | None) -> bool:
    if not isinstance(lib_node, dict):
        return False
    focus = lib_node.get("format_focus") or []
    if isinstance(focus, str):
        focus = [focus]
    return any(str(x).lower() in ("outline_level", "outlinelvl") for x in focus)


def _is_cover_or_toc(role_id: str, display: str = "") -> bool:
    text = f"{role_id} {display}"
    return bool(_COVER_OR_TOC.search(text))


def resolve_block_heading(
    block: dict[str, Any],
    *,
    style: dict[str, Any] | None = None,
    lib_node: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return {level, sources, counts_toward_depth} or None if not a heading."""
    rid = str(block.get("role_id") or "")
    if not rid or _SKIP.search(rid):
        return None
    style = style or {}
    lib_node = lib_node or {}
    dn = str(style.get("display_name") or block.get("display_name") or lib_node.get("display_name") or "")
    desc = str(style.get("description") or lib_node.get("discriminators") or "")
    org = str(block.get("organization_note") or "")
    point = str(block.get("main_point") or "")

    sources: dict[str, int] = {}
    ol = _role_params_outline_lvl(plan or {}, rid)
    if ol:
        sources["outlineLvl"] = ol
    for label, text in (
        ("organization_note", org),
        ("stylesheet_description", desc),
        ("display_name", dn),
    ):
        ord_ = _ordinal_from_text(text)
        if ord_:
            sources[label] = ord_
    alias = heading_level(rid, dn)
    if alias:
        sources["role_id"] = alias
    is_outline_role = _has_outline_focus(lib_node)
    # Numbering only ranks blocks already looking like headings (avoid "3.2 亿元" in body).
    heading_candidate = bool(alias or is_outline_role or sources)
    num = numbering_level(point) if heading_candidate else None
    if num:
        sources["numbering"] = num
    if not sources and not is_outline_role:
        return None

    # Prefer content numbering, then Word outlineLvl, then labels, then role_id.
    level = (
        sources.get("numbering")
        or sources.get("outlineLvl")
        or sources.get("organization_note")
        or sources.get("stylesheet_description")
        or sources.get("display_name")
        or sources.get("role_id")
    )
    if not level and is_outline_role:
        # Known heading role but no rank yet — still a heading; don't guess a number.
        return {
            "level": None,
            "sources": sources,
            "counts_toward_depth": True,
            "unranked_outline_role": True,
        }

    cover = _is_cover_or_toc(rid, dn)
    counts = bool(is_outline_role or (not cover and alias and alias >= 1 and "cover" not in rid.lower()))
    if cover and not is_outline_role:
        counts = False
    return {
        "level": int(level) if level else None,
        "sources": sources,
        "counts_toward_depth": counts,
    }


def collect_heading_hits(
    plan: dict[str, Any],
    *,
    library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan outline blocks; return depth, per-level counts, per-role breakdown, signals used."""
    styles = _stylesheet_records(plan)
    lib_idx = _index_library_roles(library)
    counts = {f"h{i}": 0 for i in range(1, 6)}
    by_role: dict[str, dict[str, Any]] = {}
    depth = 0
    n_heading_blocks = 0
    n_unranked = 0
    signals_used: set[str] = set()
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            rid = str(b.get("role_id") or "")
            if not rid:
                continue
            hit = resolve_block_heading(
                b,
                style=styles.get(rid),
                lib_node=lib_idx.get(rid),
                plan=plan,
            )
            if not hit:
                continue
            n_heading_blocks += 1
            signals_used.update(hit.get("sources") or {})
            lvl = hit.get("level")
            if not lvl:
                n_unranked += 1
                rec = by_role.setdefault(
                    rid,
                    {
                        "level": None,
                        "count": 0,
                        "display_name": str((styles.get(rid) or {}).get("display_name") or rid),
                        "counts_toward_depth": hit.get("counts_toward_depth"),
                        "sources_seen": {},
                    },
                )
                rec["count"] += 1
                continue
            if hit.get("counts_toward_depth"):
                depth = max(depth, int(lvl))
                if 1 <= int(lvl) <= 5:
                    counts[f"h{int(lvl)}"] += 1
            rec = by_role.setdefault(
                rid,
                {
                    "level": int(lvl),
                    "count": 0,
                    "display_name": str((styles.get(rid) or {}).get("display_name") or rid),
                    "counts_toward_depth": hit.get("counts_toward_depth"),
                    "sources_seen": {},
                },
            )
            rec["count"] += 1
            rec["level"] = int(lvl)
            seen = rec.setdefault("sources_seen", {})
            for k, v in (hit.get("sources") or {}).items():
                seen[k] = v
    return {
        "depth": depth,
        "counts": counts,
        "by_role": by_role,
        "n_heading_blocks": n_heading_blocks,
        "n_unranked": n_unranked,
        "signals_used": sorted(signals_used),
        "method": (
            "multi-signal: numbering(第一章/1.1/1.2.1) + outlineLvl + "
            "一级/二级/章标题 labels + role_id aliases; "
            "cover/TOC titles excluded from depth unless format_focus has outline_level"
        ),
    }
