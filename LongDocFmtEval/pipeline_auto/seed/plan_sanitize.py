"""Plan hygiene: drop empty Word sections; strip abstract *Base roles from stylesheets."""
from __future__ import annotations

from collections import Counter
from typing import Any

from LongDocFmtEval.common.role_library import (
    is_abstract_role_id,
    selectable_descendants,
)


def is_section_style_block(block: dict[str, Any] | None, roles: dict | None = None) -> bool:
    """True when a block's role is a Word section (layout), not a paragraph."""
    if not isinstance(block, dict):
        return False
    rid = str(block.get("role_id") or "")
    if not rid:
        return False
    obj = ""
    if isinstance(roles, dict):
        obj = str((roles.get(rid) or {}).get("object") or "").lower()
    if obj.startswith("section"):
        return True
    if obj in {
        "paragraph", "paragraph.body", "paragraph.cell_text", "paragraph.table_cell",
        "run", "table", "image",
    }:
        return False
    return rid.startswith("Sec")


def drop_section_role_blocks(plan: dict[str, Any], roles: dict | None = None) -> list[str]:
    """Remove Sec* / object=section items from outline[].blocks[] (layout, not body)."""
    notes: list[str] = []
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        blocks = o.get("blocks")
        if not isinstance(blocks, list):
            continue
        kept: list[Any] = []
        iid = str(o.get("instance_id") or "")
        for b in blocks:
            if is_section_style_block(b, roles):
                notes.append(
                    f"{iid}: 去掉节角色块 {b.get('block_id')} ({b.get('role_id')})"
                )
                continue
            kept.append(b)
        o["blocks"] = kept
    return notes


def section_family_for_instance(sec: dict[str, Any]) -> str:
    """front_matter / body / back_matter from role_id + title."""
    rid = str((sec or {}).get("role_id") or "")
    title = str((sec or {}).get("title") or "")
    blob = f"{rid} {title}".lower()
    if rid.startswith("SecFront") or any(
        k in blob for k in (
            "cover", "toc", "letter", "chair", "preface", "abstract", "front",
            "封面", "目录", "致辞", "摘要", "前言", "关键词",
        )
    ):
        return "front_matter"
    if rid.startswith("SecBack") or any(
        k in blob for k in (
            "appendix", "annex", "glossary", "attachment", "contact", "back",
            "附录", "释义", "附件", "后记", "致谢", "联系",
        )
    ):
        return "back_matter"
    return "body"


def apply_section_page_continuity(plan: dict[str, Any]) -> list[str]:
    """Fill ``sections[].instance_overrides.pageStart`` so numbering continues in-family.

    Front: first instance restarts at 1; later front continue.
    Body: first body instance restarts at 1 (arabic); later body continue.
    Back: continue (do not restart).
    Mutates plan. Role-level pageStart is overridden by these instance values at render.
    """
    notes: list[str] = []
    sections = plan.get("sections")
    if not isinstance(sections, list):
        return notes
    seen_front = False
    seen_body = False
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        ov = sec.get("instance_overrides")
        if not isinstance(ov, dict):
            ov = {}
            sec["instance_overrides"] = ov
        else:
            sec["instance_overrides"] = ov
        iid = str(sec.get("instance_id") or "")
        fam = section_family_for_instance(sec)
        if fam == "front_matter":
            if not seen_front:
                ov["pageStart"] = 1
                seen_front = True
                notes.append(f"{iid}: 前置首页 pageStart=1")
            else:
                ov["pageStart"] = "none"
                notes.append(f"{iid}: 前置续页 pageStart=none")
        elif fam == "body":
            if not seen_body:
                ov["pageStart"] = 1
                ov.setdefault("pageNumFmt", "decimal")
                seen_body = True
                notes.append(f"{iid}: 正文首页 pageStart=1")
            else:
                ov["pageStart"] = "none"
                notes.append(f"{iid}: 正文续页 pageStart=none")
        else:
            ov["pageStart"] = "none"
            notes.append(f"{iid}: 后置续页 pageStart=none")
    return notes


def _block_has_content(block: dict[str, Any]) -> bool:
    """True when a block carries text, a table, or an image.

    Section styles (Sec*) are layout, not body content.
    """
    if not isinstance(block, dict):
        return False
    if is_section_style_block(block):
        return False
    rid = str(block.get("role_id") or "")
    if rid.startswith("Tbl") or block.get("table_ref") or str(block.get("length_hint") or "") == "table":
        return True
    if rid.startswith("Img") or block.get("image_ref") or block.get("figure_id") or str(
        block.get("length_hint") or ""
    ) == "image":
        return True
    mp = str(block.get("main_point") or "").strip()
    if mp:
        return True
    # Non-empty expected_runs text also counts as content
    for run in block.get("expected_runs") or []:
        if isinstance(run, dict) and str(run.get("text") or "").strip():
            return True
    return False


def section_has_content(
    sec: dict[str, Any],
    outline_by_inst: dict[str, list[dict[str, Any]]],
) -> bool:
    """A section is empty iff it has no text / table / image blocks at all."""
    if not isinstance(sec, dict):
        return False
    inst = sec.get("instance_id")
    blocks = outline_by_inst.get(inst) if inst is not None else None
    if blocks is None:
        # Orphan section row with no outline entry → treat as empty
        return False
    return any(_block_has_content(b) for b in blocks)


def prune_empty_sections(plan: dict[str, Any]) -> list[str]:
    """Remove empty sections from ``sections`` / ``outline`` and unused ``Sec*`` from
    ``stylesheet`` / ``selection``. Mutates ``plan`` in place. Returns human notes.
    """
    notes: list[str] = []
    if not isinstance(plan, dict):
        return notes

    sections = plan.get("sections") or []
    outline = plan.get("outline") or []
    if not isinstance(sections, list):
        return notes

    outline_by_inst: dict[str, list[dict[str, Any]]] = {}
    if isinstance(outline, list):
        for entry in outline:
            if not isinstance(entry, dict):
                continue
            inst = entry.get("instance_id")
            if inst is None:
                continue
            outline_by_inst[inst] = list(entry.get("blocks") or [])

    keep_secs: list[dict[str, Any]] = []
    drop_inst: set[Any] = set()
    drop_roles: list[str] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        if section_has_content(sec, outline_by_inst):
            keep_secs.append(sec)
            continue
        inst = sec.get("instance_id")
        rid = str(sec.get("role_id") or "")
        drop_inst.add(inst)
        if rid:
            drop_roles.append(rid)
        notes.append(
            f"删除空节 {inst or '?'} ({rid or 'Sec?'}：无文本/表/图内容)"
        )

    if not drop_inst and len(keep_secs) == len(sections):
        return notes

    plan["sections"] = keep_secs

    if isinstance(outline, list):
        keep_inst = {s.get("instance_id") for s in keep_secs if isinstance(s, dict)}
        cleaned_outline: list[dict[str, Any]] = []
        for o in outline:
            if not isinstance(o, dict):
                continue
            inst = o.get("instance_id")
            if inst in drop_inst:
                continue
            # Drop orphan outline rows (no matching section) or empty-block orphans
            if inst not in keep_inst:
                blocks = list(o.get("blocks") or [])
                if not any(_block_has_content(b) for b in blocks):
                    notes.append(f"删除空大纲条目 {inst or '?'}")
                    continue
            cleaned_outline.append(o)
        plan["outline"] = cleaned_outline

    # Sec* still used by remaining instances
    used_sec_roles = {
        str(s.get("role_id") or "")
        for s in keep_secs
        if isinstance(s, dict) and s.get("role_id")
    }

    stylesheet = plan.get("stylesheet")
    if isinstance(stylesheet, list):
        plan["stylesheet"] = [
            s for s in stylesheet
            if not (
                isinstance(s, dict)
                and str(s.get("style_id") or "").startswith("Sec")
                and str(s.get("style_id") or "") not in used_sec_roles
            )
        ]

    selection = plan.get("selection")
    if isinstance(selection, dict):
        roles = selection.get("section_roles")
        if isinstance(roles, list):
            selection["section_roles"] = [r for r in roles if r in used_sec_roles]
        sn = selection.setdefault("notes", [])
        if isinstance(sn, list) and notes:
            sn.append(
                "已删除空节（无文本/表/图）："
                + ", ".join(str(i) for i in sorted(drop_inst, key=lambda x: str(x)))
            )

    meta = plan.setdefault("meta", {})
    if isinstance(meta, dict) and notes:
        applied = meta.setdefault("notes_applied", [])
        if isinstance(applied, list):
            applied.append(
                "prune_empty_sections: dropped "
                + ", ".join(str(i) for i in sorted(drop_inst, key=lambda x: str(x)))
            )

    return notes


# Concrete leaves commonly used when an abstract base leaked into a stylesheet.
_ABSTRACT_FALLBACKS: dict[str, list[str]] = {
    "ParaMainBodyBase": ["ParaBodyText", "ParaBody", "ParaNarrative"],
    "ParaFrontMatterBase": ["ParaCoverTitle", "ParaBodyParagraph"],
    "ParaBackMatterBase": ["ParaAppendixText", "ParaBibliographyEntry"],
    "ParaBodyBase": ["ParaBodyText", "ParaBody"],
    "ParaTableCellBase": ["ParaTblValueCell", "ParaTblHeaderCell", "ParaDataValueCell"],
    "SecFrontMatter": ["SecCover", "SecTOC", "SecToC", "SecToc"],
    "SecMainBody": ["SecChapter", "SecGroupOverview", "SecSegmentChapter"],
    "SecBackMatter": ["SecAnnex", "SecAppendix", "SecNotes"],
    "SecRoot": ["SecCover"],
}


def _sel_role_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("role_id") or item.get("span_role") or item.get("style_id") or "").strip()
    return str(item or "").strip()


def _stylesheet_ids(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in plan.get("stylesheet") or []:
        if isinstance(row, dict):
            sid = str(row.get("style_id") or "").strip()
            if sid:
                out.append(sid)
    return out


def _usage_counts(plan: dict[str, Any]) -> Counter:
    counts: Counter = Counter()
    for sec in plan.get("sections") or []:
        if isinstance(sec, dict):
            rid = str(sec.get("role_id") or "").strip()
            if rid:
                counts[rid] += 1
    for entry in plan.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        for block in entry.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            rid = str(block.get("role_id") or "").strip()
            if rid:
                counts[rid] += 1
            for run in block.get("expected_runs") or []:
                if isinstance(run, dict):
                    rr = str(run.get("role") or run.get("span_role") or "").strip()
                    if rr:
                        counts[rr] += 1
    return counts


def _family_candidates(rid: str, concrete_ids: list[str]) -> list[str]:
    if rid.startswith("Sec"):
        prefix = "Sec"
    elif rid.startswith("Tbl"):
        prefix = "Tbl"
    elif rid.startswith("Img"):
        prefix = "Img"
    elif rid.startswith("Run"):
        prefix = "Run"
    else:
        prefix = "Para"
    cands = [s for s in concrete_ids if s.startswith(prefix)]
    if prefix == "Para" and "Cell" not in rid:
        cands = [s for s in cands if "Cell" not in s]
    elif prefix == "Para" and "Cell" in rid:
        cands = [s for s in cands if "Cell" in s]
    return cands


def _leaf_score(role_id: str, abstract_id: str, usage: Counter, concrete_ids: list[str]) -> tuple:
    """Higher is better. Prefer in-stylesheet, body/text leaves, and usage; avoid headings as body stand-ins."""
    r = str(role_id or "")
    rl = r.lower()
    al = str(abstract_id or "").lower()
    score = usage.get(r, 0) * 10
    if r in concrete_ids:
        score += 100
    if "heading" in rl or rl.endswith("title"):
        score -= 50
    if any(k in rl for k in ("body", "narrative", "text", "prose")):
        score += 40
    if "front" in al and any(k in rl for k in ("cover", "toc", "highlight")):
        score += 20
    if "back" in al and any(k in rl for k in ("annex", "appendix", "note")):
        score += 20
    if "main" in al and r.startswith("Sec") and "cover" not in rl and "toc" not in rl:
        score += 15
    return (score, r)


def _pick_concrete(
    abstract_id: str,
    *,
    library_data: dict[str, Any] | None,
    concrete_ids: list[str],
    usage: Counter,
) -> str:
    descendants = selectable_descendants(library_data, abstract_id) if library_data else []
    in_ss = [d for d in descendants if d in concrete_ids]
    if in_ss:
        return max(in_ss, key=lambda x: _leaf_score(x, abstract_id, usage, concrete_ids))
    for fb in _ABSTRACT_FALLBACKS.get(abstract_id) or []:
        if fb in descendants or fb in concrete_ids:
            if fb and not is_abstract_role_id(fb, library_data):
                return fb
    if descendants:
        return max(descendants, key=lambda x: _leaf_score(x, abstract_id, usage, concrete_ids))
    for fb in _ABSTRACT_FALLBACKS.get(abstract_id) or []:
        if fb and not is_abstract_role_id(fb, library_data):
            return fb
    fam = _family_candidates(abstract_id, concrete_ids)
    if fam:
        return max(fam, key=lambda x: usage.get(x, 0))
    if concrete_ids:
        return concrete_ids[0]
    if abstract_id.startswith("Sec"):
        return "SecChapter"
    if abstract_id.startswith("Tbl"):
        return "TblData"
    if abstract_id.startswith("Img"):
        return "ImgFigure"
    if "Cell" in abstract_id:
        return "ParaTblValueCell"
    return "ParaBodyText"


def _add_selection_role(selection: dict[str, Any], role_id: str) -> None:
    if role_id.startswith("Sec"):
        bucket = "section_roles"
    elif role_id.startswith("Tbl"):
        bucket = "table_roles"
    elif role_id.startswith("Img"):
        bucket = "image_roles"
    elif role_id.startswith("Run"):
        bucket = "run_roles"
    elif "Cell" in role_id:
        bucket = "paragraph_cell_roles"
    else:
        bucket = "paragraph_body_roles"
    lst = selection.setdefault(bucket, [])
    if not isinstance(lst, list):
        selection[bucket] = [role_id]
        return
    existing = {_sel_role_id(x) for x in lst}
    if role_id in existing:
        return
    if bucket == "table_roles":
        lst.append({"role_id": role_id})
    else:
        lst.append(role_id)


def _filter_selection_list(items: list[Any], abstract: set[str], remap: dict[str, str]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in items:
        rid = _sel_role_id(item)
        if not rid:
            continue
        if rid in abstract:
            rid = remap.get(rid) or ""
            if not rid:
                continue
            if isinstance(item, dict):
                item = dict(item)
                item["role_id"] = rid
            else:
                item = rid
        if rid in seen:
            continue
        seen.add(rid)
        out.append(item)
    return out


def _scan_role_ids(plan: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    found.update(_stylesheet_ids(plan))
    sel = plan.get("selection") if isinstance(plan.get("selection"), dict) else {}
    for key in (
        "section_roles", "paragraph_body_roles", "paragraph_cell_roles",
        "run_roles", "table_roles", "image_roles",
    ):
        for item in sel.get(key) or []:
            rid = _sel_role_id(item)
            if rid:
                found.add(rid)
    for sec in plan.get("sections") or []:
        if isinstance(sec, dict) and sec.get("role_id"):
            found.add(str(sec.get("role_id")))
    for entry in plan.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        for block in entry.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("role_id"):
                found.add(str(block.get("role_id")))
    for row in plan.get("format_expectations") or []:
        if isinstance(row, dict) and row.get("role_id"):
            found.add(str(row.get("role_id")))
    return found


def strip_abstract_roles(
    plan: dict[str, Any],
    library_data: dict[str, Any] | None = None,
) -> list[str]:
    """Remove abstract bases from stylesheet / selection / outline; remap uses to leaves.

    Mutates ``plan`` in place. Abstract nodes (``abstract: true``, ``*Base``,
    SecFrontMatter / SecMainBody / SecBackMatter) must not become Word styles.
    """
    notes: list[str] = []
    if not isinstance(plan, dict):
        return notes

    present = _scan_role_ids(plan)
    abstract = {r for r in present if is_abstract_role_id(r, library_data)}
    if not abstract:
        return notes

    usage = _usage_counts(plan)
    concrete_now = [s for s in _stylesheet_ids(plan) if s not in abstract]
    remap: dict[str, str] = {}
    unresolved: list[str] = []
    for rid in sorted(abstract):
        tgt = _pick_concrete(
            rid,
            library_data=library_data,
            concrete_ids=concrete_now,
            usage=usage,
        )
        if not tgt or tgt == rid or is_abstract_role_id(tgt, library_data):
            unresolved.append(rid)
            continue
        remap[rid] = tgt
        if tgt not in concrete_now:
            concrete_now.append(tgt)

    stylesheet = plan.get("stylesheet")
    rows_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(stylesheet, list):
        for row in stylesheet:
            if isinstance(row, dict) and row.get("style_id"):
                rows_by_id[str(row["style_id"])] = row
        kept: list[dict[str, Any]] = []
        seen_ss: set[str] = set()
        for row in stylesheet:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("style_id") or "").strip()
            if sid in abstract:
                tgt = remap.get(sid)
                if tgt and tgt not in seen_ss and tgt not in rows_by_id:
                    cloned = dict(row)
                    cloned["style_id"] = tgt
                    kept.append(cloned)
                    seen_ss.add(tgt)
                    rows_by_id[tgt] = cloned
                    notes.append(f"stylesheet: {sid} → {tgt}（abstract 基类改为叶子）")
                else:
                    notes.append(f"stylesheet: 删除 abstract {sid}")
                continue
            if sid in seen_ss:
                continue
            seen_ss.add(sid)
            kept.append(row)
        plan["stylesheet"] = kept

    def _rewrite(rid: str) -> str:
        r = str(rid or "").strip()
        return remap.get(r, r)

    sections = plan.get("sections")
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            old = str(sec.get("role_id") or "").strip()
            new = _rewrite(old)
            if new != old:
                sec["role_id"] = new
                notes.append(f"section {sec.get('instance_id')}: {old} → {new}")
                _add_selection_role(plan.setdefault("selection", {}), new)

    outline = plan.get("outline")
    if isinstance(outline, list):
        for entry in outline:
            if not isinstance(entry, dict):
                continue
            for block in entry.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                old = str(block.get("role_id") or "").strip()
                new = _rewrite(old)
                if new != old:
                    block["role_id"] = new
                    notes.append(f"block {block.get('block_id')}: {old} → {new}")
                    _add_selection_role(plan.setdefault("selection", {}), new)
                for run in block.get("expected_runs") or []:
                    if not isinstance(run, dict):
                        continue
                    key = "role" if "role" in run else ("span_role" if "span_role" in run else None)
                    if not key:
                        continue
                    old_r = str(run.get(key) or "").strip()
                    new_r = _rewrite(old_r)
                    if new_r != old_r:
                        run[key] = new_r

    selection = plan.get("selection")
    if isinstance(selection, dict):
        for key in (
            "section_roles", "paragraph_body_roles", "paragraph_cell_roles",
            "run_roles", "image_roles",
        ):
            items = selection.get(key)
            if isinstance(items, list):
                selection[key] = _filter_selection_list(items, abstract, remap)
        tbl = selection.get("table_roles")
        if isinstance(tbl, list):
            selection["table_roles"] = _filter_selection_list(tbl, abstract, remap)
        sn = selection.setdefault("notes", [])
        if isinstance(sn, list) and remap:
            sn.append(
                "已剔除 abstract 基类，改挂叶子："
                + ", ".join(f"{a}→{b}" for a, b in sorted(remap.items()))
            )

    fe = plan.get("format_expectations")
    if isinstance(fe, list):
        cleaned_fe: list[Any] = []
        seen_fe: set[str] = set()
        for row in fe:
            if not isinstance(row, dict):
                continue
            rid = _rewrite(str(row.get("role_id") or "").strip())
            if not rid or rid in abstract or is_abstract_role_id(rid, library_data):
                continue
            if rid in seen_fe:
                continue
            seen_fe.add(rid)
            if rid != str(row.get("role_id") or ""):
                row = dict(row)
                row["role_id"] = rid
            cleaned_fe.append(row)
        plan["format_expectations"] = cleaned_fe

    diffs = plan.get("media_style_diff")
    if isinstance(diffs, list):
        plan["media_style_diff"] = [
            d for d in diffs
            if not (
                isinstance(d, dict)
                and is_abstract_role_id(str(d.get("style_id") or ""), library_data)
            )
        ]

    remaining = {r for r in _scan_role_ids(plan) if is_abstract_role_id(r, library_data)}
    if remaining:
        notes.append("仍残留 abstract: " + ", ".join(sorted(remaining)))
    if unresolved:
        notes.append("未能映射的 abstract: " + ", ".join(unresolved))

    meta = plan.setdefault("meta", {})
    if isinstance(meta, dict) and notes:
        applied = meta.setdefault("notes_applied", [])
        if isinstance(applied, list):
            applied.append(
                "strip_abstract_roles: "
                + (", ".join(f"{a}→{b}" for a, b in sorted(remap.items())) or "dropped")
            )
    return notes


def drop_abstract_role_params(
    role_params: dict[str, Any],
    library_data: dict[str, Any] | None = None,
) -> list[str]:
    """Remove T* entries for abstract bases. Mutates ``role_params``."""
    if not isinstance(role_params, dict):
        return []
    dropped = [k for k in list(role_params) if is_abstract_role_id(k, library_data)]
    for k in dropped:
        role_params.pop(k, None)
    return dropped


def write_abstract_check(
    plan: dict[str, Any],
    out_dir: Any,
    library_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strip abstract bases and write ``abstract_check.md``. Mutates ``plan``."""
    notes = strip_abstract_roles(plan, library_data)
    remaining = sorted(r for r in _scan_role_ids(plan) if is_abstract_role_id(r, library_data))
    ok = not remaining
    lines = [
        "# abstract_check",
        "",
        f"status: {'PASS' if ok else 'FAIL'}",
        f"notes: {len(notes)}",
        f"remaining: {', '.join(remaining) if remaining else '—'}",
        "",
    ]
    if notes:
        lines.append("## 处理")
        lines.extend(f"- {n}" for n in notes)
        lines.append("")
    lines.append(
        "abstract: true / `*Base` / SecFrontMatter·SecMainBody·SecBackMatter "
        "不得进入 stylesheet、selection 或 outline.role_id；应改挂非 abstract 叶子。"
    )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "abstract_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return {
        "ok": ok,
        "notes": notes,
        "remaining": remaining,
        "n_notes": len(notes),
    }
