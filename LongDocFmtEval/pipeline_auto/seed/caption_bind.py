"""Bind table/figure caption paragraphs in the step3 outline.

Most Tbl*/Img* instances need an adjacent ``ParaCaption`` (or TableTitle /
FigureTitle) block. Cover logos and cover-section images **must not** have a
caption; cover/key-value info tables omit one.

This is a plan sanitizer (insert missing blocks), not a block-count gate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_CAPTION_RID_RE = re.compile(
    r"(TableTitle|FigureTitle|FigTitle|TableNote|FigureNote|FigNote|Caption|表题|图题|表注|图注)",
    re.I,
)
_LOGO_RE = re.compile(
    r"(Logo|Emblem|Wordmark|WordMark|Crest|CoverMark|CoverLogo|CoverEmblem"
    r"|标识|徽标|司标|社标|封面标识|封面徽标)",
    re.I,
)
_COVER_INFO_RE = re.compile(
    r"(CoverInfo|CoverTable|KeyValue|FormalHeadInfo|PlanInfo|封面信息|文头信息|项目信息)",
    re.I,
)
_COVER_SEC_RE = re.compile(r"(Cover|FormalHead|文头)", re.I)


def is_caption_role(role_id: str) -> bool:
    return bool(_CAPTION_RID_RE.search(str(role_id or "")))


def is_cover_info_table_role(role_id: str) -> bool:
    """Cover / project key-value info tables (TblCoverInfo, TblCover*, …)."""
    rid = str(role_id or "")
    if not rid.startswith("Tbl"):
        return False
    if rid.startswith("TblCover"):
        return True
    return bool(_COVER_INFO_RE.search(rid))


def is_caption_exempt_media(
    role_id: str,
    *,
    section_role: str = "",
    block: dict[str, Any] | None = None,
) -> bool:
    """Cover logo / any cover-section image / cover info table: no caption."""
    rid = str(role_id or "")
    if not rid or is_caption_role(rid):
        return False
    if _LOGO_RE.search(rid):
        return True
    if _COVER_INFO_RE.search(rid):
        return True
    sec = str(section_role or "")
    if _COVER_SEC_RE.search(sec) and re.search(r"(Stamp|印章|红头)", rid, re.I):
        return True
    kind = block_media_kind(block) if isinstance(block, dict) else None
    if _COVER_SEC_RE.search(sec) and (rid.startswith("Img") or kind == "image"):
        return True
    if _COVER_SEC_RE.search(sec) and (rid.startswith("Tbl") or kind == "table") and (
        _COVER_INFO_RE.search(rid) or re.search(r"(Info|Cover|Plan)", rid, re.I)
    ):
        return True
    return False


def block_media_kind(block: dict[str, Any], roles: dict | None = None) -> str | None:
    """Return ``table`` / ``image`` for media blocks; None for captions and body."""
    if not isinstance(block, dict):
        return None
    rid = str(block.get("role_id") or "")
    if is_caption_role(rid):
        return None
    obj = ""
    if roles is not None:
        obj = str((roles.get(rid) or {}).get("object") or "")
    if obj in ("table", "image"):
        return obj
    hint = str(block.get("length_hint") or "").lower()
    if rid.startswith("Tbl") or hint == "table":
        return "table"
    if rid.startswith("Img") or hint == "image" or block.get("figure_id"):
        return "image"
    return None


def _ref_keys(block: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for k in ("table_ref", "image_ref", "figure_id"):
        v = block.get(k)
        if v:
            out.add(f"{k}:{v}")
    return out


def _caption_bound_to_exempt(
    cap: dict[str, Any],
    blocks: list[dict[str, Any]],
    idx: int,
    *,
    section_role: str,
) -> bool:
    if not is_caption_role(str(cap.get("role_id") or "")):
        return False
    cap_refs = _ref_keys(cap)
    cap_fid = cap.get("figure_id")
    neighbors: list[dict[str, Any]] = []
    if idx > 0 and isinstance(blocks[idx - 1], dict):
        neighbors.append(blocks[idx - 1])
    if idx + 1 < len(blocks) and isinstance(blocks[idx + 1], dict):
        neighbors.append(blocks[idx + 1])
    for nb in neighbors:
        if not is_caption_exempt_media(
            str(nb.get("role_id") or ""), section_role=section_role, block=nb,
        ):
            continue
        if cap_refs & _ref_keys(nb) or (cap_fid and cap_fid == nb.get("figure_id")):
            return True
        if block_media_kind(nb) in ("table", "image"):
            return True
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if not is_caption_exempt_media(
            str(b.get("role_id") or ""), section_role=section_role, block=b,
        ):
            continue
        if cap_refs & _ref_keys(b) or (cap_fid and cap_fid == b.get("figure_id")):
            return True
    return False


def _strip_exempt_captions(
    blocks: list[dict[str, Any]],
    *,
    section_role: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop caption blocks that belong to cover logo / cover info table."""
    dropped: list[str] = []
    kept: list[dict[str, Any]] = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        if _caption_bound_to_exempt(b, blocks, i, section_role=section_role):
            dropped.append(str(b.get("block_id") or b.get("role_id") or ""))
            continue
        kept.append(b)
    return kept, dropped


def media_has_bound_caption(
    media: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    roles: dict | None = None,
) -> bool:
    """True if a caption sits next to this media block or shares its ref."""
    del roles  # reserved for object lookup; adjacency is role_id-based
    if not isinstance(media, dict) or not isinstance(blocks, list):
        return False
    bid = str(media.get("block_id") or "")
    refs = _ref_keys(media)
    idx = None
    for i, b in enumerate(blocks):
        if isinstance(b, dict) and str(b.get("block_id") or "") == bid:
            idx = i
            break
        if b is media:
            idx = i
            break
    if idx is not None:
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(blocks) and isinstance(blocks[j], dict):
                if is_caption_role(str(blocks[j].get("role_id") or "")):
                    cap_refs = _ref_keys(blocks[j])
                    if not cap_refs or not refs or cap_refs & refs:
                        return True
    if refs:
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if is_caption_role(str(b.get("role_id") or "")) and refs & _ref_keys(b):
                return True
    return False


def captioned_media_block_ids(
    sections: list[dict],
    outline_map: dict[str, list[dict]],
    roles: dict | None = None,
) -> set[str]:
    ids: set[str] = set()
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        inst = sec.get("instance_id")
        blocks = list(outline_map.get(inst, []) or [])
        for b in blocks:
            if block_media_kind(b, roles) not in ("table", "image"):
                continue
            bid = str(b.get("block_id") or "")
            if bid and media_has_bound_caption(b, blocks, roles=roles):
                ids.add(bid)
    return ids


def _locale_of(plan: dict[str, Any]) -> str:
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    loc = str((meta or {}).get("locale") or "zh").lower()
    return "en" if loc.startswith("en") else "zh"


def _caption_style_id(plan: dict[str, Any]) -> str:
    stylesheet = plan.get("stylesheet") if isinstance(plan.get("stylesheet"), list) else []
    for item in stylesheet:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or "")
        obj = str(item.get("object") or "paragraph")
        if sid and is_caption_role(sid) and obj in ("paragraph", "paragraph.body", ""):
            return sid
    selection = plan.get("selection") if isinstance(plan.get("selection"), dict) else {}
    for sid in selection.get("paragraph_body_roles") or []:
        if is_caption_role(str(sid)):
            return str(sid)
    return "ParaCaption"


def _ensure_caption_style(plan: dict[str, Any], style_id: str, *, locale: str) -> list[str]:
    notes: list[str] = []
    stylesheet = plan.setdefault("stylesheet", [])
    if not isinstance(stylesheet, list):
        plan["stylesheet"] = stylesheet = []
    have = False
    for item in stylesheet:
        if not isinstance(item, dict):
            continue
        if str(item.get("style_id") or "") != style_id:
            continue
        have = True
        if item.get("title_kind"):
            item.pop("title_kind", None)
            notes.append(f"去掉 `{style_id}` 的 title_kind（题注不得当封面题）")
        item.setdefault("object", "paragraph")
        item.setdefault("display_name", "题注" if locale == "zh" else "Caption")
        break
    if not have:
        stylesheet.append({
            "style_id": style_id,
            "display_name": "题注" if locale == "zh" else "Caption",
            "object": "paragraph",
            "description": "图题、表题、说明性短题注" if locale == "zh" else "Figure/table captions",
            "expectation": (
                "居中短题注，紧随对应表/图，勿当封面主标题"
                if locale == "zh"
                else "Short centered caption beside the table/figure; not a cover title"
            ),
        })
        notes.append(f"stylesheet 补入题注样式 `{style_id}`")
    selection = plan.setdefault("selection", {})
    if isinstance(selection, dict):
        body = selection.setdefault("paragraph_body_roles", [])
        if isinstance(body, list) and style_id not in body:
            body.append(style_id)
            notes.append(f"selection.paragraph_body_roles 补入 `{style_id}`")
    return notes


def _next_block_id_fn(plan: dict[str, Any]):
    n = 0
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            m = re.match(r"B(\d+)$", str(b.get("block_id") or ""), re.I)
            if m:
                n = max(n, int(m.group(1)))

    def take() -> str:
        nonlocal n
        n += 1
        return f"B{n:03d}"

    return take


def _max_ref_n(plan: dict[str, Any], key: str, prefix: str) -> int:
    n = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$", re.I)
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            m = pat.match(str(b.get(key) or ""))
            if m:
                n = max(n, int(m.group(1)))
    return n


def _ensure_media_ref(block: dict[str, Any], kind: str, counters: dict[str, int]) -> tuple[str, str]:
    if kind == "table":
        if not block.get("table_ref"):
            counters["tbl"] += 1
            block["table_ref"] = f"TBL{counters['tbl']:02d}"
        return "table_ref", str(block["table_ref"])
    if not block.get("image_ref"):
        counters["img"] += 1
        block["image_ref"] = f"IMG{counters['img']:02d}"
    if not block.get("figure_id"):
        block["figure_id"] = block["image_ref"]
    return "image_ref", str(block["image_ref"])


def _short_caption_body(media: dict[str, Any], *, limit: int = 28) -> str:
    note = str(media.get("organization_note") or "").strip()
    mp = str(media.get("main_point") or "").strip()
    raw = note if note and len(note) <= 24 and "字段" not in note else mp
    for sep in ("字段包括", "示意：", "示意:", "。", ". "):
        if sep in raw:
            raw = raw.split(sep)[0]
    raw = re.sub(r"\s+", " ", raw).strip(" 。.;；,，")
    if len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw


def _caption_main_point(
    media: dict[str, Any],
    *,
    kind: str,
    n: int,
    locale: str,
) -> str:
    body = _short_caption_body(media)
    if not body:
        if locale == "en":
            body = "data table" if kind == "table" else "figure"
        else:
            body = "数据表" if kind == "table" else "示意图"
    if locale == "en":
        prefix = f"Table {n} " if kind == "table" else f"Figure {n} "
    else:
        prefix = f"表{n} " if kind == "table" else f"图{n} "
    text = prefix + body
    return text[:40]


def ensure_media_captions(plan: dict[str, Any]) -> dict[str, Any]:
    """Insert missing caption blocks next to body tables/figures. Mutates plan."""
    notes: list[str] = []
    inserted: list[str] = []
    bound: list[str] = []
    exempt: list[str] = []
    stripped: list[str] = []
    if not isinstance(plan, dict):
        return {
            "ok": False,
            "notes": ["plan 非对象"],
            "inserted": [],
            "bound": [],
            "exempt": [],
            "stripped": [],
            "required": 0,
            "missing": [],
        }

    locale = _locale_of(plan)
    style_id = _caption_style_id(plan)
    notes.extend(_ensure_caption_style(plan, style_id, locale=locale))

    sections = plan.get("sections") if isinstance(plan.get("sections"), list) else []
    sec_role = {
        s.get("instance_id"): str(s.get("role_id") or "")
        for s in sections
        if isinstance(s, dict) and s.get("instance_id")
    }
    outline = plan.get("outline") if isinstance(plan.get("outline"), list) else []
    take_id = _next_block_id_fn(plan)
    counters = {
        "tbl": _max_ref_n(plan, "table_ref", "TBL"),
        "img": _max_ref_n(plan, "image_ref", "IMG"),
    }
    table_n = 0
    figure_n = 0

    for entry in outline:
        if not isinstance(entry, dict):
            continue
        inst = entry.get("instance_id")
        sec = sec_role.get(inst, "")
        blocks = list(entry.get("blocks") or [])
        if not blocks:
            continue
        blocks, dropped = _strip_exempt_captions(blocks, section_role=sec)
        for bid in dropped:
            stripped.append(bid)
            notes.append(f"去掉封面豁免媒体旁题注 {bid}")
        new_blocks: list[dict[str, Any]] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            new_blocks.append(b)
            kind = block_media_kind(b)
            if kind not in ("table", "image"):
                continue
            rid = str(b.get("role_id") or "")
            bid = str(b.get("block_id") or rid)
            if is_caption_exempt_media(rid, section_role=sec, block=b):
                exempt.append(bid)
                continue
            if kind == "table":
                table_n += 1
                seq = table_n
                org = "表题" if locale == "zh" else "table caption"
            else:
                figure_n += 1
                seq = figure_n
                org = "图题" if locale == "zh" else "figure caption"
            if media_has_bound_caption(b, blocks):
                bound.append(bid)
                continue
            ref_key, ref_val = _ensure_media_ref(b, kind, counters)
            cap = {
                "block_id": take_id(),
                "role_id": style_id,
                "main_point": _caption_main_point(b, kind=kind, n=seq, locale=locale),
                "organization_note": org,
                "length_hint": "short",
                ref_key: ref_val,
            }
            if b.get("template_exhibit") is True:
                cap["template_exhibit"] = True
            elif b.get("template_exhibit") is False:
                cap["template_exhibit"] = False
            if kind == "image" and b.get("figure_id"):
                cap["figure_id"] = b.get("figure_id")
            new_blocks.append(cap)
            inserted.append(f"{bid}→{cap['block_id']}")
            notes.append(f"为 {bid} `{rid}` 补题注 {cap['block_id']}")
        entry["blocks"] = new_blocks

    # recount after inserts (skip exempt)
    missing: list[str] = []
    required = 0
    for entry in outline:
        if not isinstance(entry, dict):
            continue
        inst = entry.get("instance_id")
        blocks = list(entry.get("blocks") or [])
        for b in blocks:
            kind = block_media_kind(b)
            if kind not in ("table", "image"):
                continue
            rid = str(b.get("role_id") or "")
            bid = str(b.get("block_id") or rid)
            if is_caption_exempt_media(rid, section_role=sec_role.get(inst, ""), block=b):
                continue
            required += 1
            if not media_has_bound_caption(b, blocks):
                missing.append(bid)

    meta = plan.setdefault("meta", {})
    if isinstance(meta, dict):
        applied = meta.setdefault("notes_applied", [])
        if isinstance(applied, list) and (inserted or stripped):
            if inserted:
                applied.append(f"caption_bind: inserted {len(inserted)} caption block(s)")
            if stripped:
                applied.append(f"caption_bind: stripped {len(stripped)} cover caption(s)")

    ok = not missing
    return {
        "ok": ok,
        "caption_style": style_id,
        "required": required,
        "bound": bound,
        "exempt": exempt,
        "stripped": stripped,
        "inserted": inserted,
        "missing": missing,
        "notes": notes,
        "n_bound": len(bound) + len(inserted),
        "n_exempt": len(exempt),
        "n_inserted": len(inserted),
        "n_stripped": len(stripped),
    }


def format_caption_check_md(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("ok") else "FAIL"
    lines = [
        f"# Caption-bind check · {status}",
        "",
        "正文表/图须绑定题注段落；封面 logo / 封面节内图片 **禁止**题注；封面信息表不要题注。",
        "",
        f"- 题注样式：`{report.get('caption_style') or 'ParaCaption'}`",
        f"- 需题注的表/图：{report.get('required')}",
        f"- 已绑定（含补插）：{report.get('n_bound')}",
        f"- 豁免（封面 logo/信息表）：{report.get('n_exempt')}",
        f"- 本步去掉封面题注：{report.get('n_stripped') or 0}",
        f"- 本步补插：{report.get('n_inserted')}",
        "",
    ]
    exempt = report.get("exempt") or []
    if exempt:
        lines.append("## 豁免")
        lines.append("")
        for bid in exempt:
            lines.append(f"- `{bid}`")
        lines.append("")
    stripped = report.get("stripped") or []
    if stripped:
        lines.append("## 去掉的封面题注")
        lines.append("")
        for bid in stripped:
            lines.append(f"- `{bid}`")
        lines.append("")
    inserted = report.get("inserted") or []
    if inserted:
        lines.append("## 补插")
        lines.append("")
        for item in inserted:
            lines.append(f"- {item}")
        lines.append("")
    missing = report.get("missing") or []
    if missing:
        lines.append("## 仍缺题注")
        lines.append("")
        for bid in missing:
            lines.append(f"- `{bid}`")
        lines.append("")
    notes = report.get("notes") or []
    if notes:
        lines.append("## 备注")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_caption_check(plan: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    report = ensure_media_captions(plan)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "caption_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "caption_check.md").write_text(
        format_caption_check_md(report), encoding="utf-8",
    )
    return report
