"""Declared heading hierarchy on stylesheet (source of truth for step3/4/6).

Stylesheet paragraph styles that participate in Word's outline (章/节/小节) MUST
carry ``heading_level`` 1..6. Cover titles use ``title_kind`` instead — they are
not Heading1.

Step6 maps heading_level → built-in Heading1..6. Do not infer from role_id names.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Flip to True after the two-topic instruction-following trial if the model
# reliably emits heading_level and used depth matches step2 recommend.
HEADING_DEPTH_CHECK_FATAL = False

WORD_HEADING_STYLE = {
    1: "Heading1",
    2: "Heading2",
    3: "Heading3",
    4: "Heading4",
    5: "Heading5",
    6: "Heading6",
}
TITLE_KIND_TO_STYLE = {"cover": "Title", "subtitle": "Subtitle"}


def parse_heading_level(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 6 else None


def parse_title_kind(raw: Any) -> str | None:
    s = str(raw or "").strip().lower()
    if s in ("cover", "cover_title", "title"):
        return "cover"
    if s in ("subtitle", "cover_subtitle"):
        return "subtitle"
    return None


def stylesheet_items(plan_or_ss: Any) -> list[dict[str, Any]]:
    if isinstance(plan_or_ss, dict) and "stylesheet" in plan_or_ss:
        ss = plan_or_ss.get("stylesheet")
    else:
        ss = plan_or_ss
    if isinstance(ss, list):
        return [x for x in ss if isinstance(x, dict)]
    return []


def heading_index(plan_or_ss: Any) -> dict[str, dict[str, Any]]:
    """style_id → {heading_level?, title_kind?, word_style?}."""
    out: dict[str, dict[str, Any]] = {}
    for item in stylesheet_items(plan_or_ss):
        sid = str(item.get("style_id") or item.get("role_id") or item.get("id") or "")
        if not sid:
            continue
        hl = parse_heading_level(item.get("heading_level"))
        tk = parse_title_kind(item.get("title_kind"))
        if hl and tk:
            tk = None  # heading_level wins; cover must not also be H1
        rec: dict[str, Any] = {}
        if hl:
            rec["heading_level"] = hl
            rec["word_style"] = WORD_HEADING_STYLE[hl]
        if tk:
            rec["title_kind"] = tk
            rec["word_style"] = TITLE_KIND_TO_STYLE.get(tk)
        if rec:
            rec["display_name"] = str(item.get("display_name") or sid)
            rec["object"] = str(item.get("object") or "")
            out[sid] = rec
    return out


def is_paragraph_heading_id(style_id: str, rec: dict[str, Any] | None = None) -> bool:
    """True when heading_level sits on a paragraph style (the Word-outline source of truth).

    Names are free (ParaHeading1 / ParaSectionHeading2 / 章标题 …). Section styles
    may still *declare* heading_level in a mistaken library, but they do not drive
    paragraph outlineLvl.
    """
    sid = str(style_id or "")
    obj = str((rec or {}).get("object") or "")
    if obj == "section" or sid.startswith("Sec"):
        return False
    if obj in {
        "paragraph", "paragraph.body", "paragraph.table_cell", "paragraph.cell_text",
    } or sid.startswith("Para"):
        if "Cell" in sid or obj in {"paragraph.table_cell", "paragraph.cell_text"}:
            return False  # table-paragraph: not outline heading
        return True
    return bool((rec or {}).get("heading_level")) and not sid.startswith(("Tbl", "Img", "Run"))


def load_heading_index(*candidates: Path | None) -> dict[str, dict[str, Any]]:
    """Load stylesheet/plan from the first existing candidate."""
    for path in candidates:
        if path is None:
            continue
        p = Path(path)
        if p.is_dir():
            for name in ("stylesheet.json", "plan.json"):
                hit = load_heading_index(p / name)
                if hit:
                    return hit
            continue
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, list):
            idx = heading_index(data)
        elif isinstance(data, dict):
            idx = heading_index(data)
        else:
            continue
        if idx:
            return idx
    return {}


def used_heading_levels(plan: dict[str, Any]) -> dict[str, Any]:
    """Count outline blocks that use **paragraph** styles with heading_level.

    ``heading_level`` on section styles is recorded as ``misplaced`` and does
    not count toward used depth (step4 only copies outlineLvl onto paragraphs).
    """
    index = heading_index(plan)
    para_index = {
        sid: rec for sid, rec in index.items()
        if rec.get("heading_level") and is_paragraph_heading_id(sid, rec)
    }
    misplaced = {
        sid: rec for sid, rec in index.items()
        if rec.get("heading_level") and not is_paragraph_heading_id(sid, rec)
    }
    used: dict[str, int] = {}
    n_heading_blocks = 0
    for o in plan.get("outline") or []:
        if not isinstance(o, dict):
            continue
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            sid = str(b.get("role_id") or "")
            rec = para_index.get(sid)
            if not rec or not rec.get("heading_level"):
                continue
            n_heading_blocks += 1
            used[sid] = int(rec["heading_level"])
    levels = sorted(set(used.values()))
    depth = max(levels) if levels else 0
    return {
        "index": index,
        "paragraph_index": para_index,
        "misplaced": misplaced,
        "used_by_style": used,
        "levels_present": levels,
        "depth": depth,
        "n_heading_blocks": n_heading_blocks,
    }


def check_heading_depth(plan: dict[str, Any], brief: dict[str, Any] | None) -> dict[str, Any]:
    """Compare outline-used heading_level max to step2 recommend.heading_depth.

    Does **not** look at outline block counts.
    """
    rec = {}
    if isinstance(brief, dict):
        rec = brief.get("recommend") if isinstance(brief.get("recommend"), dict) else {}
        rec = dict(rec or {})
    want = int(rec.get("heading_depth") or 0)
    stats = used_heading_levels(plan)
    have = int(stats["depth"] or 0)
    present = list(stats["levels_present"] or [])
    missing_levels = [n for n in range(1, max(have, want) + 1) if n <= want and n not in present]
    problems: list[str] = []
    para_index = stats.get("paragraph_index") or {}
    misplaced = stats.get("misplaced") or {}
    if not para_index:
        if misplaced:
            problems.append(
                "标题层级只认 stylesheet 里「段落 style + heading_level」"
                "（step4 据此写 outlineLvl / Word Heading）。"
                "下列项把 heading_level 写在了 section 上，不会当作段落标题："
                + ", ".join(sorted(misplaced)[:8])
                + ("…" if len(misplaced) > 8 else "")
            )
        else:
            problems.append("stylesheet 未声明任何段落标题的 heading_level（正文标题 style 必须标 1–6）")
    elif misplaced:
        problems.append(
            "有 heading_level 写在 section 上（不参与段落大纲）："
            + ", ".join(sorted(misplaced)[:8])
            + ("…" if len(misplaced) > 8 else "")
        )
    if want and have < want:
        problems.append(f"大纲用到的最大 heading_level={have}，低于 step2 建议 {want}")
    if missing_levels and have:
        problems.append(f"标题层级有缺口：缺 H{missing_levels}（已有 {present}）")
    ok = not problems
    declared = {}
    for sid, rec in (para_index or {}).items():
        declared[sid] = {k: v for k, v in rec.items() if k in ("heading_level", "title_kind", "word_style", "display_name", "object")}
    for sid, rec in (stats.get("index") or {}).items():
        if rec.get("title_kind") and sid not in declared:
            declared[sid] = {k: v for k, v in rec.items() if k in ("heading_level", "title_kind", "word_style", "display_name", "object")}
    return {
        "ok": ok,
        "fatal": HEADING_DEPTH_CHECK_FATAL,
        "recommend_heading_depth": want or None,
        "used_depth": have,
        "levels_present": present,
        "used_by_style": stats["used_by_style"],
        "declared_styles": declared,
        "misplaced_heading_styles": {
            sid: {k: v for k, v in rec.items() if k in ("heading_level", "display_name", "object")}
            for sid, rec in misplaced.items()
        },
        "n_heading_blocks": stats["n_heading_blocks"],
        "problems": problems,
        "note": (
            "标题层级只认 stylesheet 里「段落 style + heading_level」（名字不限）；"
            "step4 据此写 outlineLvl，step6 映射到 Word Heading1–4。"
            "写在 section 上的 heading_level 不计入。"
            "不核验标题树分叉数量。"
        ),
    }


def format_heading_check_md(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("ok") else "FAIL"
    lines = [
        f"# Heading-level check · {status}",
        "",
        str(report.get("note") or ""),
        "",
        f"- step2 建议 heading_depth = **{report.get('recommend_heading_depth')}**",
        f"- 大纲用到的最大 heading_level = **{report.get('used_depth')}**",
        f"- 出现的层级：{report.get('levels_present') or '（无）'}",
        f"- 标题块数（挂了段落 heading_level 的 outline 块）：{report.get('n_heading_blocks')}",
        "",
        "## stylesheet 声明的段落标题（层级依据）",
        "",
    ]
    declared = report.get("declared_styles") or {}
    if declared:
        for sid, rec in sorted(
            declared.items(),
            key=lambda kv: (kv[1].get("heading_level") or 9, kv[0]),
        ):
            if rec.get("heading_level"):
                lines.append(
                    f"- H{rec['heading_level']} `{sid}`（{rec.get('display_name') or sid}）"
                    f" → {rec.get('word_style')}"
                )
            elif rec.get("title_kind"):
                lines.append(
                    f"- title_kind={rec['title_kind']} `{sid}` → {rec.get('word_style')}"
                )
    else:
        lines.append("（无）")
    misplaced = report.get("misplaced_heading_styles") or {}
    if misplaced:
        lines += ["", "## 写在 section 上的 heading_level（不计入段落大纲）", ""]
        for sid, rec in sorted(misplaced.items()):
            lines.append(
                f"- H{rec.get('heading_level')} `{sid}`（{rec.get('display_name') or sid}）"
            )
    used = report.get("used_by_style") or {}
    lines += ["", "## 大纲中实际用到", ""]
    if used:
        for sid, lvl in sorted(used.items(), key=lambda kv: (kv[1], kv[0])):
            lines.append(f"- H{lvl} `{sid}`")
    else:
        lines.append("（无）")
    probs = report.get("problems") or []
    lines += ["", "## 问题", ""]
    if probs:
        lines.extend(f"- {p}" for p in probs)
    else:
        lines.append("（无）")
    lines.append("")
    return "\n".join(lines)


def write_heading_check(plan: dict[str, Any], brief: dict[str, Any] | None, out_dir: Path) -> dict[str, Any]:
    report = check_heading_depth(plan, brief)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "heading_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "heading_check.md").write_text(format_heading_check_md(report), encoding="utf-8")
    return report


def word_style_for_role(style_id: str, index: dict[str, dict[str, Any]]) -> str | None:
    rec = index.get(style_id) or {}
    return rec.get("word_style")


def apply_heading_level_to_role_params(role_params: dict[str, Any], plan: dict[str, Any]) -> int:
    """Set outlineLvl from stylesheet heading_level; strip it from cover/TOC-like styles."""
    n = 0
    index = heading_index(plan)
    for role, spec in role_params.items():
        if not isinstance(spec, dict):
            continue
        obj = str(spec.get("object") or "")
        if obj not in (
            "paragraph", "paragraph.body", "paragraph.table_cell", "paragraph.cell_text", "",
        ):
            continue
        if "Cell" in role or obj in ("paragraph.table_cell", "paragraph.cell_text"):
            continue
        props = spec.setdefault("props", {})
        rec = index.get(role)
        if rec and rec.get("heading_level"):
            want = int(rec["heading_level"]) - 1
            spec["heading_level"] = rec["heading_level"]
            if props.get("outlineLvl") != want:
                props["outlineLvl"] = want
                n += 1
            continue
        if rec and rec.get("title_kind"):
            spec["title_kind"] = rec["title_kind"]
            if "outlineLvl" in props:
                props.pop("outlineLvl", None)
                n += 1
    return n
