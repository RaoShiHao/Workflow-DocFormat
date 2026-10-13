"""Step5 template specimen: short style↔content bindings per Sec* role.

template.docx is a format specimen, not a miniature of the full paper and not
step6 ``document.docx``. Coverage target is the **step3 3e-trimmed stylesheet**:
styles that already appear on outline blocks / expected_runs (what document.docx
will use). Per-section retry checks that section's source-block styles; a final
pass only fills styles that gold already uses but the specimen still missed.

Img*/Tbl*: once per style inside a section, and once globally across Sec*
(captions follow each *distinct* media style, not repeat instances).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from LongDocFmtEval.common.llm import load_prompt, render_template
from LongDocFmtEval.common.role_library import lenient_json_load
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path, resolve_work_file
from LongDocFmtEval.pipeline_auto.seed.caption_bind import is_caption_exempt_media, is_caption_role
from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import (
    apply_section_page_continuity,
    is_section_style_block,
)
from LongDocFmtEval.pipeline_auto.seed.writing_outline import (
    first_body_style_id,
    format_heading_style_map,
    format_stylesheet_catalog,
    stylesheet_style_ids,
    uniquify_block_ids,
)

# Per-Sec* specimen LLM budget (short style↔content JSON, not full step6 prose).
DEFAULT_SPECIMEN_MAX_TOKENS = 10480
SPECIMEN_MAX_TOKENS_CAP = 20480


def _specimen_token_budget(base: int, n_must: int) -> int:
    """Chunk budget from must-style count; never above ``SPECIMEN_MAX_TOKENS_CAP``."""
    need = 2500 + max(0, int(n_must)) * 400
    return min(max(int(base), need), SPECIMEN_MAX_TOKENS_CAP)


SPECIMEN_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step5_template_specimen",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["blocks"],
            "properties": {
                "instance_id": {"type": "string"},
                "blocks": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["role_id", "main_point"],
                        "properties": {
                            "block_id": {"type": "string"},
                            "role_id": {"type": "string"},
                            "main_point": {"type": "string"},
                            "organization_note": {"type": "string"},
                            "length_hint": {"type": "string"},
                            "expected_runs": {"type": "array"},
                            "table_ref": {"type": ["object", "string", "null"]},
                            "image_ref": {"type": ["object", "string", "null"]},
                            "figure_id": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
    },
}


def _obj(rid: str, roles: dict, stylesheet_by_id: dict | None = None) -> str:
    spec = (roles or {}).get(rid) or {}
    obj = str(spec.get("object") or "").lower()
    if obj:
        return obj
    item = (stylesheet_by_id or {}).get(rid) or {}
    return str(item.get("object") or "").lower()


def _stylesheet_by_id(stylesheet: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if sid:
            out[sid] = item
    return out


# Chrome keys under Tbl*.cells — border/fill slots, NOT paragraph style_ids.
_TABLE_CHROME_KEYS = frozenset({
    "label", "value", "header", "data", "stub", "note", "remark",
    "row_last", "row_first", "total", "footer",
})


def is_cell_style(rid: str, roles: dict | None = None, stylesheet_by_id: dict | None = None) -> bool:
    if not rid:
        return False
    if str(rid) in _TABLE_CHROME_KEYS:
        return True
    obj = _obj(rid, roles or {}, stylesheet_by_id)
    if "cell" in obj:
        return True
    return bool(
        rid.startswith("ParaTbl") and "Cell" in rid
        or rid.endswith("Cell")
    )


def is_media_style(rid: str, roles: dict | None = None, stylesheet_by_id: dict | None = None) -> bool:
    if not rid:
        return False
    obj = _obj(rid, roles or {}, stylesheet_by_id)
    if obj.startswith("table") or obj in {"image", "picture"}:
        return True
    return str(rid).startswith("Img") or str(rid).startswith("Tbl")


def is_run_style(rid: str, roles: dict | None = None, stylesheet_by_id: dict | None = None) -> bool:
    if not rid:
        return False
    obj = _obj(rid, roles or {}, stylesheet_by_id)
    if obj == "run":
        return True
    return str(rid).startswith("Run")


def is_heading_style(rid: str, roles: dict | None = None, stylesheet_by_id: dict | None = None) -> bool:
    item = (stylesheet_by_id or {}).get(rid) or {}
    if item.get("heading_level") not in (None, "", 0):
        return True
    spec = (roles or {}).get(rid) or {}
    props = spec.get("props") if isinstance(spec.get("props"), dict) else {}
    if "outlineLvl" in props:
        return True
    return "Heading" in str(rid)


def table_cell_style_ids(table_rid: str, stylesheet_by_id: dict, roles: dict | None = None) -> set[str]:
    """Paragraph styles (Para*Cell) bound to a table — never chrome keys like label/header/data."""
    ids: set[str] = set()

    def _add_para(raw: Any) -> None:
        if isinstance(raw, str) and raw.strip():
            sid = raw.strip()
            if sid in _TABLE_CHROME_KEYS:
                return
            if sid.startswith("Para") or sid.endswith("Cell"):
                ids.add(sid)
        elif isinstance(raw, dict):
            para = str(raw.get("paragraph") or raw.get("role_id") or "").strip()
            if para and para not in _TABLE_CHROME_KEYS:
                ids.add(para)

    item = stylesheet_by_id.get(table_rid) or {}
    pmap = item.get("position_map") or {}
    if isinstance(pmap, dict):
        for v in pmap.values():
            _add_para(v)
    spec = (roles or {}).get(table_rid) or {}
    props = spec.get("props") if isinstance(spec.get("props"), dict) else {}
    cells = (props or {}).get("cells") if isinstance(props, dict) else None
    if isinstance(cells, dict):
        for v in cells.values():
            _add_para(v)
    return ids


def iter_run_ids(block: dict) -> list[str]:
    out: list[str] = []
    for er in block.get("expected_runs") or []:
        if isinstance(er, str) and er.strip():
            out.append(er.strip())
        elif isinstance(er, dict):
            rid = str(er.get("role") or er.get("role_id") or "").strip()
            if rid:
                out.append(rid)
    return out


def styles_in_blocks(
    blocks: list[dict],
    roles: dict,
    stylesheet_by_id: dict,
) -> set[str]:
    covered: set[str] = set()
    for b in blocks or []:
        if not isinstance(b, dict) or is_section_style_block(b, roles):
            continue
        rid = str(b.get("role_id") or "").strip()
        if not rid:
            continue
        covered.add(rid)
        covered.update(iter_run_ids(b))
        if _obj(rid, roles, stylesheet_by_id).startswith("table"):
            covered |= table_cell_style_ids(rid, stylesheet_by_id, roles)
    return covered


def required_body_styles(
    source_blocks: list[dict],
    roles: dict,
    stylesheet_by_id: dict,
) -> set[str]:
    """Styles this Sec* group's outline blocks/runs must demonstrate (no Sec*, no cell)."""
    raw = styles_in_blocks(source_blocks, roles, stylesheet_by_id)
    out: set[str] = set()
    for sid in raw:
        if is_section_style_block({"role_id": sid}, roles):
            continue
        if is_cell_style(sid, roles, stylesheet_by_id):
            continue
        out.add(sid)
    return out


def outline_used_styles(plan: dict, roles: dict) -> set[str]:
    """style_id that actually appear on outline blocks or expected_runs (not merely stylesheet)."""
    by_id = _stylesheet_by_id(plan.get("stylesheet") or [])
    blocks: list[dict] = []
    for o in plan.get("outline") or []:
        if isinstance(o, dict):
            blocks.extend(b for b in (o.get("blocks") or []) if isinstance(b, dict))
    return required_body_styles(blocks, roles, by_id)


def pick_orphan_host_index(groups: list[dict]) -> int:
    """First non-cover/toc section; catalog orphans are demonstrated there, not dumped at the end."""
    coverish = re.compile(
        r"(Cover|Toc|TOC|FrontMatter|FormalHead|目录|封面|文头)",
        re.I,
    )
    for i, g in enumerate(groups or []):
        if not coverish.search(str(g.get("sec_role") or "")):
            return i
    return max(0, len(groups) - 1) if groups else 0


def media_styles_in_blocks(
    blocks: list[dict],
    roles: dict,
    stylesheet_by_id: dict,
) -> set[str]:
    out: set[str] = set()
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "").strip()
        if is_media_style(rid, roles, stylesheet_by_id):
            out.add(rid)
    return out


def _append_run(block: dict, sid: str) -> None:
    """Attach a Run* id; prefer ``{role, text}`` so materialize can fill the substring."""
    er = block.setdefault("expected_runs", [])
    if not isinstance(er, list):
        block["expected_runs"] = [{"role": sid, "text": ""}]
        return
    if sid in iter_run_ids(block):
        return
    er.append({"role": sid, "text": ""})


_LEAD_LABEL_RE = re.compile(r"^([^\n。；;！!？?]{1,24}[：:])")


def _run_means(role_id: str, roles: dict) -> str:
    """Map Run* → means bucket aligned with step6 write_content table."""
    rid = str(role_id or "")
    props = ((roles or {}).get(rid) or {}).get("props") or {}
    if not isinstance(props, dict):
        props = {}
    if props.get("superscript") or props.get("subscript") or "Citation" in rid or "Super" in rid:
        return "superscript"
    if props.get("underline") or "Blank" in rid or "Fill" in rid or "Underline" in rid:
        return "underline"
    if props.get("italic") or "Italic" in rid or "Term" in rid:
        return "italic"
    font = props.get("font")
    if isinstance(font, dict) and any(font.get(k) for k in ("ea", "latin", "cs", "ascii")):
        return "font"
    if "Font" in rid or "Person" in rid or "Latin" in rid or "Name" in rid:
        return "font"
    if "Lead" in rid or "BoldLead" in rid or rid.endswith("Bold") or (
        props.get("bold") and ("Lead" in rid or "Bold" in rid)
    ):
        return "lead_bold"
    if props.get("bold"):
        return "bold"
    return "other"


def _default_run_frag(role_id: str, means: str, *, locale: str, example: str) -> str:
    zh = str(locale or "zh").lower().startswith("zh")
    ex = str(example or "").strip()
    if ex and len(ex) <= 24 and "\n" not in ex:
        # Prefer role example when it looks like a span (esp. lead labels / terms)
        if means in ("lead_bold", "bold") and (ex.endswith((":", "：")) or len(ex) <= 12):
            return ex if ex.endswith((":", "：")) else (ex + ("：" if zh else ":"))
        if means in ("italic", "font", "underline", "superscript") and len(ex) <= 24:
            return ex
    if means in ("lead_bold", "bold"):
        return "说明：" if zh else "Note:"
    if means == "italic":
        return "关键术语" if zh else "key term"
    if means == "underline":
        return "______"
    if means == "superscript":
        return "¹"
    if means == "font":
        return (ex[:8] if ex else ("专名" if zh else "Name"))
    return (ex[:12] if ex else ("强调" if zh else "emphasis"))


def _insert_frag_into_text(text: str, frag: str) -> str:
    """Ensure ``frag`` appears in ``text`` with minimal rewrite (specimen stays short)."""
    t = str(text or "")
    f = str(frag or "")
    if not f:
        return t
    if f in t:
        return t
    t = t.strip()
    if not t:
        return f
    # Prefer after first comma/顿号/space clause; else prefix.
    for sep in ("，", ",", "、", " "):
        i = t.find(sep)
        if 0 < i < min(40, len(t) - 1):
            return t[: i + len(sep)] + f + (" " if sep == " " else "") + t[i + len(sep) :]
    return f + (" " if not f.endswith((":", "：")) else "") + t


def _extract_run_role_text(er: Any) -> tuple[str, str]:
    if isinstance(er, str):
        return er.strip(), ""
    if not isinstance(er, dict):
        return "", ""
    role = str(
        er.get("role") or er.get("role_id") or er.get("style_id") or er.get("span_role") or ""
    ).strip()
    # Mistaken nested block objects (bad LLM / fold): do not treat main_point as run text
    looks_like_block = (
        er.get("length_hint") is not None
        or er.get("block_id") is not None
        or (isinstance(er.get("expected_runs"), list) and er.get("expected_runs"))
    )
    text = str(er.get("text") or er.get("substr") or "").strip()
    if looks_like_block and not text:
        return role, ""
    if not text and er.get("main_point") and not looks_like_block:
        text = str(er.get("main_point") or "").strip()
    return role, text


def materialize_specimen_runs(
    blocks: list[dict],
    roles: dict,
    *,
    locale: str = "zh",
    stylesheet_by_id: dict | None = None,
) -> dict[str, int]:
    """Normalize specimen ``expected_runs`` to step6-shaped ``{role,text}`` with text∈main_point.

    Keeps Sec*/minimal-coverage skeleton; only rewrites short spans / prefixes when needed
    so template.docx actually emits visible character runs (no silent drop at render).
    """
    by_id = stylesheet_by_id or {}
    loc = "en" if str(locale or "").lower().startswith("en") else "zh"
    stats = {"blocks": 0, "runs_kept": 0, "runs_fixed": 0, "runs_dropped": 0, "text_rewrites": 0}
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "").strip()
        if not rid:
            continue
        if (
            is_media_style(rid, roles, by_id)
            or is_section_style_block(b, roles)
            or is_cell_style(rid, roles, by_id)
        ):
            continue
        raw_ers = b.get("expected_runs")
        if not raw_ers:
            continue
        if not isinstance(raw_ers, list):
            continue
        stats["blocks"] += 1
        mp = str(b.get("main_point") or "")
        mp_orig = mp
        out: list[dict] = []
        seen_roles: set[str] = set()
        for er in raw_ers:
            role, frag = _extract_run_role_text(er)
            if not role or not is_run_style(role, roles, by_id):
                stats["runs_dropped"] += 1
                continue
            if role in seen_roles:
                continue
            means = _run_means(role, roles)
            example = str(((roles or {}).get(role) or {}).get("example") or "").strip()
            fixed = False
            if frag and frag not in mp:
                frag = ""
                fixed = True
            if not frag:
                fixed = True
                m = _LEAD_LABEL_RE.match(mp.strip())
                if means in ("lead_bold", "bold") and m:
                    frag = m.group(1)
                elif example and example in mp:
                    frag = example
                else:
                    frag = _default_run_frag(role, means, locale=loc, example=example)
                    if means in ("lead_bold", "bold"):
                        if not frag.endswith((":", "：")):
                            frag = frag + ("：" if loc == "zh" else ":")
                        if not _LEAD_LABEL_RE.match(mp.strip()):
                            rest = mp.lstrip()
                            if loc == "en" and frag.endswith(":") and rest and not rest[0].isspace():
                                mp = f"{frag} {rest}"
                            else:
                                mp = frag + rest
                        elif frag not in mp:
                            # already has some label — use existing match
                            m2 = _LEAD_LABEL_RE.match(mp.strip())
                            frag = m2.group(1) if m2 else frag
                            if frag not in mp:
                                rest = mp.lstrip()
                                if loc == "en" and frag.endswith(":") and rest and not rest[0].isspace():
                                    mp = f"{frag} {rest}"
                                else:
                                    mp = frag + rest
                    elif means == "superscript":
                        if frag not in mp:
                            mp = mp.rstrip() + frag
                    else:
                        mp = _insert_frag_into_text(mp, frag)
            if not frag or frag not in mp:
                stats["runs_dropped"] += 1
                continue
            out.append({"role": role, "text": frag})
            seen_roles.add(role)
            if fixed:
                stats["runs_fixed"] += 1
            else:
                stats["runs_kept"] += 1
        if mp != mp_orig:
            stats["text_rewrites"] += 1
            # Keep specimen short (sanitize used 160; allow slight growth for lead prefix)
            b["main_point"] = mp[:200] if len(mp) > 200 else mp
            # If truncate broke a frag, drop broken runs
            mp = b["main_point"]
            out = [er for er in out if er["text"] in mp]
        else:
            b["main_point"] = mp
        b["expected_runs"] = out
    return stats


def fold_run_style_blocks(
    blocks: list[dict],
    roles: dict,
    stylesheet_by_id: dict,
) -> list[dict]:
    """Run* are character styles: fold fake paragraph blocks into expected_runs."""
    out: list[dict] = []
    pending: list[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "").strip()
        if is_run_style(rid, roles, stylesheet_by_id):
            if out:
                _append_run(out[-1], rid)
            else:
                pending.append(rid)
            continue
        out.append(b)
        if pending:
            for sid in pending:
                _append_run(out[-1], sid)
            pending.clear()
    return out


def dedupe_media_blocks(
    blocks: list[dict],
    *,
    already: set[str],
    roles: dict,
    stylesheet_by_id: dict,
    section_role: str = "",
) -> list[dict]:
    """Keep each Img*/Tbl* once here; drop styles already shown in earlier Sec*."""
    seen: set[str] = set(already or ())
    out: list[dict] = []
    skip_caption = False
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "").strip()
        if is_media_style(rid, roles, stylesheet_by_id):
            if rid in seen:
                skip_caption = not is_caption_exempt_media(rid, section_role=section_role)
                continue
            seen.add(rid)
            skip_caption = False
            out.append(b)
            continue
        if skip_caption and is_caption_role(rid):
            continue
        skip_caption = False
        out.append(b)
    return out


def group_sections_by_role(plan: dict) -> list[dict[str, Any]]:
    outline_map = {
        o.get("instance_id"): list(o.get("blocks") or [])
        for o in (plan.get("outline") or [])
        if isinstance(o, dict)
    }
    groups: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for sec in plan.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        rid = str(sec.get("role_id") or "").strip()
        inst = sec.get("instance_id")
        if not rid:
            continue
        if rid not in index:
            index[rid] = len(groups)
            groups.append({
                "sec_role": rid,
                "section": dict(sec),
                "instances": [],
                "titles": [],
                "blocks": [],
            })
        g = groups[index[rid]]
        g["instances"].append(inst)
        g["titles"].append(str(sec.get("title") or rid))
        g["blocks"].extend(outline_map.get(inst) or [])
    return groups


def _block_sketch(
    blocks: list[dict],
    *,
    limit: int = 80,
    skip_media: set[str] | None = None,
    roles: dict | None = None,
    stylesheet_by_id: dict | None = None,
) -> str:
    skip_media = set(skip_media or ())
    lines: list[str] = []
    if skip_media:
        lines.append(
            "以下 Img*/Tbl* 已在前节标本示范，本节不要再放："
            + "、".join(sorted(skip_media))
        )
        lines.append("")
    counts: dict[str, int] = defaultdict(int)
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "")
        counts[rid] += 1
    lines.append("出现次数（仅供参考，标本不要按次数铺开；同 Img*/Tbl* 本节只留 1 次）：")
    for rid, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if rid.startswith("Sec"):
            continue
        note = " ← 前节已示范，跳过" if rid in skip_media else ""
        lines.append(f"  {rid} ×{n}{note}")
    lines.append("")
    lines.append("原文块顺序（压缩；同 style 连续只留首条+计数）：")
    prev = ""
    streak = 0
    shown = 0
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        rid = str(b.get("role_id") or "")
        if rid.startswith("Sec"):
            continue
        if skip_media and is_media_style(rid, roles or {}, stylesheet_by_id):
            if rid in skip_media:
                continue
        mp = str(b.get("main_point") or "").replace("\n", " ")[:36]
        if rid == prev:
            streak += 1
            continue
        if streak > 1:
            lines.append(f"    … 同上 {rid} 共 {streak} 条，标本勿照抄")
        lines.append(f"  - {rid}: {mp}")
        shown += 1
        prev = rid
        streak = 1
        if shown >= limit:
            lines.append("  …")
            break
    if streak > 1 and shown < limit:
        lines.append(f"    … 同上 {prev} 共 {streak} 条，标本勿照抄")
    return "\n".join(lines)


def format_must_styles(style_ids: set[str], stylesheet_by_id: dict, roles: dict) -> str:
    lines = ["| style_id | object | name |", "|---|---|---|"]
    for sid in sorted(style_ids):
        item = stylesheet_by_id.get(sid) or {}
        spec = (roles or {}).get(sid) or {}
        obj = str(item.get("object") or spec.get("object") or "")
        name = str(item.get("display_name") or spec.get("display_name") or "")
        lines.append(f"| {sid} | {obj} | {name} |")
    return "\n".join(lines) if len(lines) > 2 else "（无）"


def parse_specimen_blocks(text: str, instance_id: str) -> list[dict]:
    obj = lenient_json_load(text)
    if not isinstance(obj, dict):
        raise ValueError("标本 JSON 不是对象")
    blocks = obj.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError(f"{instance_id}: 未解析到非空 blocks[]")
    clean: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if not b.get("role_id") and b.get("style_id"):
            b["role_id"] = b.get("style_id")
        if b.get("role_id"):
            clean.append(b)
    if not clean:
        raise ValueError(f"{instance_id}: blocks 没有有效 role_id")
    return clean


def attach_donors(blocks: list[dict], source: list[dict], roles: dict) -> None:
    """Copy figure_id / table_ref / image_ref / runs from the first unused source block of the same role."""
    unused: dict[str, list[dict]] = defaultdict(list)
    for b in source or []:
        if isinstance(b, dict) and b.get("role_id"):
            unused[str(b["role_id"])].append(b)
    taken: set[str] = set()
    for b in blocks:
        rid = str(b.get("role_id") or "")
        donor = None
        for cand in unused.get(rid) or []:
            cid = str(cand.get("block_id") or "")
            if cid and cid in taken:
                continue
            donor = cand
            if cid:
                taken.add(cid)
            break
        if not donor:
            continue
        for key in ("table_ref", "image_ref", "figure_id", "expected_runs"):
            if not b.get(key) and donor.get(key) not in (None, "", [], {}):
                b[key] = donor.get(key)


def sanitize_specimen_blocks(
    blocks: list[dict],
    *,
    instance_id: str,
    allowed: set[str],
    roles: dict,
    body_fallback: str,
) -> list[dict]:
    out: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if is_section_style_block(b, roles):
            continue
        rid = str(b.get("role_id") or "").strip()
        # Chrome keys (label/value/header/…) must never become body paragraphs
        if rid in _TABLE_CHROME_KEYS or is_cell_style(rid, roles):
            continue
        if rid and rid not in allowed:
            rid = body_fallback
            b["role_id"] = rid
        if not rid:
            continue
        mp = str(b.get("main_point") or "").strip()
        b["main_point"] = mp[:160] if mp else rid
        out.append(b)
    uniquify_block_ids(instance_id, out)
    return out


def greedy_cover(
    source: list[dict],
    *,
    required: set[str],
    roles: dict,
    stylesheet_by_id: dict,
    section_role: str = "",
    already_shown_media: set[str] | None = None,
) -> list[dict]:
    """Deterministic fallback: keep new styles; keep caption after each new media style."""
    kept: list[dict] = []
    seen: set[str] = set()
    pending_media: dict | None = None
    heading_seen: set[str] = set()
    already_media = set(already_shown_media or ())

    def _keep(b: dict) -> None:
        kept.append(json.loads(json.dumps(b, ensure_ascii=False)))

    for b in source or []:
        if not isinstance(b, dict) or is_section_style_block(b, roles):
            continue
        rid = str(b.get("role_id") or "")
        if not rid:
            continue
        obj = _obj(rid, roles, stylesheet_by_id)
        if obj.startswith("table") or obj == "image":
            if rid in already_media:
                continue
            if rid not in seen:
                _keep(b)
                seen.add(rid)
                if not is_caption_exempt_media(rid, section_role=section_role):
                    pending_media = b
            continue
        if is_caption_role(rid) and pending_media is not None:
            _keep(b)
            seen.add(rid)
            pending_media = None
            continue
        if is_heading_style(rid, roles, stylesheet_by_id):
            if rid not in heading_seen:
                _keep(b)
                heading_seen.add(rid)
                seen.add(rid)
            pending_media = None
            continue
        if rid not in seen:
            _keep(b)
            seen.add(rid)
            pending_media = None
        if required <= seen:
            break
    if required - seen:
        by_role: dict[str, dict] = {}
        for b in source or []:
            if isinstance(b, dict) and b.get("role_id"):
                by_role.setdefault(str(b["role_id"]), b)
        for sid in sorted(required - seen):
            if is_media_style(sid, roles, stylesheet_by_id) and sid in already_media:
                continue
            donor = by_role.get(sid)
            if donor:
                _keep(donor)
            else:
                kept.append({"role_id": sid, "main_point": sid, "length_hint": "short"})
            seen.add(sid)
    return kept


def inject_missing(
    blocks: list[dict],
    missing: set[str],
    source: list[dict],
    stylesheet_by_id: dict,
    roles: dict,
) -> int:
    by_role: dict[str, dict] = {}
    for b in source or []:
        if isinstance(b, dict) and b.get("role_id"):
            by_role.setdefault(str(b["role_id"]), b)
    n = 0
    for sid in sorted(missing):
        if sid in _TABLE_CHROME_KEYS or is_cell_style(sid, roles, stylesheet_by_id):
            continue
        donor = by_role.get(sid)
        if donor:
            blocks.append(json.loads(json.dumps(donor, ensure_ascii=False)))
        else:
            obj = _obj(sid, roles, stylesheet_by_id)
            if is_run_style(sid, roles, stylesheet_by_id) and blocks:
                attached = False
                for b in reversed(blocks):
                    rid = str(b.get("role_id") or "")
                    if (
                        is_run_style(rid, roles, stylesheet_by_id)
                        or is_media_style(rid, roles, stylesheet_by_id)
                        or is_caption_role(rid)
                        or is_heading_style(rid, roles, stylesheet_by_id)
                        or is_section_style_block(b, roles)
                    ):
                        continue
                    _append_run(b, sid)
                    attached = True
                    break
                if not attached:
                    _append_run(blocks[-1], sid)
                n += 1
                continue
            item = stylesheet_by_id.get(sid) or {}
            spec = (roles or {}).get(sid) or {}
            name = str(item.get("display_name") or spec.get("display_name") or sid)
            block: dict[str, Any] = {
                "role_id": sid,
                "main_point": name[:80],
                "length_hint": "short",
            }
            obj = _obj(sid, roles, stylesheet_by_id)
            if obj.startswith("table"):
                block["table_ref"] = {}
            elif obj == "image":
                block["image_ref"] = {}
            blocks.append(block)
        n += 1
    return n


def _roles_from_template(out_dir: Path, plan: dict) -> dict:
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415

    roles = load_merged_roles(out_dir)
    if roles:
        return roles
    out: dict[str, dict] = {}
    for item in plan.get("stylesheet") or []:
        if isinstance(item, dict) and item.get("style_id"):
            out[str(item["style_id"])] = {
                "object": item.get("object") or "",
                "display_name": item.get("display_name") or "",
                "description": item.get("description") or "",
            }
    return out


def _generate_group_blocks(
    *,
    group: dict,
    plan: dict,
    roles: dict,
    locale: str,
    theme: str,
    temperature: float,
    max_tokens: int,
    dump_dir: Path,
    extra_required: set[str] | None = None,
    already_shown_media: set[str] | None = None,
) -> list[dict]:
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    prompt = load_prompt(prompt_path(loc, "step5_template_specimen.yaml", fallbacks=("step4_template_specimen.yaml",)))
    stylesheet = plan.get("stylesheet") or []
    by_id = _stylesheet_by_id(stylesheet)
    allowed = stylesheet_style_ids(stylesheet)
    body_fallback = first_body_style_id(stylesheet)
    inst = str(group["section"].get("instance_id") or "T01")
    already_shown = set(already_shown_media or ())
    extra_req = set(extra_required or ())
    required = required_body_styles(group["blocks"], roles, by_id) | extra_req
    required = {
        s for s in required
        if not (is_media_style(s, roles, by_id) and s in already_shown)
    }
    if not any(is_media_style(s, roles, by_id) for s in required):
        required = {s for s in required if not is_caption_role(s) or s in extra_req}
    catalog = format_must_styles(required, by_id, roles)
    extra_styles = sorted(
        sid for sid in allowed
        if not str(sid).startswith("Sec")
        and not is_cell_style(str(sid), roles, by_id)
        and sid not in required
        and not is_section_style_block({"role_id": sid}, roles)
    )
    extra_txt = "、".join(extra_styles[:40]) if extra_styles else (
        "(none)" if loc == "en" else "（无）"
    )
    already_txt = "、".join(sorted(already_shown)) if already_shown else (
        "(none)" if loc == "en" else "（无）"
    )

    base_tok = _specimen_token_budget(max_tokens, len(required))
    retries = 3

    def _call(feedback: str, label: str, *, attempt_tokens: int) -> str:
        retry = ""
        if feedback.strip():
            retry = ("## Previous attempt failed; fix only this section's blocks\n"
                     if loc == "en" else
                     "## 上次未通过，请只改正本节 blocks\n") + feedback.strip()
        user = render_template(
            prompt["user_template"],
            instance_id=inst,
            sec_role=group["sec_role"],
            section_titles="；".join(group["titles"][:8]),
            instance_ids="、".join(str(x) for x in group["instances"][:12]),
            must_styles=catalog,
            extra_styles=extra_txt,
            already_shown_media=already_txt,
            heading_style_map=format_heading_style_map(stylesheet),
            block_sketch=_block_sketch(
                group["blocks"],
                skip_media=already_shown,
                roles=roles,
                stylesheet_by_id=by_id,
            ),
            stylesheet_catalog=format_stylesheet_catalog(stylesheet),
            theme=theme or "",
            retry_feedback=retry or ("(none)" if loc == "en" else "（无）"),
        )
        tok = max(1024, int(attempt_tokens))
        print(f"    [specimen {inst}/{group['sec_role']}] {label} max_tokens={tok}")
        return stream_llm_text(
            system=prompt["system"],
            user=user,
            temperature=temperature,
            max_tokens=tok,
            label=label,
            response_format=SPECIMEN_RESPONSE_FORMAT,
        )

    def _finish(raw_blocks: list[dict]) -> list[dict]:
        out = sanitize_specimen_blocks(
            raw_blocks, instance_id=inst, allowed=allowed, roles=roles, body_fallback=body_fallback,
        )
        out = fold_run_style_blocks(out, roles, by_id)
        out = dedupe_media_blocks(
            out, already=already_shown, roles=roles, stylesheet_by_id=by_id,
            section_role=group["sec_role"],
        )
        attach_donors(out, group["blocks"], roles)
        st = materialize_specimen_runs(out, roles, locale=locale, stylesheet_by_id=by_id)
        if st.get("runs_fixed") or st.get("text_rewrites"):
            print(
                f"    [specimen {inst}] runs materialize "
                f"kept={st['runs_kept']} fixed={st['runs_fixed']} "
                f"drop={st['runs_dropped']} rewrite_blocks={st['text_rewrites']}"
            )
        return out

    def _greedy() -> list[dict]:
        return greedy_cover(
            group["blocks"], required=required, roles=roles,
            stylesheet_by_id=by_id, section_role=group["sec_role"],
            already_shown_media=already_shown,
        )

    dump_dir.mkdir(parents=True, exist_ok=True)
    raw_path = dump_dir / f"{inst}.raw.txt"
    blocks: list[dict] | None = None
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        if attempt == 1:
            attempt_tokens = base_tok
            feedback = ""
            label = f"template_specimen_{inst}"
        else:
            attempt_tokens = min(
                int(base_tok * (1.0 + 0.25 * (attempt - 1))),
                SPECIMEN_MAX_TOKENS_CAP,
            )
            feedback = f"JSON parse error: {last_exc}"
            label = f"template_specimen_{inst}_parse{attempt}"
            print(
                f"[retry] specimen {inst} 解析失败: {last_exc}"
                f"（第 {attempt}/{retries} 次，max_tokens={attempt_tokens}）"
            )
        raw = _call(feedback, label, attempt_tokens=attempt_tokens)
        raw_path.write_text(raw, encoding="utf-8")
        try:
            blocks = parse_specimen_blocks(raw, inst)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if blocks is None:
        print(f"[warn] specimen {inst} 仍无法解析，改用程序覆盖：{last_exc}")
        blocks = _greedy()

    blocks = _finish(blocks)
    covered = styles_in_blocks(blocks, roles, by_id)
    miss = required - covered
    if miss:
        fb = (
            f"Missing required style_id(s) in this section specimen: {sorted(miss)}. "
            "Add blocks using only catalog ids; same Img*/Tbl* once; "
            "Run* go in expected_runs as {role,text} with text in main_point "
            "(not as their own blocks); keep captions with each distinct Img*/Tbl*."
        )
        cover_tok = min(int(base_tok * 1.25), SPECIMEN_MAX_TOKENS_CAP)
        print(
            f"[retry] specimen {inst} 缺本节 must-style: {sorted(miss)}"
            f"（max_tokens={cover_tok}）"
        )
        raw = _call(fb, f"template_specimen_{inst}_cover", attempt_tokens=cover_tok)
        raw_path.write_text(raw, encoding="utf-8")
        try:
            blocks = parse_specimen_blocks(raw, inst)
        except Exception:  # noqa: BLE001
            blocks = _greedy()
        blocks = _finish(blocks)
        covered = styles_in_blocks(blocks, roles, by_id)
        miss = required - covered
        if miss:
            n = inject_missing(blocks, miss, group["blocks"], by_id, roles)
            blocks = fold_run_style_blocks(blocks, roles, by_id)
            blocks = dedupe_media_blocks(
                blocks, already=already_shown, roles=roles, stylesheet_by_id=by_id,
                section_role=group["sec_role"],
            )
            attach_donors(blocks, group["blocks"], roles)
            materialize_specimen_runs(blocks, roles, locale=locale, stylesheet_by_id=by_id)
            uniquify_block_ids(inst, blocks)
            print(f"[fix] specimen {inst} 程序补 {n} 个本节仍缺 style")
    # Final run pass (greedy path / inject may leave bare Run* ids)
    materialize_specimen_runs(blocks, roles, locale=locale, stylesheet_by_id=by_id)
    covered = styles_in_blocks(blocks, roles, by_id)
    still = required - covered
    if still:
        print(f"[warn] specimen {inst} 本节 must 仍缺: {sorted(still)}")
    else:
        print(f"[specimen {inst}] 本节 must {len(required)} 项已覆盖")
    (dump_dir / f"{inst}.json").write_text(
        json.dumps({"instance_id": inst, "sec_role": group["sec_role"], "blocks": blocks},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return blocks


def global_required_styles(plan: dict, roles: dict) -> set[str]:
    by_id = _stylesheet_by_id(plan.get("stylesheet") or [])
    required: set[str] = set()
    for sid, item in by_id.items():
        if is_section_style_block({"role_id": sid}, roles):
            continue
        if is_cell_style(sid, roles, by_id):
            continue
        required.add(sid)
    # Styles that never appear in any block still need a slot
    return required


def assemble_specimen_plan(
    plan: dict,
    group_outlines: list[dict],
    roles: dict,
) -> dict[str, Any]:
    sections = []
    seen_sec: set[str] = set()
    for item in group_outlines:
        sec = dict(item["section"])
        rid = str(sec.get("role_id") or "")
        if rid in seen_sec:
            continue
        seen_sec.add(rid)
        sections.append(sec)
    specimen = {
        "meta": dict(plan.get("meta") or {}),
        "stylesheet": plan.get("stylesheet") or [],
        "sections": sections,
        "outline": [{"instance_id": o["instance_id"], "blocks": o["blocks"]} for o in group_outlines],
        "specimen": True,
    }
    apply_section_page_continuity(specimen)
    by_id = _stylesheet_by_id(plan.get("stylesheet") or [])
    required = outline_used_styles(plan, roles)
    leftover = required - styles_in_blocks(
        [b for o in group_outlines for b in (o.get("blocks") or [])],
        roles,
        by_id,
    )
    if leftover and group_outlines:
        host = group_outlines[-1]
        n = inject_missing(
            host["blocks"], leftover, host.get("source_blocks") or [],
            by_id, roles,
        )
        host["blocks"] = fold_run_style_blocks(host["blocks"], roles, by_id)
        uniquify_block_ids(str(host["instance_id"]), host["blocks"])
        print(
            f"[fix] specimen 补 {n} 个成稿已用但标本未示范的 style: {sorted(leftover)}"
        )
        leftover = required - styles_in_blocks(
            [b for o in group_outlines for b in (o.get("blocks") or [])],
            roles,
            by_id,
        )
    # Align all specimen runs with step6 {role,text}∈main_point contract
    locale = str((plan.get("meta") or {}).get("locale") or "zh")
    for o in group_outlines:
        materialize_specimen_runs(
            o.get("blocks") or [], roles, locale=locale, stylesheet_by_id=by_id,
        )
    leftover = required - styles_in_blocks(
        [b for o in group_outlines for b in (o.get("blocks") or [])],
        roles,
        by_id,
    )
    stats = {
        "sec_roles": [g.get("sec_role") for g in group_outlines],
        "blocks": sum(len(o.get("blocks") or []) for o in group_outlines),
        "missing": sorted(leftover),
        "ok": not leftover,
    }
    specimen["coverage"] = stats
    return specimen


def _preassign_already_shown_media(
    groups: list[dict],
    roles: dict,
    by_id: dict,
) -> list[set[str]]:
    """Per-group media already 'owned' by earlier Sec* groups (from source blocks).

    Enables parallel specimen generation without a sequential shown_media accumulator.
    Leftover coverage is still fixed in ``assemble_specimen_plan``.
    """
    claimed: set[str] = set()
    already_per_group: list[set[str]] = []
    for g in groups or []:
        already_per_group.append(set(claimed))
        for sid in required_body_styles(g.get("blocks") or [], roles, by_id):
            if is_media_style(sid, roles, by_id):
                claimed.add(sid)
    return already_per_group


def ensure_specimen_plan(
    out_dir: Path,
    *,
    overwrite: bool = False,
    temperature: float = 0.3,
    max_tokens: int = DEFAULT_SPECIMEN_MAX_TOKENS,
    chapter_workers: int = 1,
) -> dict[str, Any] | None:
    """Build or reuse ``specimen_plan.json`` next to template.json."""
    out_dir = Path(out_dir)
    spec_path = out_dir / "specimen_plan.json"
    if spec_path.is_file() and not overwrite:
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("outline"):
            # Refresh run substrings to step6 contract without re-calling the LLM
            try:
                plan_path = resolve_work_file(out_dir, "plan.json")
                plan = (
                    json.loads(plan_path.read_text(encoding="utf-8"))
                    if plan_path.is_file()
                    else data
                )
                roles = _roles_from_template(out_dir, plan if isinstance(plan, dict) else data)
                by_id = _stylesheet_by_id((plan or data).get("stylesheet") or data.get("stylesheet") or [])
                locale = str(
                    ((plan or data).get("meta") or data.get("meta") or {}).get("locale") or "zh"
                )
                fixed = 0
                for o in data.get("outline") or []:
                    if isinstance(o, dict):
                        st = materialize_specimen_runs(
                            o.get("blocks") or [],
                            roles,
                            locale=locale,
                            stylesheet_by_id=by_id,
                        )
                        fixed += int(st.get("runs_fixed") or 0) + int(st.get("text_rewrites") or 0)
                if fixed:
                    spec_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    print(f"[cache] specimen_plan.json 已物化 runs（fix/rewrite={fixed}）")
                else:
                    miss = (data.get("coverage") or {}).get("missing") or []
                    print(f"[cache] specimen_plan.json（缺 style {len(miss)}）")
            except Exception as exc:  # noqa: BLE001
                miss = (data.get("coverage") or {}).get("missing") or []
                print(f"[cache] specimen_plan.json（缺 style {len(miss)}；runs refresh skip: {exc}）")
            return data

    plan_path = resolve_work_file(out_dir, "plan.json")
    if not plan_path.is_file():
        print(f"[skip] specimen 缺 plan.json: {plan_path}")
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    roles = _roles_from_template(out_dir, plan)
    locale = str((plan.get("meta") or {}).get("locale") or "zh")
    theme = str((plan.get("meta") or {}).get("theme") or "")
    groups = group_sections_by_role(plan)
    if not groups:
        print("[skip] specimen 无 sections")
        return None

    dump_dir = out_dir / "specimen"
    by_id = _stylesheet_by_id(plan.get("stylesheet") or [])
    used = outline_used_styles(plan, roles)
    workers = max(1, int(chapter_workers or 1))
    print(
        f"[specimen] {len(groups)} 个 Sec* 角色 → 短标本"
        f"（覆盖成稿已用 style {len(used)}；chapter_workers={workers}；"
        f"max_tokens base={int(max_tokens)} cap={SPECIMEN_MAX_TOKENS_CAP}）"
    )

    already_list = _preassign_already_shown_media(groups, roles, by_id)

    def _one(idx: int, g: dict) -> dict:
        blocks = _generate_group_blocks(
            group=g, plan=plan, roles=roles, locale=locale, theme=theme,
            temperature=temperature, max_tokens=max_tokens, dump_dir=dump_dir,
            extra_required=set(), already_shown_media=already_list[idx],
        )
        return {
            "instance_id": g["section"].get("instance_id"),
            "sec_role": g["sec_role"],
            "section": g["section"],
            "blocks": blocks,
            "source_blocks": g["blocks"],
        }

    outlines: list[dict] = [{}] * len(groups)
    if workers <= 1 or len(groups) <= 1:
        for i, g in enumerate(groups):
            outlines[i] = _one(i, g)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as pool:
            futs = {pool.submit(_one, i, g): i for i, g in enumerate(groups)}
            for fut in as_completed(futs):
                i = futs[fut]
                outlines[i] = fut.result()

    specimen = assemble_specimen_plan(plan, outlines, roles)
    spec_path.write_text(json.dumps(specimen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cov = specimen.get("coverage") or {}
    print(
        f"[specimen] 写出 {spec_path.name}  blocks={cov.get('blocks')} "
        f"sec_roles={len(cov.get('sec_roles') or [])} "
        f"{'OK' if cov.get('ok') else '缺 ' + str(cov.get('missing'))}"
    )
    return specimen
