"""Writing outline (薄树) parse / check / compact render.

Step3 phase A emits a short heading tree. Checks:
  - JSON/tree parse
  - heading_depth vs structure_brief
  - no only-child heading parents (open a deeper level ⇒ ≥2 siblings)

Later phases must keep the heading signature frozen:
  3b stylesheet (type catalog)
  3c1 text/section placement (Para*/Run*/Sec* → nodes / slots)
  3c2 media placement (Tbl*/Img* → outline node_id)
  3d blocks per H1 / front / back chunk
  3e trim stylesheet to styles actually used on blocks
"""
from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from LongDocFmtEval.common.role_library import _disc, _esc_cell, rid
from LongDocFmtEval.pipeline_auto.seed.caption_bind import is_caption_role
from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index
from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level as detect_heading_level

_FRONT_KINDS = frozenset({
    "front", "cover", "toc", "contents", "letter", "back", "annex_part",
})
_BODY_WRAPPER_KINDS = frozenset({"body", "root", "document", "main", ""})
_BACK_KINDS = frozenset({"back", "annex", "annex_part", "appendix", "colophon", "afterword"})
_KIND_SECTION_HINTS: dict[str, tuple[str, ...]] = {
    "cover": ("SecCover",),
    "toc": ("SecToc", "SecContents", "SecDirectory"),
    "contents": ("SecToc", "SecContents"),
    "letter": ("SecLetter", "SecPreface", "SecFormalHead"),
    "front": ("SecCover", "SecFront"),
    "h1": ("SecChapter", "SecBody", "SecMain", "SecSection"),
    "back": ("SecAnnex", "SecAppendix", "SecBack"),
    "annex": ("SecAnnex", "SecAppendix"),
    "appendix": ("SecAppendix", "SecAnnex"),
}


def _where_used(n: dict[str, Any]) -> str:
    """Discriminators plus light planning tags (family / heading / kind). No format YAML."""
    parts: list[str] = []
    disc = _disc(n)
    if disc:
        parts.append(disc)
    extra: list[str] = []
    fam = n.get("section_family")
    if fam:
        extra.append(str(fam))
    hl = n.get("heading_level")
    if hl not in (None, ""):
        extra.append(f"H{hl}")
    kind = n.get("image_kind")
    if kind:
        extra.append(str(kind))
    tl = n.get("table_layout")
    if isinstance(tl, dict):
        pat = str(tl.get("pattern") or "").strip()
        if pat:
            extra.append(pat[:40])
    if extra:
        parts.append(" / ".join(extra))
    return "；".join(parts)


def render_role_inventory_compact(data: dict[str, Any]) -> str:
    """Complete Role list in inventory.md spirit: name + where-used, no format YAML."""
    roles = data.get("roles") or {}
    para = roles.get("paragraph") or {}
    lines: list[str] = [
        f"# {(data.get('display_name') or data.get('genre') or 'Role')} — 简易 Role 清单",
        "",
        "完整列表（role_id + 名称 + 用在何处）。**abstract 基类不可直接当 style / 树节点。**",
        "规划与大纲只引用真实 role_id；不要填 pt/cm/色值。",
        "",
    ]

    def table(title: str, nodes: list, extra: str = "") -> None:
        lines.append(f"## {title}")
        lines.append("")
        if extra:
            lines.append(extra)
            lines.append("")
        lines.append("| role_id | display_name | parent | abstract | 用在何处 |")
        lines.append("|---|---|---|---|---|")
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            lines.append(
                "| {rid} | {dn} | {parent} | {ab} | {disc} |".format(
                    rid=_esc_cell(rid(n)),
                    dn=_esc_cell(n.get("display_name")),
                    parent=_esc_cell(n.get("parent") or "—"),
                    ab="✓" if n.get("abstract") else "",
                    disc=_esc_cell(_where_used(n)),
                )
            )
        lines.append("")

    table("section（节）", roles.get("section") or [])
    table("paragraph.body（正文/标题/题注）", para.get("body") or [])
    table("paragraph.table_cell（格内文字）", para.get("table_cell") or [])

    lines.append("## paragraph_runs（段内片段）")
    lines.append("")
    lines.append("| role_id | display_name | 附着段落 | 用在何处 |")
    lines.append("|---|---|---|---|")
    for n in roles.get("paragraph_runs") or []:
        if not isinstance(n, dict):
            continue
        lines.append(
            "| {rid} | {dn} | {at} | {disc} |".format(
                rid=_esc_cell(rid(n)),
                dn=_esc_cell(n.get("display_name") or n.get("span_role")),
                at=_esc_cell(", ".join(n.get("attaches_to") or [])),
                disc=_esc_cell(_where_used(n) or ", ".join(n.get("format_focus") or [])),
            )
        )
    lines.append("")
    table("table（表）", roles.get("table") or [], extra="只需知道有哪些表种；不要展开格子或写边框参数。")
    table("image（图）", roles.get("image") or [], extra="只需知道有哪些图种；不要写宽高。")
    return "\n".join(lines).strip() + "\n"


def _as_nodes(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [n for n in raw if isinstance(n, dict)]
    if isinstance(raw, dict):
        inner = raw.get("children") or raw.get("parts") or raw.get("nodes") or raw.get("tree")
        if isinstance(inner, list):
            return [n for n in inner if isinstance(n, dict)]
    return []


def extract_tree_roots(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("parts", "tree", "nodes", "outline"):
        roots = _as_nodes(data.get(key))
        if roots:
            return roots
    return []


def parse_writing_outline(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:
                s = part.strip()
                if s[:4].lower() == "json":
                    s = s[4:].strip()
                if s.startswith("{"):
                    t = s
                    break
    brace = t.find("{")
    if brace < 0:
        raise ValueError("写作大纲：输出中没有 JSON 对象")
    try:
        obj, _end = json.JSONDecoder().raw_decode(t[brace:])
    except json.JSONDecodeError as e:
        raise ValueError(f"写作大纲 JSON 语法错误: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("写作大纲根节点必须是对象")
    roots = extract_tree_roots(obj)
    if not roots:
        raise ValueError("写作大纲缺少非空 parts/tree（树形节点列表）")
    _validate_nodes(roots, path="$")
    obj["_roots"] = roots
    return obj


def _validate_nodes(nodes: list[dict[str, Any]], *, path: str) -> None:
    for i, n in enumerate(nodes):
        here = f"{path}/{n.get('id') or i}"
        title = str(n.get("title") or n.get("name") or "").strip()
        if not title:
            raise ValueError(f"写作大纲节点缺 title: {here}")
        kids = n.get("children")
        if kids is None:
            n["children"] = []
            continue
        if not isinstance(kids, list):
            raise ValueError(f"写作大纲 children 必须是数组: {here}")
        bad = [c for c in kids if not isinstance(c, dict)]
        if bad:
            raise ValueError(f"写作大纲 children 含非对象: {here}")
        _validate_nodes([c for c in kids if isinstance(c, dict)], path=here)


def _node_level(n: dict[str, Any]) -> int | None:
    kind = str(n.get("kind") or n.get("type") or "").strip().lower()
    if kind in _FRONT_KINDS:
        return None
    raw = n.get("heading_level")
    if raw in (None, "", False):
        if kind in {"h1", "h2", "h3", "h4"}:
            try:
                return int(kind[1])
            except ValueError:
                return None
        return None
    try:
        lvl = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= lvl <= 6:
        return lvl
    return None


def walk_heading_nodes(
    roots: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int, list[dict[str, Any]]]]:
    """Yield (node, heading_level, heading_children) in preorder."""
    out: list[tuple[dict[str, Any], int, list[dict[str, Any]]]] = []

    def rec(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
            lvl = _node_level(n)
            heading_kids = [c for c in kids if _node_level(c)]
            if lvl:
                out.append((n, lvl, heading_kids))
            rec(kids)

    rec(roots)
    return out


def _heading_children(n: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in (n.get("children") or []) if isinstance(c, dict) and _node_level(c)]


def flatten_only_child_headings(data: dict[str, Any]) -> list[str]:
    """Deterministic fix: drop a unique heading child, or promote its ≥2 kids.

    Matches the check hint「删掉该独生层」. Does not invent sibling titles.
    Mutates the tree in place. Repeat from the leaves up until none remain.
    """
    roots = data.get("_roots") or extract_tree_roots(data)
    notes: list[str] = []

    def _set_level(node: dict[str, Any], lvl: int) -> None:
        node["heading_level"] = lvl
        node["kind"] = f"h{lvl}"

    def _splice(parent: dict[str, Any]) -> bool:
        hk = _heading_children(parent)
        if len(hk) != 1:
            return False
        child = hk[0]
        kids = parent.setdefault("children", [])
        try:
            idx = next(i for i, x in enumerate(kids) if x is child)
        except StopIteration:
            return False
        ghk = _heading_children(child)
        child_lvl = _node_level(child)
        child_title = str(child.get("title") or "").strip()
        parent_title = str(parent.get("title") or "").strip()
        if len(ghk) >= 2 and child_lvl:
            promoted: list[dict[str, Any]] = []
            for g in list(child.get("children") or []):
                if not isinstance(g, dict):
                    continue
                if _node_level(g):
                    _set_level(g, child_lvl)
                promoted.append(g)
            kids[idx:idx + 1] = promoted
            notes.append(
                f"拍平「{parent_title}」的独生层「{child_title}」→ "
                f"{len(ghk)} 个标题升为 H{child_lvl} 并列"
            )
            return True
        extra = [c for c in (child.get("children") or []) if isinstance(c, dict)]
        kids[idx:idx + 1] = extra
        pur = str(parent.get("purpose") or "").strip()
        bit = str(child.get("purpose") or child_title).strip()
        if bit:
            parent["purpose"] = (pur + "；" if pur else "") + f"原「{child_title}」: {bit}"
        notes.append(f"拍平「{parent_title}」的独生叶「{child_title}」（内容留在父级）")
        return True

    def rec(node: dict[str, Any]) -> None:
        for c in list(node.get("children") or []):
            if isinstance(c, dict):
                rec(c)
        if _node_level(node):
            while _splice(node):
                pass

    for r in roots:
        rec(r)
    return notes


def _node_stub(n: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": n.get("id"),
        "title": n.get("title"),
        "kind": n.get("kind"),
        "heading_level": n.get("heading_level"),
        "purpose": n.get("purpose") or "",
    }


def _node_dump(n: dict[str, Any]) -> dict[str, Any]:
    out = _node_stub(n)
    out["children"] = [
        _node_dump(c) for c in (n.get("children") or []) if isinstance(c, dict)
    ]
    return out


def collect_only_child_sites(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every heading with exactly one heading child, with H1 spine for local repair."""
    roots = data.get("_roots") or extract_tree_roots(data)
    sites: list[dict[str, Any]] = []

    def rec(nodes: list[dict[str, Any]], ancestors: list[dict[str, Any]]) -> None:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            path = ancestors + [n]
            lvl = _node_level(n)
            hk = _heading_children(n)
            if lvl and len(hk) == 1:
                chapter = next((a for a in path if _node_level(a) == 1), n)
                parent_anc = ancestors[-1] if ancestors else None
                sibling_stubs: list[dict[str, Any]] = []
                if parent_anc is not None:
                    for s in _heading_children(parent_anc):
                        if s is n:
                            continue
                        sibling_stubs.append(_node_stub(s))
                spine = [_node_stub(a) for a in path if a is not n]
                sites.append({
                    "parent_id": str(n.get("id") or ""),
                    "parent": n,
                    "issue": (
                        f"{n.get('title')} (H{lvl}) → 唯一子标题「{hk[0].get('title')}」"
                    ),
                    "chapter": chapter,
                    "chapter_id": str(chapter.get("id") or ""),
                    "spine": spine,
                    "frozen_siblings": sibling_stubs,
                })
            rec(
                [c for c in (n.get("children") or []) if isinstance(c, dict)],
                path,
            )

    rec(roots, [])
    return sites


def build_only_child_repair_payload(sites: list[dict[str, Any]]) -> dict[str, Any]:
    """Group sites by H1 chapter: frozen spine + editable cuts only."""
    chapters: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for s in sites:
        cid = s.get("chapter_id") or "_root"
        if cid not in chapters:
            order.append(cid)
            ch = s.get("chapter") if isinstance(s.get("chapter"), dict) else {}
            chapters[cid] = {
                "chapter": _node_stub(ch) if ch else {"id": cid, "title": ""},
                "repairs": [],
            }
        chapters[cid]["repairs"].append({
            "issue": s.get("issue"),
            "replace_id": s.get("parent_id"),
            "spine": s.get("spine") or [],
            "frozen_siblings": s.get("frozen_siblings") or [],
            "editable": _node_dump(s["parent"]) if isinstance(s.get("parent"), dict) else {},
        })
    return {
        "instruction": (
            "只改 repairs[].editable；spine 与 frozen_siblings 只读。"
            "每个 replace_id 输出一棵修补后的节点，按原 id 插回。"
        ),
        "chapters": [chapters[k] for k in order],
    }


def parse_repair_replacements(text: str) -> list[dict[str, Any]]:
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:
                s = part.strip()
                if s[:4].lower() == "json":
                    s = s[4:].strip()
                if s.startswith("{") or s.startswith("["):
                    t = s
                    break
    brace = t.find("{")
    bracket = t.find("[")
    start = brace
    if bracket >= 0 and (brace < 0 or bracket < brace):
        start = bracket
    if start < 0:
        raise ValueError("独生子修补：输出中没有 JSON")
    try:
        obj, _end = json.JSONDecoder().raw_decode(t[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"独生子修补 JSON 语法错误: {e}") from e
    raw: list[Any]
    if isinstance(obj, list):
        raw = obj
    elif isinstance(obj, dict):
        raw = obj.get("replacements") or obj.get("nodes") or []
        if not isinstance(raw, list):
            raw = []
        if isinstance(obj.get("chapters"), list):
            extra: list[Any] = []
            for ch in obj["chapters"]:
                if isinstance(ch, dict):
                    extra.extend(ch.get("replacements") or ch.get("repairs") or [])
            if extra:
                raw = list(raw) + extra
        if not raw and obj.get("id") and "children" in obj:
            raw = [obj]
    else:
        raise ValueError("独生子修补根节点必须是对象或数组")
    out: list[dict[str, Any]] = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        # allow {replace_id, editable: node}
        if isinstance(n.get("editable"), dict):
            node = dict(n["editable"])
            node["id"] = n.get("replace_id") or n.get("id") or node.get("id")
            n = node
        nid = str(n.get("id") or n.get("replace_id") or "").strip()
        if not nid:
            continue
        n["id"] = nid
        if not isinstance(n.get("children"), list):
            n["children"] = []
        out.append(n)
    if not out:
        raise ValueError("独生子修补：没有带 id 的 replacements")
    return out


def splice_outline_replacements(
    data: dict[str, Any],
    replacements: list[dict[str, Any]],
    *,
    allowed_ids: set[str],
) -> list[str]:
    """Replace nodes by id. Refuse ids outside the cut set or still-only-child trees."""
    roots = data.get("_roots") or extract_tree_roots(data)
    notes: list[str] = []
    allowed = {str(x) for x in allowed_ids if x}

    def replace(nodes: list[Any], nid: str, new: dict[str, Any]) -> bool:
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                continue
            if str(n.get("id") or "") == nid:
                if new.get("heading_level") in (None, "") and n.get("heading_level") not in (None, ""):
                    new["heading_level"] = n.get("heading_level")
                if not new.get("kind"):
                    new["kind"] = n.get("kind")
                nodes[i] = new
                return True
            kids = n.get("children")
            if isinstance(kids, list) and replace(kids, nid, new):
                return True
        return False

    for new in replacements:
        if not isinstance(new, dict):
            continue
        nid = str(new.get("id") or "").strip()
        if nid not in allowed:
            notes.append(f"忽略非切口 id={nid}")
            continue
        node = deepcopy(new)
        if len(_heading_children(node)) == 1:
            notes.append(f"拒绝插回 {nid}：修补后仍是独生子")
            continue
        if replace(roots, nid, node):
            title = str(node.get("title") or "")
            n_h = len(_heading_children(node))
            notes.append(f"插回 {nid}「{title}」（标题子节点 {n_h} 个）")
        else:
            notes.append(f"树上找不到 id={nid}，未插回")
    data["_roots"] = roots
    return notes


def heading_signature(data: dict[str, Any]) -> list[tuple[int, str]]:
    roots = data.get("_roots") or extract_tree_roots(data)
    sig: list[tuple[int, str]] = []
    for n, lvl, _kids in walk_heading_nodes(roots):
        title = str(n.get("title") or "").strip()
        sig.append((lvl, title))
    return sig


def heading_signature_from_plan(plan: dict[str, Any]) -> list[tuple[int, str]]:
    idx = heading_index(plan)
    sig: list[tuple[int, str]] = []
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            rid_ = str(b.get("role_id") or "")
            rec = idx.get(rid_) or {}
            hl = rec.get("heading_level")
            if hl is None and not rec.get("title_kind"):
                hl = detect_heading_level(rid_, rid_)
            if not hl:
                continue
            title = str(b.get("main_point") or b.get("title") or "").strip()
            sig.append((int(hl), title))
    return sig


def signatures_compatible(
    frozen: list[tuple[int, str]],
    planned: list[tuple[int, str]],
) -> tuple[bool, str]:
    """Same heading-level sequence (titles may be numbered/paraphrased)."""
    if [a for a, _t in frozen] != [a for a, _t in planned]:
        return False, (
            f"标题层级序列被改写：写作大纲 { [a for a, _t in frozen] } "
            f"vs 写作规划 { [a for a, _t in planned] }。"
            "禁止增删/重排标题节点，只能在已有标题下挂正文/表/图块。"
        )
    return True, ""


def check_writing_outline(
    data: dict[str, Any],
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    roots = data.get("_roots") or extract_tree_roots(data)
    walked = walk_heading_nodes(roots)
    levels = sorted({lvl for _n, lvl, _k in walked})
    used_depth = max(levels) if levels else 0
    rec = (brief or {}).get("recommend") or {}
    want = rec.get("heading_depth")
    try:
        want_i = int(want) if want not in (None, "") else None
    except (TypeError, ValueError):
        want_i = None

    only_child: list[str] = []
    for n, lvl, heading_kids in walked:
        if len(heading_kids) == 1:
            child = heading_kids[0]
            only_child.append(
                f"{n.get('title')} (H{lvl}) → 唯一子标题「{child.get('title')}」"
            )

    problems: list[str] = []
    if not walked:
        problems.append("树中没有带 heading_level 的正文标题（H1–H6）")
    if want_i and used_depth and used_depth < want_i:
        problems.append(
            f"最大标题深度={used_depth}，低于 brief 建议 {want_i}。"
            f"请在**已经成对的**某父级下再开 ≥2 个同级更深标题；禁止只挂一个 H{want_i} 凑深度。"
        )
    if want_i and used_depth and levels and any(d not in levels for d in range(1, used_depth + 1)):
        missing = [d for d in range(1, used_depth + 1) if d not in levels]
        problems.append(f"标题层级有缺口：缺 H{missing}")
    if only_child:
        problems.append(
            "存在独生子标题（开了下一层却只有 1 个子标题）。"
            f"共 {len(only_child)} 处，须全部修完："
            + "；".join(only_child)
        )

    ok = not problems
    return {
        "ok": ok,
        "used_depth": used_depth or None,
        "recommend_heading_depth": want_i,
        "levels_present": levels,
        "n_heading_nodes": len(walked),
        "only_child_parents": only_child,
        "problems": problems,
        "signature": heading_signature(data),
    }


def format_check_feedback(report: dict[str, Any], *, locale: str = "zh") -> str:
    probs = report.get("problems") or []
    if not probs:
        return ""
    only = report.get("only_child_parents") or []
    if str(locale).lower().startswith("en"):
        lines = [
            "The writing outline failed structure checks. Fix the TREE only; do not add styles.",
            f"Used depth={report.get('used_depth')} target={report.get('recommend_heading_depth')}.",
        ]
        lines.extend(f"- {p}" for p in probs)
        if only:
            lines.append(f"All {len(only)} only-child sites (do not drop any):")
            lines.extend(f"  · {x}" for x in only)
        lines.append(
            "If you open a deeper heading under a parent, give ≥2 siblings at that level. "
            "Do not pad depth with a single H3/H4."
        )
        return "\n".join(lines)
    lines = [
        "写作大纲未通过结构检查。只改树，不要写 stylesheet / 正文块。",
        f"当前最大深度={report.get('used_depth')}，目标={report.get('recommend_heading_depth')}。",
    ]
    lines.extend(f"- {p}" for p in probs)
    if only:
        lines.append(f"全部 {len(only)} 处独生子（不得漏修）：")
        lines.extend(f"  · {x}" for x in only)
    lines.append("开下一层时同级子标题必须 ≥2；禁止用一个独生 H3/H4 凑 heading_depth。")
    return "\n".join(lines)


def heading_counts(data: dict[str, Any] | None = None, *, roots: list[dict[str, Any]] | None = None) -> dict[int, int]:
    src = roots
    if src is None:
        src = (data or {}).get("_roots") or extract_tree_roots(data or {})
    counts: dict[int, int] = {}
    for _n, lvl, _kids in walk_heading_nodes(src):
        counts[lvl] = counts.get(lvl, 0) + 1
    return counts


def format_heading_counts_line(counts: dict[int, int]) -> str:
    if not counts:
        return "H1–H6: 0"
    parts = [f"H{k}={counts[k]}" for k in sorted(counts)]
    return "  ".join(parts) + f"  合计={sum(counts.values())}"


def format_writing_outline_compact(
    data: dict[str, Any],
    *,
    include_purpose: bool = True,
) -> str:
    """Short tree text for step5 / stylesheet / block prompts."""
    roots = data.get("_roots") or extract_tree_roots(data)
    lines: list[str] = []

    def rec(nodes: list[dict[str, Any]], indent: int) -> None:
        pad = "  " * indent
        for n in nodes:
            lvl = _node_level(n)
            title = str(n.get("title") or "").strip()
            kind = str(n.get("kind") or "").strip()
            if lvl:
                tag = f"H{lvl}"
            elif kind:
                tag = kind
            else:
                tag = "part"
            extra = ""
            if include_purpose:
                purpose = str(n.get("purpose") or "").strip()
                extra = f" — {purpose[:60]}" if purpose else ""
            lines.append(f"{pad}- [{tag}] {title}{extra}")
            rec([c for c in (n.get("children") or []) if isinstance(c, dict)], indent + 1)

    rec(roots, 0)
    return "\n".join(lines) if lines else "（空树）"


def format_writing_outline_toc(data: dict[str, Any]) -> str:
    """Numbered heading tree (no purpose). Front/back parts stay unnumbered."""
    roots = data.get("_roots") or extract_tree_roots(data)
    lines: list[str] = []
    counters = [0, 0, 0, 0, 0, 0]

    def rec(nodes: list[dict[str, Any]], indent: int) -> None:
        pad = "  " * indent
        for n in nodes:
            lvl = _node_level(n)
            title = str(n.get("title") or "").strip()
            kind = str(n.get("kind") or "").strip()
            if lvl:
                idx = lvl - 1
                counters[idx] += 1
                for j in range(idx + 1, len(counters)):
                    counters[j] = 0
                num = ".".join(str(counters[i]) for i in range(lvl) if counters[i])
                lines.append(f"{pad}{num}  {title}")
            else:
                tag = kind or "part"
                lines.append(f"{pad}[{tag}] {title}")
            rec([c for c in (n.get("children") or []) if isinstance(c, dict)], indent + 1)

    rec(roots, 0)
    return "\n".join(lines) if lines else "（空树）"


def format_writing_outline_md(data: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    counts = heading_counts(data)
    status = "PASS" if (report or {}).get("ok", True) else "FAIL"
    lines = [
        f"# 写作大纲 · {status}",
        "",
        f"- theme: {meta.get('theme') or ''}",
        f"- genre: {meta.get('genre') or ''}",
        f"- depth used: **{(report or {}).get('used_depth')}** "
        f"(brief={(report or {}).get('recommend_heading_depth')})",
        f"- headings: **{(report or {}).get('n_heading_nodes') or sum(counts.values())}**"
        f"  ({format_heading_counts_line(counts)})",
        "",
        "## 目录树（仅标题）",
        "",
        format_writing_outline_toc(data),
        "",
        "## 带写作目的（规划用）",
        "",
        format_writing_outline_compact(data, include_purpose=True),
        "",
    ]
    probs = (report or {}).get("problems") or []
    lines += ["## 检查", ""]
    if probs:
        lines.extend(f"- {p}" for p in probs)
    else:
        lines.append("- 通过（深度 + 无独生子）")
    lines.append("")
    return "\n".join(lines)


def write_writing_outline_artifacts(
    data: dict[str, Any],
    out_dir: Path,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = check_writing_outline(data, brief)
    dump = {k: v for k, v in data.items() if k != "_roots"}
    dump["check"] = {
        "ok": report.get("ok"),
        "used_depth": report.get("used_depth"),
        "recommend_heading_depth": report.get("recommend_heading_depth"),
        "only_child_parents": report.get("only_child_parents"),
        "problems": report.get("problems"),
    }
    (out_dir / "writing_outline.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "writing_outline.md").write_text(
        format_writing_outline_md(data, report), encoding="utf-8"
    )
    (out_dir / "writing_outline_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def load_writing_outline(path: Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    roots = extract_tree_roots(data)
    if not roots:
        return None
    data["_roots"] = roots
    return data


def _flatten_body_wrappers(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote H1 children out of a single body/root bag so chunking splits by chapter."""
    out: list[dict[str, Any]] = []
    for n in nodes:
        kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        kind = str(n.get("kind") or n.get("type") or "").strip().lower()
        if not _node_level(n) and kind in _BODY_WRAPPER_KINDS and any(_node_level(c) == 1 for c in kids):
            out.extend(_flatten_body_wrappers(kids))
            continue
        out.append(n)
    return out


def split_outline_chunks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One Word-section chunk per front/back part and per H1 subtree."""
    roots = _flatten_body_wrappers(data.get("_roots") or extract_tree_roots(data))
    chunks: list[dict[str, Any]] = []
    seen_h1 = False
    seq = 0
    for n in roots:
        seq += 1
        lvl = _node_level(n)
        kind = str(n.get("kind") or n.get("type") or "").strip().lower()
        if lvl == 1:
            seen_h1 = True
            kind_tag = "h1"
        elif lvl:
            seen_h1 = True
            kind_tag = f"h{lvl}"
        elif seen_h1 or kind in _BACK_KINDS:
            kind_tag = kind or "back"
        else:
            kind_tag = kind or "front"
        chunks.append({
            "instance_id": f"I{seq:02d}",
            "kind": kind_tag,
            "title": str(n.get("title") or "").strip(),
            "purpose": str(n.get("purpose") or "").strip(),
            "node_id": n.get("id"),
            "root": n,
            "heading_signature": heading_signature({"_roots": [n]}),
        })
    return chunks


def format_chunk_slots(chunks: list[dict[str, Any]]) -> str:
    lines = ["instance_id | kind | title | headings"]
    for ch in chunks:
        n_h = len(ch.get("heading_signature") or [])
        lines.append(
            f"{ch.get('instance_id')} | {ch.get('kind')} | {ch.get('title')} | {n_h}"
        )
    return "\n".join(lines)


def format_chunk_tree(chunk: dict[str, Any], *, include_purpose: bool = True) -> str:
    root = chunk.get("root")
    if not isinstance(root, dict):
        return "（空块）"
    return format_writing_outline_compact({"_roots": [root]}, include_purpose=include_purpose)


def stylesheet_style_ids(stylesheet: Any) -> set[str]:
    out: set[str] = set()
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or item.get("id") or "").strip()
        if sid:
            out.add(sid)
    return out


def format_stylesheet_catalog(stylesheet: Any) -> str:
    """Closed style list for the block pass (no full Role YAML)."""
    lines = [
        "| style_id | object | heading/title | expectation |",
        "|---|---|---|---|",
    ]
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        obj = str(item.get("object") or "").strip()
        hl = item.get("heading_level")
        tk = item.get("title_kind")
        mark = f"H{hl}" if hl not in (None, "") else (str(tk) if tk else "—")
        exp = str(item.get("expectation") or item.get("description") or "").replace("\n", " ")[:80]
        lines.append(f"| {sid} | {obj} | {mark} | {exp} |")
    return "\n".join(lines) if len(lines) > 2 else "（无 stylesheet）"


def collect_outline_node_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """id → node for the frozen writing outline."""
    roots = data.get("_roots") or extract_tree_roots(data)
    out: dict[str, dict[str, Any]] = {}

    def rec(n: dict[str, Any]) -> None:
        nid = str(n.get("id") or "").strip()
        if nid:
            out[nid] = n
        for c in n.get("children") or []:
            if isinstance(c, dict):
                rec(c)

    for r in roots:
        if isinstance(r, dict):
            rec(r)
    return out


def format_writing_outline_with_ids(
    data: dict[str, Any],
    *,
    include_purpose: bool = True,
) -> str:
    """Compact tree with node ids for media placement (not used in step5)."""
    roots = data.get("_roots") or extract_tree_roots(data)
    lines: list[str] = []

    def rec(nodes: list[dict[str, Any]], indent: int) -> None:
        pad = "  " * indent
        for n in nodes:
            nid = str(n.get("id") or "").strip() or "?"
            lvl = _node_level(n)
            title = str(n.get("title") or "").strip()
            kind = str(n.get("kind") or "").strip()
            if lvl:
                tag = f"H{lvl}"
            elif kind:
                tag = kind
            else:
                tag = "part"
            extra = ""
            if include_purpose:
                purpose = str(n.get("purpose") or "").strip()
                extra = f" — {purpose[:80]}" if purpose else ""
            lines.append(f"{pad}- {nid} [{tag}] {title}{extra}")
            rec([c for c in (n.get("children") or []) if isinstance(c, dict)], indent + 1)

    rec(roots, 0)
    return "\n".join(lines) if lines else "（空树）"


def media_catalog_items(stylesheet: Any) -> list[dict[str, Any]]:
    """Tbl*/Img* rows from a frozen stylesheet (types only)."""
    rows: list[dict[str, Any]] = []
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        obj = str(item.get("object") or "").strip().lower()
        if obj not in ("table", "image") and not (
            sid.startswith("Tbl") or sid.startswith("Img")
        ):
            continue
        if obj not in ("table", "image"):
            obj = "table" if sid.startswith("Tbl") else "image"
        rows.append({
            "style_id": sid,
            "object": obj,
            "display_name": str(item.get("display_name") or sid),
            "description": str(item.get("description") or item.get("expectation") or ""),
        })
    return rows


def format_media_catalog(stylesheet: Any) -> str:
    rows = media_catalog_items(stylesheet)
    if not rows:
        return "（无 Tbl*/Img*）"
    lines = [
        "| style_id | object | reuse | display_name | description |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        desc = str(r.get("description") or "").replace("\n", " ")[:80]
        sid = str(r.get("style_id") or "")
        reuse = "once" if is_once_media(sid, desc) else "repeat"
        lines.append(
            f"| {sid} | {r['object']} | {reuse} | {r.get('display_name') or ''} | {desc} |"
        )
    return "\n".join(lines)


def _stylesheet_object(item: dict[str, Any]) -> str:
    sid = str(item.get("style_id") or item.get("role_id") or "").strip()
    obj = str(item.get("object") or "").strip().lower()
    if obj:
        return obj
    if sid.startswith("Sec"):
        return "section"
    if sid.startswith("Run") or sid.startswith("Span"):
        return "run"
    if sid.startswith("Tbl"):
        return "table"
    if sid.startswith("Img"):
        return "image"
    if "Cell" in sid:
        return "cell"
    return "paragraph"


def is_heading_catalog_style(item: dict[str, Any]) -> bool:
    if item.get("heading_level") not in (None, "", 0):
        return True
    tk = str(item.get("title_kind") or "").strip().lower()
    if tk in {"cover", "subtitle"}:
        return True
    sid = str(item.get("style_id") or "").strip()
    return "Heading" in sid


def is_cell_catalog_style(item: dict[str, Any]) -> bool:
    sid = str(item.get("style_id") or "").strip()
    obj = _stylesheet_object(item)
    if "cell" in obj:
        return True
    return bool(sid.startswith("ParaTbl") and "Cell" in sid) or sid.endswith("Cell")


def text_catalog_items(stylesheet: Any) -> list[dict[str, Any]]:
    """Para*/Run* that 3c1 must try to place (no Sec*/Tbl*/Img*/heading/cell/caption)."""
    rows: list[dict[str, Any]] = []
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        obj = _stylesheet_object(item)
        if obj in {"section", "table", "image"} or sid.startswith(("Sec", "Tbl", "Img")):
            continue
        if is_cell_catalog_style(item) or is_heading_catalog_style(item):
            continue
        if is_caption_role(sid):
            continue
        kind = "run" if (obj == "run" or sid.startswith("Run") or sid.startswith("Span")) else "paragraph"
        rows.append({
            "style_id": sid,
            "kind": kind,
            "object": obj or kind,
            "display_name": str(item.get("display_name") or sid),
            "description": str(item.get("description") or item.get("expectation") or ""),
        })
    return rows


def format_text_catalog(stylesheet: Any) -> str:
    rows = text_catalog_items(stylesheet)
    if not rows:
        return "（无待落位的正文/run；标题 style 按 heading_level 自动挂）"
    lines = [
        "| style_id | kind | display_name | description |",
        "|---|---|---|---|",
    ]
    for r in rows:
        desc = str(r.get("description") or "").replace("\n", " ")[:80]
        lines.append(
            f"| {r['style_id']} | {r['kind']} | {r.get('display_name') or ''} | {desc} |"
        )
    return "\n".join(lines)


def format_section_style_catalog(stylesheet: Any) -> str:
    lines = [
        "| style_id | display_name | description |",
        "|---|---|---|",
    ]
    n = 0
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or "").strip()
        if not sid:
            continue
        obj = _stylesheet_object(item)
        if obj != "section" and not sid.startswith("Sec"):
            continue
        n += 1
        desc = str(item.get("description") or item.get("expectation") or "").replace("\n", " ")[:80]
        lines.append(f"| {sid} | {item.get('display_name') or sid} | {desc} |")
    return "\n".join(lines) if n else "（无 Sec*）"


def normalize_text_placements(
    raw: Any,
    catalog: list[dict[str, Any]],
    node_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    allowed = {str(r.get("style_id") or "") for r in catalog}
    kind_of = {str(r.get("style_id") or ""): str(r.get("kind") or "paragraph") for r in catalog}
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    fallback = next(iter(node_index), "")
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("style_id") or row.get("role_id") or "").strip()
        nid = str(row.get("node_id") or "").strip()
        if not sid or sid not in allowed:
            continue
        if nid not in node_index:
            if fallback:
                notes.append(f"{sid} 的 node_id={nid or '∅'} 不在树上，改挂 {fallback}")
                nid = fallback
            else:
                continue
        key = (sid, nid)
        if key in seen:
            continue
        seen.add(key)
        cnt = row.get("count")
        count = cnt if isinstance(cnt, int) and cnt >= 1 else 1
        out.append({
            "style_id": sid,
            "node_id": nid,
            "count": min(count, 4),
            "kind": kind_of.get(sid) or "paragraph",
        })
    return out, notes


def normalize_section_roles(
    raw: Any,
    chunks: list[dict[str, Any]],
    stylesheet: Any,
) -> list[dict[str, Any]]:
    allowed = {
        str(it.get("style_id") or "")
        for it in (stylesheet or [])
        if isinstance(it, dict)
        and str(it.get("style_id") or "")
        and (
            _stylesheet_object(it) == "section"
            or str(it.get("style_id") or "").startswith("Sec")
        )
    }
    by_iid: dict[str, str] = {}
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("instance_id") or "").strip()
        rid_ = str(row.get("role_id") or row.get("style_id") or "").strip()
        if iid and rid_ in allowed:
            by_iid[iid] = rid_
    out: list[dict[str, Any]] = []
    for ch in chunks:
        iid = str(ch.get("instance_id") or "").strip()
        if not iid:
            continue
        role = by_iid.get(iid) or default_section_role(str(ch.get("kind") or ""), stylesheet)
        out.append({"instance_id": iid, "role_id": role})
    return out


def apply_section_roles_to_sheet(
    sheet: dict[str, Any],
    section_roles: list[dict[str, Any]],
) -> None:
    by_iid = {
        str(r.get("instance_id") or ""): str(r.get("role_id") or "")
        for r in section_roles
        if isinstance(r, dict) and r.get("instance_id") and r.get("role_id")
    }
    for sec in sheet.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        iid = str(sec.get("instance_id") or "")
        if iid in by_iid:
            sec["role_id"] = by_iid[iid]


def format_chunk_text_must(
    placements: list[dict[str, Any]],
    node_index: dict[str, dict],
    *,
    locale: str = "zh",
) -> str:
    en = str(locale or "").lower().startswith("en")
    if not placements:
        return "(no required body/run placements)" if en else "（无强制正文/run 落位）"
    if en:
        lines = [
            "Required body/run styles in this section (hang each listed style; "
            "Run* go in expected_runs on a real paragraph, not as their own blocks):"
        ]
    else:
        lines = [
            "本章必须挂上的正文/run（按列出的 style 挂上；"
            "Run* 写在真实段落块的 expected_runs 里，不要单独成块）："
        ]
    for p in placements:
        sid = str(p.get("style_id") or "").strip()
        nid = str(p.get("node_id") or "").strip()
        n = (p.get("count") if isinstance(p.get("count"), int) else 1) or 1
        kind = str(p.get("kind") or "")
        node = node_index.get(nid) or {}
        title = str(node.get("title") or "").strip()
        extra = f" ({title})" if title and en else (f"「{title}」" if title else "")
        tag = f" [{kind}]" if kind else ""
        if en:
            lines.append(f"- {sid}{tag} ×{n} → node {nid}{extra}".rstrip())
        else:
            lines.append(f"- {sid}{tag} ×{n} → 节点 {nid} {extra}".rstrip())
    return "\n".join(lines)


def unplaced_text_styles(
    catalog: list[dict[str, Any]],
    placements: list[dict[str, Any]],
) -> list[str]:
    have = {str(p.get("style_id") or "") for p in placements}
    return sorted(str(r.get("style_id") or "") for r in catalog if r.get("style_id") and r["style_id"] not in have)


def missing_required_text(
    blocks: list[dict[str, Any]],
    placements: list[dict[str, Any]],
) -> list[str]:
    """3c1 styles not yet hung on this chapter's blocks (Para* as role_id, Run* in expected_runs)."""
    used_para: set[str] = set()
    used_run: set[str] = set()
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid_ = str(b.get("role_id") or "").strip()
        if rid_:
            used_para.add(rid_)
        used_run.update(_iter_run_ids_from_block(b))
    missing: list[str] = []
    for p in placements or []:
        if not isinstance(p, dict):
            continue
        sid = str(p.get("style_id") or "").strip()
        if not sid:
            continue
        kind = str(p.get("kind") or "").lower()
        is_run = kind == "run" or sid.startswith("Run") or sid.startswith("Span")
        if is_run:
            if sid not in used_run:
                missing.append(sid)
        elif sid not in used_para:
            missing.append(sid)
    return sorted(set(missing))


def inject_missing_text_styles(
    blocks: list[dict[str, Any]],
    missing: list[str],
) -> int:
    """Append thin Para* blocks or fold Run* into expected_runs. Returns how many styles fixed."""
    if not missing:
        return 0
    n = 0
    for sid in missing:
        is_run = sid.startswith("Run") or sid.startswith("Span")
        if is_run:
            host = None
            for b in reversed(blocks or []):
                if not isinstance(b, dict):
                    continue
                rid_ = str(b.get("role_id") or "")
                if not rid_ or rid_.startswith(("Sec", "Tbl", "Img", "Run")):
                    continue
                if is_caption_role(rid_) or "Heading" in rid_:
                    continue
                host = b
                break
            if host is None and blocks:
                host = blocks[-1]
            if host is None:
                blocks.append({
                    "role_id": "ParaBody",
                    "main_point": sid[:40],
                    "length_hint": "short",
                    "expected_runs": [sid],
                })
            else:
                er = host.setdefault("expected_runs", [])
                if not isinstance(er, list):
                    host["expected_runs"] = [sid]
                elif sid not in er:
                    er.append(sid)
            n += 1
            continue
        blocks.append({
            "role_id": sid,
            "main_point": sid[:40],
            "length_hint": "short",
            "expected_runs": [],
        })
        n += 1
    return n


def _iter_run_ids_from_block(block: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for er in block.get("expected_runs") or []:
        if isinstance(er, str) and er.strip():
            out.append(er.strip())
        elif isinstance(er, dict):
            rid_ = str(er.get("role") or er.get("role_id") or "").strip()
            if rid_:
                out.append(rid_)
    return out


def table_cell_ids_from_stylesheet(table_sid: str, stylesheet: Any) -> set[str]:
    """Para*Cell (table-paragraph) ids from a Tbl* stylesheet row — ignore chrome keys."""
    chrome = {
        "label", "value", "header", "data", "stub", "note", "remark",
        "row_last", "row_first", "total", "footer",
    }
    ids: set[str] = set()
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or "").strip()
        if sid != table_sid:
            continue
        pmap = item.get("position_map") or {}
        if isinstance(pmap, dict):
            for v in pmap.values():
                if isinstance(v, str) and v.strip():
                    para = v.strip()
                    if para not in chrome and (para.startswith("Para") or para.endswith("Cell")):
                        ids.add(para)
                elif isinstance(v, dict):
                    para = str(v.get("paragraph") or v.get("role_id") or "").strip()
                    if para and para not in chrome:
                        ids.add(para)
        cells = item.get("cells") or {}
        if isinstance(cells, dict):
            for meta in cells.values():
                if isinstance(meta, dict):
                    para = str(meta.get("paragraph") or meta.get("paragraph_style") or "").strip()
                    if para and para not in chrome:
                        ids.add(para)
    return ids


def collect_used_style_ids(
    plan: dict[str, Any],
    library: dict[str, Any] | None = None,
) -> set[str]:
    """Styles actually instantiated on sections / outline blocks (incl. runs and table-paragraph)."""
    from LongDocFmtEval.pipeline_auto.style_taxonomy import (  # noqa: PLC0415
        cell_paragraphs_for_table,
        is_table_paragraph_object,
        normalize_paragraph_object,
    )

    used: set[str] = set()
    stylesheet = plan.get("stylesheet") or []
    # Normalize Para*Cell object tags while scanning (canonical: paragraph.table_cell)
    for item in stylesheet:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        if is_table_paragraph_object(item.get("object"), sid):
            item["object"] = normalize_paragraph_object(item.get("object"), sid)
    for sec in plan.get("sections") or []:
        if isinstance(sec, dict) and sec.get("role_id"):
            used.add(str(sec["role_id"]))
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            rid_ = str(b.get("role_id") or "").strip()
            if rid_:
                used.add(rid_)
                used.update(_iter_run_ids_from_block(b))
                used |= table_cell_ids_from_stylesheet(rid_, stylesheet)
                for para in cell_paragraphs_for_table(rid_, plan, library):
                    used.add(para)
    # Any used Tbl* ⇒ keep selection's table-paragraph ladder (and library maps)
    table_used = {
        s for s in used
        if s.startswith("Tbl") or any(
            isinstance(it, dict)
            and str(it.get("style_id") or "") == s
            and str(it.get("object") or "").lower() == "table"
            for it in stylesheet
        )
    }
    if table_used:
        for cid in (plan.get("selection") or {}).get("paragraph_cell_roles") or []:
            if isinstance(cid, str) and cid.strip():
                used.add(cid.strip())
            elif isinstance(cid, dict):
                rid = str(cid.get("role_id") or cid.get("style_id") or "").strip()
                if rid:
                    used.add(rid)
        for tid in table_used:
            for para in cell_paragraphs_for_table(tid, plan, library):
                used.add(para)
    return {s for s in used if s}


def trim_stylesheet_to_used(
    plan: dict[str, Any],
    library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """3e: drop catalog styles that never appear on sections/blocks. Mutates plan."""
    stylesheet = [x for x in (plan.get("stylesheet") or []) if isinstance(x, dict)]
    used = collect_used_style_ids(plan, library=library)
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in stylesheet:
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        if sid in used:
            kept.append(item)
        else:
            dropped.append(sid)
    plan["stylesheet"] = kept
    plan["selection"] = infer_selection_from_stylesheet(kept, plan.get("selection") if isinstance(plan.get("selection"), dict) else {})
    fe = plan.get("format_expectations")
    if isinstance(fe, list):
        plan["format_expectations"] = [
            x for x in fe
            if isinstance(x, dict) and str(x.get("role_id") or x.get("style_id") or "") in used
        ]
    diff = plan.get("media_style_diff")
    if isinstance(diff, list):
        plan["media_style_diff"] = [
            x for x in diff
            if isinstance(x, dict) and str(x.get("style_id") or "") in used
        ]
    return {
        "kept": [str(x.get("style_id") or "") for x in kept],
        "dropped": dropped,
        "used": sorted(used),
    }


def collect_node_ids_from_root(root: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()

    def rec(n: dict[str, Any]) -> None:
        nid = str(n.get("id") or "").strip()
        if nid:
            ids.add(nid)
        for c in n.get("children") or []:
            if isinstance(c, dict):
                rec(c)

    if isinstance(root, dict):
        rec(root)
    return ids


def placements_for_chunk(placements: list[dict[str, Any]], chunk: dict[str, Any]) -> list[dict[str, Any]]:
    ids = collect_node_ids_from_root(chunk.get("root") if isinstance(chunk.get("root"), dict) else None)
    iid = str(chunk.get("instance_id") or "").strip()
    root_id = str(chunk.get("node_id") or "").strip()
    if iid:
        ids.add(iid)
    if root_id:
        ids.add(root_id)
    out: list[dict[str, Any]] = []
    for p in placements or []:
        if not isinstance(p, dict):
            continue
        nid = str(p.get("node_id") or "").strip()
        if nid and nid in ids:
            out.append(p)
    return out


def format_chunk_media_must(
    placements: list[dict[str, Any]],
    node_index: dict[str, dict],
    *,
    locale: str = "zh",
) -> str:
    en = str(locale or "").lower().startswith("en")
    if not placements:
        return "(no required media placements)" if en else "（无强制媒体落位）"
    if en:
        lines = [
            "Required tables/figures in this section (hang ×N for each listed row; "
            "the same style may appear on several nodes; "
            "captions next to body tables/figures; cover logos MUST NOT have captions; "
            "cover info tables omit captions):"
        ]
    else:
        lines = [
            "本章必须挂上的表/图（按列出的 ×N 挂够，同一 style 可多节点；"
            "正文表/图紧邻题注；封面 logo **禁止**题注；封面信息表不要题注）："
        ]
    for p in placements:
        sid = str(p.get("style_id") or "").strip()
        nid = str(p.get("node_id") or "").strip()
        n = (p.get("count") if isinstance(p.get("count"), int) else 1) or 1
        node = node_index.get(nid) or {}
        title = str(node.get("title") or "").strip()
        extra = f"「{title}」" if title else ""
        if en:
            extra = f" ({title})" if title else ""
            lines.append(f"- {sid} ×{n} → node {nid}{extra}".rstrip())
        else:
            lines.append(f"- {sid} ×{n} → 节点 {nid} {extra}".rstrip())
    return "\n".join(lines)


def pick_fallback_node_id(
    style_id: str,
    description: str,
    node_index: dict[str, dict[str, Any]],
) -> str:
    """Cover/logo → cover node; otherwise first H1 (then any node)."""
    if not node_index:
        return ""
    blob = f"{style_id} {description}".lower()
    coverish = any(
        k in blob for k in ("cover", "logo", "embl", "封面", "文头", "标识")
    )

    def kind_of(n: dict[str, Any]) -> str:
        return str(n.get("kind") or n.get("type") or "").strip().lower()

    items = list(node_index.items())
    if coverish:
        for nid, n in items:
            k = kind_of(n)
            title = str(n.get("title") or "").lower()
            if k in {"cover", "front", "title"} or "cover" in k or "封面" in title:
                return nid
        for nid, n in items:
            if not n.get("heading_level") and not str(kind_of(n)).startswith("h"):
                return nid
    for nid, n in items:
        try:
            if int(n.get("heading_level") or 0) == 1:
                return nid
        except (TypeError, ValueError):
            continue
    return items[0][0]


_ONCE_MEDIA_RE = re.compile(
    r"(CoverMark|CoverLogo|CoverEmblem|Wordmark|WordMark|Crest|"
    r"封面标识|封面徽标|封面信息|PlanInfo|CoverInfo|FormalHeadInfo|"
    r"Stamp|印章|红头)",
    re.I,
)
_CHART_MEDIA_RE = re.compile(
    r"(StatChart|Chart|统计图|柱状|折线|饼图|指标图)",
    re.I,
)
_IMG_INSTANCE_FLOOR = 6
_IMG_INSTANCE_CAP = 12
_TBL_INSTANCE_FLOOR = 6
_TBL_INSTANCE_CAP = 14
_REUSE_PER_STYLE_CAP = 4


def is_once_media(style_id: str, description: str = "") -> bool:
    """Cover logo / cover info / stamp: one instance. Body tables/figures reuse."""
    return bool(_ONCE_MEDIA_RE.search(f"{style_id} {description}"))


def is_chart_media(style_id: str, description: str = "") -> bool:
    return bool(_CHART_MEDIA_RE.search(f"{style_id} {description}"))


def _placement_count(row: dict[str, Any]) -> int:
    cnt = row.get("count")
    if not isinstance(cnt, int) or cnt < 1:
        return 1
    return cnt


def _style_instance_total(placements: list[dict[str, Any]], style_id: str) -> int:
    return sum(
        _placement_count(p)
        for p in placements
        if str(p.get("style_id") or "") == style_id
    )


def _object_instance_total(placements: list[dict[str, Any]], catalog: list[dict[str, Any]], obj: str) -> int:
    want = {
        str(r.get("style_id") or "")
        for r in catalog
        if str(r.get("object") or "").lower() == obj
        or (obj == "table" and str(r.get("style_id") or "").startswith("Tbl"))
        or (obj == "image" and str(r.get("style_id") or "").startswith("Img"))
    }
    return sum(
        _placement_count(p)
        for p in placements
        if str(p.get("style_id") or "") in want
    )


def _is_frontish_node(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or node.get("type") or "").strip().lower()
    title = str(node.get("title") or "")
    if kind in _FRONT_KINDS or kind in {"toc", "contents", "letter"}:
        return True
    if "封面" in title or "目录" in title.lower() or "cover" in title.lower():
        return True
    return False


def _body_hang_node_ids(node_index: dict[str, dict[str, Any]]) -> list[str]:
    """Reading-order body nodes to hang extra table/figure instances (H2, H3, then H1)."""
    h2: list[str] = []
    h3: list[str] = []
    h1: list[str] = []
    for nid, n in node_index.items():
        if not isinstance(n, dict) or _is_frontish_node(n):
            continue
        lvl = _node_level(n)
        if lvl == 2:
            h2.append(nid)
        elif lvl == 3:
            h3.append(nid)
        elif lvl == 1:
            h1.append(nid)
    return h2 or h3 or h1


def _reuse_floor(style_id: str, description: str, obj: str, n_body: int) -> int:
    if is_once_media(style_id, description):
        return 1
    if n_body <= 1:
        return 1
    if obj == "image" and is_chart_media(style_id, description):
        desired = 3
    else:
        desired = 2
    return min(_REUSE_PER_STYLE_CAP, max(1, min(desired, n_body)))


def _pick_extra_node(
    hang: list[str],
    *,
    used_nodes: set[str],
    load: Counter[str],
    start: int = 0,
) -> str:
    if not hang:
        return ""
    n = len(hang)
    for max_load in (0, 1, 99):
        for i in range(n):
            nid = hang[(start + i) % n]
            if nid in used_nodes:
                continue
            if int(load.get(nid, 0)) <= max_load:
                return nid
    return ""


def spread_media_placements(
    placements: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    node_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Same Tbl*/Img* style is a type: hang extra instances on other body nodes.

    Cover logo / cover info stay at 1. Body tables and figures default to 2+;
    statistical charts default to 3. Then lift document-wide floors (figures 6–12,
    tables 6–14) using those reusable styles.
    """
    notes: list[str] = []
    out = [dict(p) for p in placements]
    hang = _body_hang_node_ids(node_index)
    n_body = len(hang)
    desc_of = {
        str(r.get("style_id") or "").strip(): str(r.get("description") or "")
        for r in catalog
    }
    obj_of = {
        str(r.get("style_id") or "").strip(): str(r.get("object") or "").lower()
        for r in catalog
    }

    def _load() -> Counter[str]:
        c: Counter[str] = Counter()
        for p in out:
            c[str(p.get("node_id") or "")] += _placement_count(p)
        return c

    def _used_nodes(sid: str) -> set[str]:
        return {
            str(p.get("node_id") or "")
            for p in out
            if str(p.get("style_id") or "") == sid
        }

    def _add(sid: str, nid: str, why: str) -> None:
        out.append({"style_id": sid, "node_id": nid, "count": 1})
        notes.append(f"{sid}: {why} → {nid}")

    cat_ids = [str(r.get("style_id") or "").strip() for r in catalog if r.get("style_id")]
    for i, sid in enumerate(cat_ids):
        desc = desc_of.get(sid, "")
        obj = obj_of.get(sid) or ("table" if sid.startswith("Tbl") else "image")
        floor = _reuse_floor(sid, desc, obj, n_body)
        have = _style_instance_total(out, sid)
        cap_obj = _IMG_INSTANCE_CAP if obj == "image" else _TBL_INSTANCE_CAP
        while have < floor:
            if _object_instance_total(out, catalog, obj) >= cap_obj:
                break
            nid = _pick_extra_node(
                hang, used_nodes=_used_nodes(sid), load=_load(), start=i + have,
            )
            if not nid:
                break
            _add(sid, nid, "正文复用补挂")
            have += 1

    chart_ids = [
        sid for sid in cat_ids
        if (obj_of.get(sid) == "image" or sid.startswith("Img"))
        and not is_once_media(sid, desc_of.get(sid, ""))
        and is_chart_media(sid, desc_of.get(sid, ""))
    ]
    reusable_img = [
        sid for sid in cat_ids
        if (obj_of.get(sid) == "image" or sid.startswith("Img"))
        and not is_once_media(sid, desc_of.get(sid, ""))
    ]
    reusable_tbl = [
        sid for sid in cat_ids
        if (obj_of.get(sid) == "table" or sid.startswith("Tbl"))
        and not is_once_media(sid, desc_of.get(sid, ""))
    ]

    def _fill_floor(sids: list[str], obj: str, floor: int, cap: int, why: str) -> None:
        if not sids:
            return
        k = 0
        while _object_instance_total(out, catalog, obj) < floor:
            sid = sids[k % len(sids)]
            k += 1
            if _style_instance_total(out, sid) >= _REUSE_PER_STYLE_CAP:
                if k > len(sids) * _REUSE_PER_STYLE_CAP:
                    break
                continue
            nid = _pick_extra_node(
                hang, used_nodes=_used_nodes(sid), load=_load(), start=k,
            )
            if not nid:
                break
            if _object_instance_total(out, catalog, obj) >= cap:
                break
            _add(sid, nid, why)
            if k > 40:
                break

    _fill_floor(chart_ids or reusable_img, "image", _IMG_INSTANCE_FLOOR, _IMG_INSTANCE_CAP, "补足图实例下限")
    _fill_floor(reusable_tbl, "table", _TBL_INSTANCE_FLOOR, _TBL_INSTANCE_CAP, "补足表实例下限")
    return out, notes


def normalize_media_placements(
    raw_placements: Any,
    catalog: list[dict[str, Any]],
    node_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep valid rows; remap bad node_ids; fill every catalog Tbl*/Img*; reuse body styles."""
    notes: list[str] = []
    cat_ids = [str(r.get("style_id") or "").strip() for r in catalog if r.get("style_id")]
    cat_set = set(cat_ids)
    desc_of = {
        str(r.get("style_id") or "").strip(): str(r.get("description") or "")
        for r in catalog
    }
    allowed = set(node_index)
    out: list[dict[str, Any]] = []
    covered: set[str] = set()
    for p in raw_placements or []:
        if not isinstance(p, dict):
            continue
        sid = str(p.get("style_id") or "").strip()
        nid = str(p.get("node_id") or "").strip()
        if sid not in cat_set:
            notes.append(f"忽略目录外 style_id={sid}")
            continue
        if nid not in allowed:
            fb = pick_fallback_node_id(sid, desc_of.get(sid, ""), node_index)
            notes.append(f"{sid}: node_id={nid} 无效 → {fb}")
            nid = fb
        if not nid:
            continue
        cnt = p.get("count")
        if not isinstance(cnt, int) or cnt < 1:
            cnt = 1
        cnt = min(3, cnt)
        out.append({"style_id": sid, "node_id": nid, "count": cnt})
        covered.add(sid)
    for sid in cat_ids:
        if sid in covered:
            continue
        nid = pick_fallback_node_id(sid, desc_of.get(sid, ""), node_index)
        if not nid:
            notes.append(f"{sid}: 无可用节点，未落位")
            continue
        out.append({"style_id": sid, "node_id": nid, "count": 1})
        notes.append(f"{sid}: 缺落位，补到 {nid}")
        covered.add(sid)
    spread, extra = spread_media_placements(out, catalog, node_index)
    notes.extend(extra)
    return spread, notes


def media_instance_totals(
    placements: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> tuple[int, int]:
    """Return (table_instances, image_instances)."""
    return (
        _object_instance_total(placements, catalog, "table"),
        _object_instance_total(placements, catalog, "image"),
    )


def missing_required_media(
    blocks: list[dict[str, Any]],
    placements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Styles whose instance count in this chapter is below the summed placement need."""
    used = Counter(str(b.get("role_id") or "") for b in blocks or [])
    need_by: dict[str, dict[str, Any]] = {}
    for p in placements or []:
        sid = str(p.get("style_id") or "").strip()
        if not sid:
            continue
        rec = need_by.setdefault(
            sid,
            {"style_id": sid, "node_id": str(p.get("node_id") or ""), "need": 0},
        )
        rec["need"] += _placement_count(p)
        rec["node_id"] = str(p.get("node_id") or rec["node_id"])
    missing: list[dict[str, Any]] = []
    for sid, rec in need_by.items():
        need = int(rec["need"])
        have = int(used.get(sid, 0))
        if have < need:
            missing.append({
                "style_id": sid,
                "node_id": rec["node_id"],
                "count": need,
                "have": have,
                "need": need,
            })
    return missing


def inject_missing_media_blocks(
    blocks: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> int:
    """Append thin Tbl*/Img* blocks so required styles appear. Returns how many added."""
    by_sid = {str(r.get("style_id") or ""): r for r in catalog}
    added = 0
    for p in missing:
        sid = str(p.get("style_id") or "")
        rec = by_sid.get(sid) or {}
        obj = str(rec.get("object") or "").lower()
        if obj not in ("table", "image"):
            obj = "table" if sid.startswith("Tbl") else "image"
        need = int(p.get("need") or p.get("count") or 1)
        have = int(p.get("have") or 0)
        dn = str(rec.get("display_name") or sid)
        for _ in range(max(0, need - have)):
            block: dict[str, Any] = {
                "role_id": sid,
                "main_point": dn[:40],
                "length_hint": "short",
            }
            if obj == "table":
                block["table_ref"] = {}
                block["image_ref"] = None
            else:
                block["image_ref"] = {}
                block["table_ref"] = None
            blocks.append(block)
            added += 1
    return added


def heading_style_map(stylesheet: Any) -> dict[int, str]:
    """heading_level → first paragraph style_id in stylesheet."""
    idx = heading_index({"stylesheet": stylesheet})
    out: dict[int, str] = {}
    for sid, rec in idx.items():
        hl = rec.get("heading_level")
        if not hl:
            continue
        if rec.get("object") == "section" or str(sid).startswith("Sec"):
            continue
        out.setdefault(int(hl), sid)
    return out


def format_heading_style_map(stylesheet: Any) -> str:
    mapping = heading_style_map(stylesheet)
    if not mapping:
        return "（stylesheet 尚未声明 heading_level）"
    return "\n".join(f"H{lvl} → {sid}" for lvl, sid in sorted(mapping.items()))


def infer_selection_from_stylesheet(
    stylesheet: Any,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sel: dict[str, Any] = dict(selection) if isinstance(selection, dict) else {}

    def _ids(raw: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(raw, list):
            return out
        for x in raw:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
            elif isinstance(x, dict):
                rid_ = str(x.get("role_id") or x.get("style_id") or "").strip()
                if rid_:
                    out.append(rid_)
        return out

    inferred: dict[str, list[str]] = {
        "section_roles": [],
        "paragraph_body_roles": [],
        "paragraph_cell_roles": [],
        "run_roles": [],
        "table_roles": [],
        "image_roles": [],
    }
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        obj = str(item.get("object") or "").strip().lower()
        if obj == "section" or sid.startswith("Sec"):
            key = "section_roles"
        elif (
            obj in {"cell", "paragraph.table_cell", "paragraph.cell_text", "table.paragraph"}
            or ("Cell" in sid and sid.startswith("Para"))
        ):
            key = "paragraph_cell_roles"
            item["object"] = "paragraph.table_cell"
        elif obj in {"run", "paragraph_runs"} or sid.startswith("Run") or sid.startswith("Span"):
            key = "run_roles"
        elif obj == "table" or sid.startswith("Tbl"):
            key = "table_roles"
        elif obj == "image" or sid.startswith("Img"):
            key = "image_roles"
        elif obj in {"paragraph", "paragraph.body"} or sid.startswith("Para"):
            key = "paragraph_body_roles"
            if obj in {"", "paragraph"}:
                item["object"] = "paragraph.body"
        else:
            continue
        if sid not in inferred[key]:
            inferred[key].append(sid)

    for key, ids in inferred.items():
        existing = _ids(sel.get(key))
        merged = list(existing)
        for sid in ids:
            if sid not in merged:
                merged.append(sid)
        if key == "table_roles":
            orig = sel.get(key)
            if isinstance(orig, list) and orig and isinstance(orig[0], dict):
                have = {str(x.get("role_id") or "") for x in orig if isinstance(x, dict)}
                extra = [{"role_id": s} for s in merged if s not in have]
                sel[key] = list(orig) + extra
            else:
                sel[key] = [{"role_id": s} for s in merged]
        else:
            sel[key] = merged
    sel.setdefault("notes", list(sel.get("notes") or []) if isinstance(sel.get("notes"), list) else [])
    return sel


def default_section_role(kind: str, stylesheet: Any) -> str:
    ids = [
        str(it.get("style_id") or "")
        for it in (stylesheet or [])
        if isinstance(it, dict)
        and str(it.get("style_id") or "")
        and (
            str(it.get("object") or "").lower() == "section"
            or str(it.get("style_id") or "").startswith("Sec")
        )
    ]
    kind = str(kind or "").strip().lower()
    if kind == "cover" and "SecCover" in ids:
        return "SecCover"
    pool = [s for s in ids if s != "SecCover"] or ids
    for cand in _KIND_SECTION_HINTS.get(kind, ()):
        if cand in pool:
            return cand
    needles = {
        "toc": ("toc", "contents", "目录"),
        "contents": ("toc", "contents", "目录"),
        "letter": ("letter", "preface", "致辞", "前言"),
        "h1": ("chapter", "body", "章", "正文"),
        "back": ("annex", "appendix", "back", "附"),
        "annex": ("annex", "appendix", "附"),
        "appendix": ("appendix", "annex", "附"),
    }.get(kind, ())
    for sid in pool:
        low = sid.lower()
        if any(n.lower() in low or n in sid for n in needles):
            return sid
    if kind in _BACK_KINDS or kind == "back":
        return pool[-1] if pool else "SecBody"
    return pool[0] if pool else "SecBody"


def merge_sections_with_chunks(
    chunks: list[dict[str, Any]],
    llm_sections: Any,
    stylesheet: Any,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    if isinstance(llm_sections, list):
        for s in llm_sections:
            if not isinstance(s, dict):
                continue
            iid = str(s.get("instance_id") or "").strip()
            if iid:
                by_id[iid] = s
            title = str(s.get("title") or "").strip()
            if title and title not in by_title:
                by_title[title] = s
    section_ids = stylesheet_style_ids(stylesheet)
    out: list[dict[str, Any]] = []
    for ch in chunks:
        llm = by_id.get(str(ch.get("instance_id") or "")) or by_title.get(str(ch.get("title") or "")) or {}
        role = str(llm.get("role_id") or "").strip()
        if role not in section_ids:
            role = default_section_role(str(ch.get("kind") or ""), stylesheet)
        kind = str(ch.get("kind") or "")
        if kind == "cover" and "SecCover" in section_ids:
            role = "SecCover"
        elif kind != "cover" and role == "SecCover":
            role = default_section_role(kind or "h1", stylesheet)
        elif role == "SecFormalHead" and "SecCover" in section_ids:
            role = default_section_role(kind or "letter", stylesheet)
        elif kind in {"h1", "h2", "h3"} and role == "SecFormalHead":
            role = default_section_role("h1", stylesheet)
        overrides = llm.get("instance_overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        out.append({
            "instance_id": ch["instance_id"],
            "role_id": role,
            "title": ch.get("title") or llm.get("title") or "",
            "purpose": str(llm.get("purpose") or ch.get("purpose") or ""),
            "instance_overrides": overrides,
        })
    return out


def ensure_heading_styles(stylesheet: list[dict[str, Any]], used_depth: int) -> list[str]:
    """Guarantee heading styles for 1..used_depth. Mutates list."""
    notes: list[str] = []
    mapping = heading_style_map(stylesheet)
    ids = stylesheet_style_ids(stylesheet)
    depth = max(0, int(used_depth or 0))
    for lvl in range(1, depth + 1):
        if mapping.get(lvl):
            continue
        sid = f"ParaHeading{lvl}"
        if sid not in ids:
            stylesheet.append({
                "style_id": sid,
                "display_name": f"标题 {lvl}",
                "object": "paragraph",
                "heading_level": lvl,
                "description": "程序补入：正文标题层级（stylesheet 未声明）",
                "expectation": "大纲标题段，勿当封面题",
            })
            notes.append(f"补入 {sid} (heading_level={lvl})")
        else:
            for item in stylesheet:
                if isinstance(item, dict) and str(item.get("style_id") or "") == sid:
                    item["heading_level"] = lvl
                    item.setdefault("object", "paragraph")
                    notes.append(f"为已有 {sid} 补 heading_level={lvl}")
                    break
    return notes


def uniquify_block_ids(instance_id: str, blocks: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        bid = str(b.get("block_id") or "").strip() or f"B{i + 1:02d}"
        if not bid.startswith(f"{instance_id}_"):
            bid = f"{instance_id}_{bid}"
        base = bid
        n = 2
        while bid in seen:
            bid = f"{base}_{n}"
            n += 1
        seen.add(bid)
        b["block_id"] = bid


def format_expectations_from_stylesheet(stylesheet: Any, existing: Any = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for it in existing:
            if isinstance(it, dict) and it.get("role_id"):
                by_id[str(it["role_id"])] = it
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        prev = by_id.get(sid) or {}
        exp = str(prev.get("expectation") or item.get("expectation") or item.get("description") or "").strip()
        out.append({"role_id": sid, "expectation": exp})
    for sid, prev in by_id.items():
        if sid not in seen:
            out.append(prev)
    return out


def first_body_style_id(stylesheet: Any) -> str:
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or "").strip()
        obj = str(item.get("object") or "").lower()
        if not sid or sid.startswith("Sec"):
            continue
        if item.get("heading_level") not in (None, "") or item.get("title_kind"):
            continue
        if obj in {"paragraph", "paragraph.body", ""} and "Caption" not in sid and "Cell" not in sid:
            return sid
    ids = stylesheet_style_ids(stylesheet)
    for cand in ("ParaBodyNarrative", "ParaBody", "ParaText"):
        if cand in ids:
            return cand
    return next(iter(ids), "ParaBodyNarrative")
