"""Step 4 runner: parameterize a step3 selection into T* (executable format params).

Input  = step3 outline（``datasets/{id}_*/C*/step3_outlines/``；
         兼容旧 ``work/topics/...`` 与扁平 ``work/step3_outlines/<slug>/``）。
Output = ``C*/step4_styles/``：role_params.json（格式真源）+ template.json（名称目录，不含 props）
（specimen + template.docx 在 step5 ``C*/step5_template/``。）

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/run_step4.py --id 1 --theme 1 --overwrite
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.draft_resolve import parse_ids_arg, resolve_draft_dir, resolve_outline_dir
from LongDocFmtEval.common.llm import dump_json, load_prompt, load_yaml, render_template
from LongDocFmtEval.common.prompt_locale import format_theme_context
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import (
    CONFIG_DIR,
    OUTLINES_DIR,
    ROLE_LIBRARY_DRAFTS_DIR,
    TEMPLATES_DIR,
    TOPICS_DIR,
    officecli_locale_yaml,
)
from LongDocFmtEval.pipeline_auto.style_taxonomy import (
    count_from_selection,
    format_ref_roles,
    layers_payload,
    taxonomy_brief,
)
from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: E402
    outline_dir as topic_outline_dir,
    resolve_topic_dir,
    styles_dir as topic_styles_dir,
)
from LongDocFmtEval.pipeline_auto.seed.heading_contract import apply_heading_level_to_role_params
from LongDocFmtEval.pipeline_auto.seed.step6_t_text import (
    public_display_name,
    public_purpose,
    stylesheet_name_desc,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (
    apply_officecli_keys_to_selected_roles,
    coerce_officecli_prop_keys,
    drop_unlisted_officecli_props,
    expand_font_props,
    strip_wrong_layer_keys,
)
from LongDocFmtEval.common.role_library import effective_focus, is_abstract_role_id, lenient_json_load, rid


def _prompt_path(locale: str, name: str, *fallbacks: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name, fallbacks=fallbacks)


def _is_en(locale: str) -> bool:
    return str(locale).lower().startswith("en")


# ─────────────────────────── input assembly ───────────────────────────

def _index(nodes: list) -> dict[str, dict]:
    return {rid(n): n for n in (nodes or [])}


def assemble_selected_roles(plan: dict, lib: dict) -> tuple[dict, list[str]]:
    """Return (selected_roles payload for the prompt, ordered list of expected role ids).

    Selection buckets map to style layers:
      section_roles → section
      paragraph_body_roles → paragraph.body
      paragraph_cell_roles → paragraph.cell_text (reusable across Tbl*)
      table_roles → table (Tbl* structure)
      image_roles → image
      run_roles → run
    """
    roles = lib.get("roles", {})
    sec_nodes = roles.get("section") or []
    body_nodes = (roles.get("paragraph") or {}).get("body") or []
    cell_nodes = (roles.get("paragraph") or {}).get("table_cell") or []
    run_nodes = roles.get("paragraph_runs") or []
    tbl_nodes = roles.get("table") or []
    img_nodes = roles.get("image") or []

    eff_sec = effective_focus(sec_nodes)
    eff_body = effective_focus(body_nodes)
    eff_cell = effective_focus(cell_nodes)
    eff_img = effective_focus(img_nodes)
    idx_sec, idx_body, idx_cell = _index(sec_nodes), _index(body_nodes), _index(cell_nodes)
    idx_run, idx_tbl, idx_img = _index(run_nodes), _index(tbl_nodes), _index(img_nodes)

    exp = {r["role_id"]: r.get("expectation", "") for r in (plan.get("format_expectations") or [])}
    sel = plan.get("selection") or {}
    cell_keep = [x for x in (sel.get("paragraph_cell_roles") or []) if x]
    cell_keep_set = set(cell_keep)
    default_cell = next(
        (x for x in ("ParaTblDataCell", "ParaTblBodyCell") if x in cell_keep_set),
        cell_keep[0] if cell_keep else "",
    )
    expected: list[str] = []
    out: dict = {"section": [], "paragraph": [], "run": [], "table": [], "image": []}

    for r in sel.get("section_roles") or []:
        n = idx_sec.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        disc = n.get("discriminators", "")
        fam = n.get("section_family") or _section_family_of(r, n, idx_sec)
        out["section"].append({"role_id": r, "effective_focus": eff_sec.get(r, []),
                               "display_name": n.get("display_name", ""),
                               "section_family": fam,
                               "discriminators": disc, "expectation": exp.get(r, "")})
        expected.append(r)

    for r in sel.get("paragraph_body_roles") or []:
        n = idx_body.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        disc = n.get("discriminators", "")
        fam = n.get("section_family") or _section_family_of(r, n, idx_body)
        out["paragraph"].append({"role_id": r, "cell": False, "effective_focus": eff_body.get(r, []),
                                 "display_name": n.get("display_name", ""),
                                 "section_family": fam,
                                 "discriminators": disc, "expectation": exp.get(r, "")})
        expected.append(r)
    for r in sel.get("paragraph_cell_roles") or []:
        n = idx_cell.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        disc = n.get("discriminators", "")
        fam = n.get("section_family") or _section_family_of(r, n, idx_cell)
        out["paragraph"].append({"role_id": r, "cell": True, "effective_focus": eff_cell.get(r, []),
                                 "display_name": n.get("display_name", ""),
                                 "section_family": fam,
                                 "discriminators": disc, "expectation": exp.get(r, "")})
        expected.append(r)

    for r in sel.get("run_roles") or []:
        n = idx_run.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        disc = n.get("discriminators", "")
        if isinstance(disc, list):
            disc = "；".join(str(x) for x in disc if x)
        out["run"].append({"span_role": r, "format_focus": n.get("format_focus", []),
                           "display_name": n.get("display_name", ""),
                           "attaches_to": n.get("attaches_to", []),
                           "discriminators": disc,
                           "expectation": exp.get(r, "")})
        expected.append(r)

    for t in sel.get("table_roles") or []:
        r = t.get("role_id") if isinstance(t, dict) else t
        n = idx_tbl.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        kept = t.get("position_map_kept") if isinstance(t, dict) else None
        tl = n.get("table_layout") or {}
        pm = tl.get("position_map") or {}
        positions = {}
        for pos, m in pm.items():
            if kept is not None and pos not in kept:
                continue
            para = (m or {}).get("paragraph")
            if para and cell_keep_set and para not in cell_keep_set and default_cell:
                para = default_cell
            positions[pos] = {"cell_focus": (m or {}).get("cell_focus", []), "paragraph": para}
        disc = n.get("discriminators", "")
        fam = n.get("section_family") or _section_family_of(r, n, idx_tbl)
        out["table"].append({"role_id": r, "table_focus": tl.get("table_focus", []),
                             "positions": positions, "display_name": n.get("display_name", ""),
                             "section_family": fam,
                             "discriminators": disc,
                             "expectation": exp.get(r, "")})
        expected.append(r)

    for r in sel.get("image_roles") or []:
        n = idx_img.get(r) or {}
        if n.get("abstract") or (not n and is_abstract_role_id(r, lib)):
            continue
        disc = n.get("discriminators", "")
        fam = n.get("section_family") or _section_family_of(r, n, idx_img)
        out["image"].append({"role_id": r, "effective_focus": eff_img.get(r, []),
                             "display_name": n.get("display_name", ""),
                             "section_family": fam,
                             "discriminators": disc, "expectation": exp.get(r, "")})
        expected.append(r)

    return out, expected


def whitelist_text(wl: dict, locale: str = "zh") -> str:
    """Readable, compact rendering of the officecli whitelist for the prompt."""
    if _is_en(locale):
        header = (
            "Keys below are officecli JSON prop names (write these into props). "
            "Do not invent snake_case aliases.\n"
        )
        order = [
            ("section", "section (page setup)"),
            ("paragraph", "paragraph text (incl. table-cell text)"),
            ("run", "run (inline character span)"),
            ("table_format", "table-structure (whole table)"),
            ("cell_format", "table-cell (cell chrome)"),
            ("image", "image (inline)"),
        ]
    else:
        header = (
            "下列键名即 officecli JSON props 键（驼峰）。请原样写入；不要自造下划线别名。\n"
        )
        order = [
            ("section", "section 节级"),
            ("paragraph", "paragraph 段落文本(含表格单元格文本)"),
            ("run", "run 段内字符跨度"),
            ("table_format", "table-structure 表级"),
            ("cell_format", "table-cell 单元格级"),
            ("image", "image 内联图片"),
        ]
    lines: list[str] = [header]
    for key, label in order:
        block = wl.get(key) or {}
        lines.append(f"### {label}")
        for k, meta in block.items():
            lines.append(f"- {k} = {meta.get('form', '')}")
        lines.append("")
    return "\n".join(lines).strip()


# ─────────────────────────── validation ───────────────────────────

# 同格式共用阈值：默认不允许不同语义角色碰撞（签名相同即超阈）。
# 任意一组 > max_group_size，或碰撞组数 > max_groups → 带反馈补丁。
# group_size≤1 且 max_groups=0 → 任意 ≥2 角色同签名即失败。
DEFAULT_SHARE_MAX_GROUP_SIZE = 1  # 1=不允许同组多人（即零碰撞）
DEFAULT_SHARE_MAX_GROUPS = 0      # 0=不允许任何同格式组
DEFAULT_SHARE_RETRIES = 2         # 超阈后最多再补丁几轮
DEFAULT_MISSING_RETRIES = 2       # 缺少角色参数时最多补丁几轮


def _sig(spec: dict) -> str:
    """格式签名。section 把页眉/页脚文案与字符格式一并计入（不只边距/页码制式）。"""
    props = spec.get("props", {}) or {}
    if (spec.get("object") or "") == "section":
        return json.dumps({
            "props": props,
            "header": spec.get("header") or {},
            "footer": spec.get("footer") or {},
            "header_first": spec.get("header_first") or {},
        }, ensure_ascii=False, sort_keys=True)
    return json.dumps(props, ensure_ascii=False, sort_keys=True)


def find_collisions(role_params: dict) -> list[list[str]]:
    """Groups (>=2) of same-object roles whose format signature is identical.

    Sharing one Para*Cell across many Tbl* is **not** a collision (one role_id).
    Collision = two different role_ids (e.g. two Para*Cell) with the same signature.
    """
    by_key: dict[tuple[str, str], list[str]] = {}
    for role, spec in role_params.items():
        by_key.setdefault((spec.get("object", "?"), _sig(spec)), []).append(role)
    return [roles for roles in by_key.values() if len(roles) > 1]


def share_exceeds_threshold(
    groups: list[list[str]],
    *,
    max_group_size: int = DEFAULT_SHARE_MAX_GROUP_SIZE,
    max_groups: int = DEFAULT_SHARE_MAX_GROUPS,
) -> bool:
    if not groups:
        return False
    if len(groups) > max_groups:
        return True
    return any(len(g) > max_group_size for g in groups)


def roles_to_differentiate(
    groups: list[list[str]],
    *,
    max_group_size: int,
    max_groups: int,
) -> list[str]:
    """Pick roles that must change so sharing falls under threshold (keep one anchor per group)."""
    fix: set[str] = set()
    # Oversized groups: keep first id as anchor, revise the rest
    for g in groups:
        if len(g) > max_group_size:
            for r in sorted(g)[1:]:
                fix.add(r)
    # Too many pair-groups: keep first max_groups (by size then id), break the rest
    if len(groups) > max_groups:
        ranked = sorted(groups, key=lambda g: (-len(g), sorted(g)[0]))
        for g in ranked[max_groups:]:
            for r in sorted(g)[1:]:
                fix.add(r)
    return sorted(fix)


def collision_patch_user(
    groups: list[list[str]],
    role_params: dict,
    name_by_role: dict,
    *,
    max_group_size: int,
    max_groups: int,
    locale: str = "zh",
) -> str:
    """Compact retry prompt: only colliding roles; LLM returns a patch, not the full stylesheet."""
    fix_roles = roles_to_differentiate(
        groups, max_group_size=max_group_size, max_groups=max_groups,
    )
    if not fix_roles:
        # threshold exceeded only by group count with size-2 pairs — still need anchors' siblings
        fix_roles = sorted({r for g in groups for r in sorted(g)[1:]})
    subset = {r: role_params[r] for r in fix_roles if r in role_params}
    # Include one unchanged sibling per group as contrast (read-only context)
    anchors: dict[str, dict] = {}
    for g in groups:
        anchor = sorted(g)[0]
        if anchor in role_params and anchor not in subset:
            anchors[anchor] = role_params[anchor]

    en = _is_en(locale)
    if en:
        lines = [
            "## Format-collision PATCH (do NOT regenerate the full stylesheet)",
            f"Share groups now={len(groups)} (max {max_groups}); "
            f"largest group={max((len(g) for g in groups), default=0)} (max {max_group_size}).",
            "Rule: **no format collisions** — every colliding peer must get a distinct signature.",
            "Sections must differ via **header/footer text or character format** "
            "(header counts as format). Do not rename roles / display_name.",
            "",
            "### Output contract (hard)",
            "1. One JSON object only: `{\"role_params\": { ... }}`.",
            "2. Include **ONLY** the roles listed under **Revise these** "
            "(complete per-role objects: object/props/example/header/… as before).",
            "3. Do **not** re-output other roles. The program merges your patch into the previous result.",
            "4. After your edits, each revised role's format signature must differ from its former group mates.",
            "",
            "### Collision groups",
        ]
    else:
        lines = [
            "## 格式碰撞补丁（不要整份重生成 stylesheet）",
            f"当前同格式组数={len(groups)}（上限 {max_groups}）；"
            f"单组最大人数={max((len(g) for g in groups), default=0)}（上限 {max_group_size}）。",
            "规则：**不允许同格式碰撞**——组内每个待改角色必须拉开签名。",
            "section 须靠 **header/footer 文案或字符格式** 区分（页眉算格式）；不要改 display_name。",
            "",
            "### 输出约定（硬）",
            "1. 只输出一个 JSON：`{\"role_params\": { ... }}`。",
            "2. **只包含「需要修订」下列出的角色**（每个角色给完整对象：object/props/example/header/…）。",
            "3. **不要**重输其它未列角色；程序会把补丁合并进上一版。",
            "4. 修订后，这些角色的格式签名必须与原同组成员区分开。",
            "",
            "### 碰撞组",
        ]
    for i, g in enumerate(groups, 1):
        labels = [f"{r}（{name_by_role.get(r) or r}）" for r in sorted(g)]
        obj = (role_params.get(g[0]) or {}).get("object", "?")
        lines.append(f"{i}. [{obj}] {', '.join(labels)}")
        sample = role_params.get(sorted(g)[0]) or {}
        if obj == "section":
            lines.append(
                f"   header={json.dumps(sample.get('header') or {}, ensure_ascii=False)} "
                f"footer={json.dumps(sample.get('footer') or {}, ensure_ascii=False)}"
            )
    if en:
        lines.append("")
        lines.append("### Revise these (patch keys)")
        lines.append(", ".join(fix_roles))
        if anchors:
            lines.append("")
            lines.append("### Keep as contrast (do not output; unchanged anchors)")
            lines.append(json.dumps(anchors, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("### Current specs to revise")
        lines.append(json.dumps(subset, ensure_ascii=False, indent=2))
    else:
        lines.append("")
        lines.append("### 需要修订（补丁键）")
        lines.append("、".join(fix_roles))
        if anchors:
            lines.append("")
            lines.append("### 对照锚点（勿输出；保持不变）")
            lines.append(json.dumps(anchors, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("### 待修订角色的当前规格")
        lines.append(json.dumps(subset, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def merge_role_params_patch(
    base: dict,
    patch: dict,
    *,
    expected_fix: list[str] | None = None,
    allow_new: bool = False,
    allowed_new: list[str] | None = None,
) -> tuple[dict, list[str]]:
    """Merge LLM patch into previous role_params. Returns (merged, warnings).

    ``allow_new``: accept roles absent from base (missing-role fill). When
    ``allowed_new`` is set, only those ids may be added.
    """
    warnings: list[str] = []
    if not patch:
        warnings.append("补丁为空")
        return dict(base), warnings
    merged = dict(base)
    allow_set = set(allowed_new) if allowed_new is not None else None
    applied = 0
    for role, spec in patch.items():
        if role not in base:
            if not allow_new:
                warnings.append(f"补丁含未知角色已忽略: {role}")
                continue
            if allow_set is not None and role not in allow_set:
                warnings.append(f"补丁含非缺失角色已忽略: {role}")
                continue
        if not isinstance(spec, dict):
            warnings.append(f"补丁角色非对象已忽略: {role}")
            continue
        merged[role] = spec
        applied += 1
    if expected_fix:
        missing = [r for r in expected_fix if r not in patch]
        if missing:
            warnings.append(f"补丁未覆盖建议修订角色: {missing}")
    if applied == 0:
        warnings.append("补丁未成功合并任何角色")
    return merged, warnings


def _selected_role_briefs(selected_roles: dict, role_ids: list[str]) -> dict[str, dict]:
    """Subset of assemble payload keyed by role_id (runs use span_role field as id)."""
    want = set(role_ids)
    briefs: dict[str, dict] = {}
    for bucket, key in (
        ("section", "role_id"),
        ("paragraph", "role_id"),
        ("table", "role_id"),
        ("image", "role_id"),
        ("run", "span_role"),
    ):
        for item in selected_roles.get(bucket) or []:
            rid = item.get(key)
            if rid in want:
                briefs[rid] = dict(item)
                briefs[rid]["_bucket"] = bucket
    return briefs


def missing_roles_patch_user(
    missing: list[str],
    selected_roles: dict,
    *,
    locale: str = "zh",
) -> str:
    """Ask LLM to fill only the omitted role_params entries."""
    briefs = _selected_role_briefs(selected_roles, missing)
    en = _is_en(locale)
    if en:
        lines = [
            "## Missing-role PATCH (do NOT regenerate the full stylesheet)",
            f"These stylesheet roles were omitted from `role_params`: {', '.join(missing)}.",
            "Output **only** those keys. Host merges into the previous full table.",
            "",
            "### Output contract (hard)",
            "1. One JSON object only: `{\"role_params\": { ... }}`.",
            "2. Include **every** missing role below as a complete object "
            "(object/props/example; runs: object=`run`; sections: header/footer as usual).",
            "3. Do **not** re-output roles that already exist.",
            "",
            "### Role briefs to fill",
            json.dumps(briefs, ensure_ascii=False, indent=2),
        ]
    else:
        lines = [
            "## 缺失角色补丁（不要整份重生成 stylesheet）",
            f"以下 stylesheet 角色未出现在 `role_params`：{'、'.join(missing)}。",
            "只输出这些键；宿主会合并进上一版完整表。",
            "",
            "### 输出约定（硬）",
            "1. 只输出一个 JSON：`{\"role_params\": { ... }}`。",
            "2. **每个**缺失角色给完整对象（object/props/example；"
            "run 的 object=`run`；section 含 header/footer）。",
            "3. **不要**重输已有角色。",
            "",
            "### 待补角色说明",
            json.dumps(briefs, ensure_ascii=False, indent=2),
        ]
    return "\n".join(lines)


# backward-compat alias used by older call sites / docs
def collision_feedback(
    groups: list[list[str]],
    role_params: dict,
    name_by_role: dict,
    *,
    max_group_size: int,
    max_groups: int,
    locale: str = "zh",
) -> str:
    return collision_patch_user(
        groups, role_params, name_by_role,
        max_group_size=max_group_size, max_groups=max_groups, locale=locale,
    )


def _infer_section_family(role_id: str, display_name: str = "", disc="") -> str:
    """软先验：把内容角色归到前置/正文/后置节族，便于同族共享、跨族拉开。"""
    if isinstance(disc, list):
        disc = "；".join(str(x) for x in disc if x)
    blob = f"{role_id} {display_name} {disc}".lower()
    front = ("cover", "toc", "abstract", "preface", "front", "封面", "目录", "摘要", "关键词", "前言", "说明")
    back = ("appendix", "reference", "annex", "附录", "参考", "后记", "致谢")
    if any(k in blob for k in front):
        return "front_matter"
    if any(k in blob for k in back):
        return "back_matter"
    rid = (role_id or "").lower()
    if rid.startswith("sec"):
        if any(k in rid for k in ("cover", "toc", "abstract", "preface", "front")):
            return "front_matter"
        if any(k in rid for k in ("appendix", "reference", "annex")):
            return "back_matter"
        return "body"
    return "body"


def _section_family_of(role_id: str, node: dict, idx_by_id: dict) -> str:
    """显式 section_family → 沿 parent 链继承 → 名称启发式。"""
    seen: set[str] = set()
    cur_id, cur = role_id, node or {}
    while cur_id and cur_id not in seen:
        seen.add(cur_id)
        fam = cur.get("section_family")
        if fam:
            return str(fam)
        parent = cur.get("parent")
        if not parent:
            break
        cur_id = parent
        cur = idx_by_id.get(parent) or {}
    return _infer_section_family(role_id, (node or {}).get("display_name", ""),
                                 (node or {}).get("discriminators", ""))


def _obj_keys(wl: dict, obj: str) -> set[str]:
    if obj == "table":
        return set()  # handled specially
    key = {"section": "section", "paragraph": "paragraph", "run": "run", "image": "image"}.get(obj)
    return set((wl.get(key) or {}).keys())


# Allowed Chinese type sizes: 二号→小五 (8 discrete steps)
_ALLOWED_SIZE_PT = {22.0, 18.0, 16.0, 15.0, 14.0, 12.0, 10.5, 9.0}
_SIZE_NAME = {
    22.0: "二号", 18.0: "小二", 16.0: "三号", 15.0: "小三",
    14.0: "四号", 12.0: "小四", 10.5: "五号", 9.0: "小五",
}


def _parse_size_pt(raw) -> float | None:
    """Parse officecli size like '12pt' / '10.5pt' → float pt."""
    if raw is None:
        return None
    m = re.search(r"([\d.]+)\s*pt", str(raw), re.I)
    return float(m.group(1)) if m else None


def _heading_rank(role_id: str, props: dict, spec: dict | None = None) -> int | None:
    """Map a paragraph style to hierarchy rank (lower = larger expected size).

    Prefer stylesheet ``heading_level`` / ``title_kind`` copied onto role_params.
    0=document title, 1=H1章, 2=H2节, 3=H3小节, 4=H4. Returns None if not heading-like.
    """
    spec = spec or {}
    tk = str(spec.get("title_kind") or "").strip().lower()
    if tk in ("cover", "subtitle", "title"):
        return 0
    hl = spec.get("heading_level")
    if hl is not None:
        try:
            n = int(hl)
            if 1 <= n <= 4:
                return n
        except (TypeError, ValueError):
            pass
    rid = role_id or ""
    rid_l = rid.lower()
    # 目录角色不是正文标题
    if "toc" in rid_l or "目录" in rid:
        return None
    if any(k in rid_l for k in ("reporttitle", "题名")) and "heading" not in rid_l:
        return 0
    if "cover" in rid_l and "title" in rid_l and "heading" not in rid_l:
        return 0
    if "heading1" in rid_l or rid_l.endswith("h1") or "章标题" in rid or "headingchapter" in rid_l:
        return 1
    if "heading2" in rid_l or rid_l.endswith("h2") or "节标题" in rid:
        return 2
    if "heading3" in rid_l or rid_l.endswith("h3") or "小节" in rid:
        return 3
    if "heading4" in rid_l or rid_l.endswith("h4") or "小小节" in rid:
        return 4
    m = re.search(r"(?:para)?heading\s*([1234])", rid_l)
    if m:
        return int(m.group(1))
    m = re.search(r"heading([1234])", rid_l)
    if m:
        return int(m.group(1))
    ol = props.get("outlineLvl")
    if ol is not None and str(ol).isdigit():
        lvl = int(ol)
        if 0 <= lvl <= 3:
            return lvl + 1  # outlineLvl 0 → H1 rank 1
    return None


def _check_heading_size_ladder(role_params: dict) -> list[str]:
    """Warn when heading sizes are not strictly decreasing by level, or below 五号."""
    warns: list[str] = []
    by_rank: dict[int, list[tuple[str, float]]] = {}
    for role, spec in role_params.items():
        obj = str(spec.get("object") or "")
        if obj not in ("paragraph", "paragraph.body", "paragraph.table_cell", "paragraph.cell_text", ""):
            continue
        if "Cell" in role or obj in ("paragraph.table_cell", "paragraph.cell_text"):
            continue  # table-paragraph: not heading ladder
        props = spec.get("props") or {}
        pt = _parse_size_pt(props.get("size"))
        if pt is None:
            continue
        if pt not in _ALLOWED_SIZE_PT:
            warns.append(
                f"{role}: 字号 {pt}pt 不在八档内（二号/小二/三号/小三/四号/小四/五号/小五 = "
                f"22/18/16/15/14/12/10.5/9pt）")
        elif pt < 9.0:
            warns.append(f"{role}: 字号 {pt}pt 小于小五(9pt)")
        elif pt > 22:
            warns.append(f"{role}: 字号 {pt}pt 大于二号(22pt)")
        rank = _heading_rank(role, props, spec)
        if rank == 0 and pt < 18.0:
            warns.append(f"{role}: 封面/题名字号 {pt}pt 偏小，宜二号或小二（22/18pt）")
        if rank is not None:
            by_rank.setdefault(rank, []).append((role, pt))
    # Compare adjacent ranks: max(lower-rank size) must be > max(higher-rank size)
    ranks = sorted(by_rank)
    for i in range(len(ranks) - 1):
        r_hi, r_lo = ranks[i], ranks[i + 1]  # smaller rank = higher in hierarchy
        max_hi = max(pt for _, pt in by_rank[r_hi])
        max_lo = max(pt for _, pt in by_rank[r_lo])
        if max_hi <= max_lo:
            names_hi = ", ".join(r for r, _ in by_rank[r_hi])
            names_lo = ", ".join(r for r, _ in by_rank[r_lo])
            warns.append(
                f"字号未随层级递减: rank{r_hi}[{names_hi}]≤{max_hi}pt "
                f"vs rank{r_lo}[{names_lo}]≤{max_lo}pt（应为 题名>一级>二级>三级）")
    return warns


def validate_params(role_params: dict, expected: list[str], wl: dict) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    got = set(role_params.keys())
    exp = set(expected)
    missing, extra = sorted(exp - got), sorted(got - exp)
    if missing:
        problems.append(f"缺少角色参数: {missing}")
    if extra:
        warnings.append(f"多出未选角色（不阻断）: {extra}")

    tbl_keys = set((wl.get("table_format") or {}).keys())
    cell_keys = set((wl.get("cell_format") or {}).keys())
    buckets: dict[str, list[tuple[str, str]]] = {}  # object -> [(role, canonical props)]
    for role, spec in role_params.items():
        obj = spec.get("object", "?")
        props = spec.get("props", {}) or {}
        if obj == "table":
            tf = props.get("table_format", {}) or {}
            if not isinstance(tf, dict):
                tf = {}
            bad = sorted(set(tf) - tbl_keys)
            if bad:
                for k in bad:
                    tf.pop(k, None)
                warnings.append(f"{role}.table_format 已丢弃不支持的键: {bad}")
            for pos, cf in (props.get("cells", {}) or {}).items():
                if not isinstance(cf, dict):
                    continue
                badc = sorted(set(cf) - cell_keys)
                if badc:
                    for k in badc:
                        cf.pop(k, None)
                    warnings.append(f"{role}.cells.{pos} 已丢弃不支持的键: {badc}")
        else:
            allowed = _obj_keys(wl, obj)
            if not allowed:
                buckets.setdefault(obj, []).append(
                    (role, json.dumps(props, ensure_ascii=False, sort_keys=True)))
                continue
            bad = sorted(set(props) - allowed)
            if bad:
                for k in bad:
                    props.pop(k, None)
                warnings.append(f"{role}({obj}) 已丢弃不支持的键: {bad}")
        buckets.setdefault(obj, []).append((role, json.dumps(props, ensure_ascii=False, sort_keys=True)))

    # 同格式多角色：默认零碰撞；超阈由 run_one 带反馈补丁（不改 display_name）

    warnings.extend(_check_heading_size_ladder(role_params))

    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "n_roles": len(role_params), "missing": missing, "extra": extra}


def _flatten_nested_font(props: dict) -> int:
    """Normalize nested ``font: {latin, ea}`` to ``font.latin`` / ``font.ea`` for whitelist checks."""
    if not isinstance(props, dict):
        return 0
    if not isinstance(props.get("font"), dict):
        return 0
    expanded = expand_font_props(props)
    n = 1 + sum(1 for k in ("font.latin", "font.ea") if k in expanded and k not in props)
    props.clear()
    props.update(expanded)
    return n


def apply_readability_guards(role_params: dict, wl: dict | None = None) -> int:
    """确定性护栏：focus/snake_case 键与常见取值→officecli；丢掉白名单外的键；图片默认居中且去掉 crop；
    强制单栏（弃用 columns=2/3）；11pt→12pt（禁止 Word 默认字号）；字号下限五号；剥离节级 vAlign；校正 pageStart；
    有页面外框(pgBorders=box)时页眉二选一：≤1.2cm 框外或 ≥1.7cm 框内（避开横线冲突带），页脚 ≥1.7cm。"""
    n = 0
    for role, spec in role_params.items():
        obj = spec.get("object")
        props = spec.setdefault("props", {})
        n += coerce_officecli_prop_keys(props)
        n += _flatten_nested_font(props)
        if obj == "section":
            n += strip_wrong_layer_keys(props, obj="section")
        if obj == "table":
            n += _flatten_nested_font(props.get("table_format") or {})
            n += strip_wrong_layer_keys(props.get("table_format") or {}, obj="table_format")
            for cf in (props.get("cells") or {}).values():
                if isinstance(cf, dict):
                    n += _flatten_nested_font(cf)
                    n += strip_wrong_layer_keys(cf, obj="cell_format")
        for hf in ("header", "footer", "header_first"):
            blob = spec.get(hf)
            if isinstance(blob, dict):
                n += coerce_officecli_prop_keys(blob)
        if obj == "image":
            n += strip_wrong_layer_keys(props, obj="image")
            if not props.get("hAlign"):
                props["hAlign"] = "center"
                n += 1
        if obj == "section":
            if "vAlign" in props:
                props.pop("vAlign", None)  # 禁止页面垂直对齐；默认 top
                n += 1
            if "pageStart" in props:
                raw = props.get("pageStart")
                cleaned = sanitize_page_start(raw)
                if cleaned is None:
                    props.pop("pageStart", None)
                    n += 1
                elif cleaned != raw:
                    props["pageStart"] = cleaned
                    n += 1
            # 页码起始是节实例行为，不写在可复用 T* 上（由 instance_overrides 决定）
            if "pageStart" in props:
                props.pop("pageStart", None)
                n += 1
            # 弃用双栏：长文档一律单栏
            cols = str(props.get("columns", "1") or "1").strip()
            if cols != "1":
                props["columns"] = 1
                n += 1
            if "columnSpace" in props:
                props.pop("columnSpace", None)
                n += 1
            # 页面外框：页眉相对顶线二选一，禁止落在 1.2–1.7cm 冲突带
            #   ≤1.2cm → 框外（页眉在顶线之上）；≥1.7cm → 框内（页眉在顶线之下）
            if str(props.get("pgBorders") or "").lower() in ("box", "true"):
                hdr = _parse_len_cm(props.get("marginHeader"))
                if hdr is None:
                    props["marginHeader"] = "1.2cm"
                    n += 1
                elif 1.2 < hdr < 1.7:
                    props["marginHeader"] = "1.2cm" if hdr < 1.45 else "1.7cm"
                    n += 1
                ftr = _parse_len_cm(props.get("marginFooter"))
                if ftr is None or ftr < 1.7:
                    props["marginFooter"] = "1.7cm"
                    n += 1
        # 字号：禁止 11pt（Word 默认，不在八档）；低于五号抬到 10.5pt
        if obj in ("paragraph", "run") and "size" in props:
            pt = _parse_size_pt(props.get("size"))
            if pt is not None and abs(pt - 11.0) < 0.05:
                props["size"] = "12pt"
                n += 1
            elif pt is not None and pt < 10.5:
                props["size"] = "10.5pt"
                n += 1
        # 目录条目/目录页标题不得带大纲级别（否则会进 Word 导航并污染「目录」）
        if obj == "paragraph" and ("toc" in role.lower() or "目录" in str(
                (spec.get("display_name") or "") + role)):
            if "outlineLvl" in props:
                props.pop("outlineLvl", None)
                n += 1
    if wl:
        notes = drop_unlisted_officecli_props(role_params, wl)
        n += len(notes)
    return n


def _parse_len_cm(raw) -> float | None:
    """Parse length to cm (supports cm / mm / in / pt)."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(" ", "")
    m = re.match(r"^([\d.]+)(cm|mm|in|pt)?$", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2) or "cm"
    if unit == "cm":
        return val
    if unit == "mm":
        return val / 10.0
    if unit == "in":
        return val * 2.54
    if unit == "pt":
        return val * 2.54 / 72.0
    return None


# pageStart 必须是整数或 none；LLM/大纲常误把罗马显示值（i/ii）写进 pageStart
_ROMAN_PAGE = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
}


def sanitize_page_start(value):
    """Return int page start, or None to omit. Maps roman 'i'→1; rejects non-numeric junk."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    low = s.lower()
    if low in _ROMAN_PAGE:
        return _ROMAN_PAGE[low]
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 1


# ─────────────────────────── optional round-trip ───────────────────────────

def roundtrip_check(role_params: dict, out_dir: Path) -> list[str]:
    """Best-effort: write each role's props to a scratch docx via officecli, then validate.

    Catches keys/values officecli rejects. Non-fatal: every failure becomes a warning.
    Images are skipped (need a binary asset).
    """
    from LongDocFmtEval.common.officecli_runner import (add_props, close_docx, create_docx,
                                                       run_officecli, validate_docx)
    warns: list[str] = []
    doc = out_dir / "_roundtrip.docx"
    try:
        create_docx(doc)
        # numbering for list roles
        add_props(doc, "/numbering", typ="num", props={"id": 901, "format": "decimal", "text": "%1.", "start": 1})
        add_props(doc, "/numbering", typ="num", props={"id": 902, "format": "bullet", "start": 1})
    except Exception as e:  # noqa: BLE001
        return [f"round-trip 初始化失败，已跳过: {e}"]

    def para_props(props: dict) -> dict:
        p = dict(props)
        ls = p.pop("listStyle", None)
        if ls == "ordered":
            p["numId"] = 901
        elif ls == "bullet":
            p["numId"] = 902
        return p

    for role, spec in role_params.items():
        obj = spec.get("object", "?")
        props = spec.get("props", {}) or {}
        try:
            if obj in ("paragraph", "run"):
                add_props(doc, "/body", typ="paragraph", props={"text": f"probe:{role}", **para_props(props)})
            elif obj == "section":
                add_props(doc, "/body", typ="paragraph", props={"text": f"sec:{role}"})
                run_officecli(["set", str(doc), "/section[1]", *sum(
                    ([f"--prop", f"{k}={v}"] for k, v in props.items()), [])], check=True)
            elif obj == "table":
                add_props(doc, "/body", typ="table", props={"rows": 2, "cols": 2})
                # table-level props go on the last table
                tf = props.get("table_format", {}) or {}
                if tf:
                    run_officecli(["set", str(doc), "/body/table[last()]", *sum(
                        ([f"--prop", f"{k}={v}"] for k, v in tf.items() if k != "repeat_header"), [])], check=True)
                # repeat_header is row-scoped (w:tblHeader), not a table prop
                rep = tf.get("repeat_header")
                if isinstance(rep, bool):
                    want_rep = rep
                else:
                    want_rep = str(rep or "").strip().lower() in ("true", "1", "yes", "on")
                if want_rep or ("repeat_header" in tf):
                    run_officecli(
                        [
                            "set", str(doc), "/body/table[last()]/row[1]",
                            "--prop", f"header={'true' if want_rep else 'false'}",
                        ],
                        check=True,
                    )
                cells = props.get("cells", {}) or {}
                if cells:
                    first = next(iter(cells.values()))
                    run_officecli(["set", str(doc), "/body/table[last()]/row[1]/cell[1]", *sum(
                        ([f"--prop", f"{k}={v}"] for k, v in first.items()), [])], check=True)
            elif obj == "image":
                warns.append(f"{role}: image 需二进制素材，round-trip 跳过（仅白名单校验）")
        except Exception as e:  # noqa: BLE001
            warns.append(f"{role}({obj}) officecli 拒绝: {str(e).splitlines()[-1][:160]}")

    try:
        validate_docx(doc)
    except Exception as e:  # noqa: BLE001
        warns.append(f"validate 未通过: {str(e).splitlines()[-1][:160]}")
    finally:
        close_docx(doc)
    return warns


# ─────────────────────────── assembly of T* ───────────────────────────

def overlay_role_names_and_purposes(
    role_params: dict,
    *,
    name_by_role: dict,
    desc_by_role: dict,
    stylesheet: list | None,
    locale: str = "zh",
) -> None:
    """Ensure every filled style (incl. extra Para*Cell) has a locale name + 用途."""
    ss_name, ss_desc, _ = stylesheet_name_desc(stylesheet)
    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        dn = public_display_name(
            rid,
            spec.get("display_name"),
            name_by_role.get(rid),
            ss_name.get(rid),
            locale=locale,
        )
        if dn:
            spec["display_name"] = dn
        purpose = public_purpose(
            rid,
            spec.get("description"),
            desc_by_role.get(rid),
            ss_desc.get(rid),
            locale=locale,
        )
        if purpose:
            spec["description"] = purpose


def build_template(
    plan: dict, role_params: dict, desc_by_role: dict, name_by_role: dict, *, locale: str = "zh",
) -> dict:
    roles_full = {}
    for role, spec in role_params.items():
        entry = {
            "object": spec.get("object", "?"),
            "display_name": public_display_name(
                role, name_by_role.get(role), spec.get("display_name"), locale=locale,
            ) or "",
            "description": desc_by_role.get(role, ""),
            "example": spec.get("example", ""),
        }
        roles_full[role] = entry
    return {
        "meta": plan.get("meta", {}),
        "selection": plan.get("selection", {}),
        "roles": roles_full,
        "format_expectations": plan.get("format_expectations", []),
        "outline_ref": "见同名 step2 目录 plan.json 的 sections/outline（step4 撰写用）",
    }


_OBJ_TITLE = {"section": "节（section）", "paragraph": "段落（paragraph）", "run": "行内（run）",
              "table": "表格（table）", "image": "图片（image）"}
_OBJ_TITLE_EN = {"section": "Section", "paragraph": "Paragraph", "run": "Run (inline)",
                 "table": "Table", "image": "Image"}

# officecli 键 → 含义映射：config/officecli_prop_labels.yaml（zh）/ _en.yaml（en）
_LABELS_CACHE: dict[str, dict] = {}


def _labels(locale: str = "zh") -> dict:
    key = "en" if _is_en(locale) else "zh"
    if key not in _LABELS_CACHE:
        path = officecli_locale_yaml("officecli_prop_labels.yaml", locale)
        _LABELS_CACHE[key] = load_yaml(path) if path.exists() else {}
    return _LABELS_CACHE[key]


def _san(s) -> str:
    return str(s).replace("|", "／").replace("\n", " ").strip()


def _fmt_val(k: str, v, locale: str = "zh") -> str:
    val_map = _labels(locale).get("val_labels", {})
    s = str(v)
    if k in val_map and s in val_map[k]:
        return val_map[k][s]
    if _is_en(locale):
        if k == "lineSpacing" and s.endswith("x"):
            return f"{s[:-1]}×"
        if k == "columns":
            return f"{s} column(s)"
        return s
    if k == "lineSpacing" and s.endswith("x"):
        return f"{s[:-1]}倍"
    if k == "columns":
        return f"{s}栏"
    return s


def _describe_props(props: dict, locale: str = "zh") -> str:
    """把属性字典转成人话：『设置A为a、B为b；启用C、D』 / English: Set…; Enable…"""
    prop_label = _labels(locale).get("prop_labels", {})
    bool_label = _labels(locale).get("bool_labels", {})
    setters, enables = [], []
    for k, v in (props or {}).items():
        if v in (None, "", "none", False):
            continue
        if k in bool_label:
            if str(v).lower() == "true":
                enables.append(bool_label[k])
            continue
        setters.append(f"{prop_label.get(k, k)} to {_fmt_val(k, v, locale)}" if _is_en(locale)
                       else f"{prop_label.get(k, k)}为{_fmt_val(k, v, locale)}")
    parts = []
    if setters:
        if _is_en(locale):
            parts.append("Set " + ", ".join(setters))
        else:
            parts.append("设置" + "、".join(setters))
    if enables:
        if _is_en(locale):
            parts.append("Enable " + ", ".join(enables))
        else:
            parts.append("启用" + "、".join(enables))
    if _is_en(locale):
        return "; ".join(parts) or "baseline (no overrides)"
    return "；".join(parts) or "沿用基线（无覆盖）"


_POS_NAME = {"header": "表头行", "corner_header": "表头行·表角", "column_header": "表头行·列标题",
             "group_header": "表头行·分组", "sub_header": "表头行·次级",
             "stub": "行标签列", "body_label": "数据行·标签列", "body_code": "数据行·编码列",
             "body_value": "数据行·数值列", "data_value": "数据行·数值列", "code_cell": "数据行·编码列",
             "summary_cell": "数据行·摘要列", "body_note": "数据行·备注列", "table_note": "表注行",
             "total_value": "合计行", "total": "合计行",
             "question_no": "题号列", "prompt": "题干列", "option": "选项列", "response_box": "作答栏"}
_POS_NAME_EN = {"header": "header row", "corner_header": "header · corner", "column_header": "header · column",
                "group_header": "header · group", "sub_header": "header · sub",
                "stub": "stub column", "body_label": "body · label", "body_code": "body · code",
                "body_value": "body · value", "data_value": "body · value", "code_cell": "body · code",
                "summary_cell": "body · summary", "body_note": "body · note", "table_note": "table note",
                "total_value": "total row", "total": "total row",
                "question_no": "question no.", "prompt": "prompt", "option": "option", "response_box": "response"}


def _cell_name(pos: str, para_role: str | None, name_by_role: dict, locale: str = "zh") -> str:
    """单元格样式的自然名称（行/列语义优先）。"""
    pos_map = _POS_NAME_EN if _is_en(locale) else _POS_NAME
    if pos in pos_map:
        return pos_map[pos]
    if para_role and name_by_role.get(para_role):
        return name_by_role[para_role]
    return pos


def _describe_table(spec: dict, pos_para_role: dict, name_by_role: dict, locale: str = "zh") -> str:
    props = spec.get("props", {})
    tf = {k: v for k, v in (props.get("table_format") or {}).items() if k != "repeat_header"}
    rep = str((props.get("table_format") or {}).get("repeat_header", "")).lower() == "true"
    if _is_en(locale):
        lines = ["**Table-level**: " + _describe_props(tf, locale)
                 + ("; Enable repeat header row" if rep else "")]
        all_cells = "all cells"
        sep = ": "
    else:
        lines = ["**表级**：" + _describe_props(tf, locale) + ("；启用表头跨页重复" if rep else "")]
        all_cells = "全表单元格"
        sep = "："
    cells = props.get("cells", {}) or {}
    single = len(cells) == 1
    for pos, cf in cells.items():
        who = all_cells if single else _cell_name(pos, (pos_para_role or {}).get(pos), name_by_role, locale)
        lines.append(f"**{who}**{sep}{_describe_props(cf, locale)}")
    return "<br>".join(_san(x) for x in lines)


def template_md(genre: str, role_params: dict, desc_by_role: dict, name_by_role: dict, pos_para: dict,
                locale: str = "zh") -> str:
    """规则模板：每类对象一张表（名称 / 说明 / 格式要求）。"""
    order = ["section", "paragraph", "run", "table", "image"]
    by_obj: dict[str, list] = {o: [] for o in order}
    for role, spec in role_params.items():
        by_obj.setdefault(spec.get("object", "?"), []).append((role, spec))

    labels_name = "officecli_prop_labels_en.yaml" if _is_en(locale) else "officecli_prop_labels.yaml"
    titles = _OBJ_TITLE_EN if _is_en(locale) else _OBJ_TITLE
    if _is_en(locale):
        lines = [f"# T* rule template — {genre}", "",
                 "> One table per object type: **Name** · **Description** · **Format** "
                 "(natural language: Set …; Enable …).",
                 f"> Sample text and layout live in `template.docx` in this folder; "
                 f"officecli key glossary: `config/{labels_name}`.", ""]
        headers = "| Name | Description | Format |"
    else:
        lines = [f"# T* 规则模板 — {genre}", "",
                 "> 每类对象一张表：**名称**（角色的自然叫法）· **说明**（这个 role 是什么）· **格式要求**（自然语言：设置…为…；启用…）。",
                 f"> 各角色的示例文本与实际排版见同目录 `template.docx`；officecli 键与中文含义对照见 `config/{labels_name}`。", ""]
        headers = "| 名称 | 说明 | 格式要求 |"

    for obj in order:
        items = by_obj.get(obj) or []
        if not items:
            continue
        lines.append(f"## {titles.get(obj, obj)}  ({len(items)})")
        lines.append("")
        lines.append(headers)
        lines.append("|---|---|---|")
        for role, spec in items:
            name = name_by_role.get(role) or role
            desc = desc_by_role.get(role, "")
            if obj == "table":
                fmt = _describe_table(spec, pos_para.get(role) or {}, name_by_role, locale)
            else:
                fmt = _san(_describe_props(spec.get("props", {}), locale))
            lines.append(f"| **{_san(name)}** | {_san(desc)} | {fmt} |")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# ─────────────────────────── driver ───────────────────────────

def _normalize_existing_tstar(out_dir: Path) -> int:
    """Flatten nested font etc. on already-written T* so cache/rerender need no --overwrite."""
    rp = Path(out_dir) / "role_params.json"
    if not rp.is_file():
        return 0
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(data, dict) or not data:
        return 0
    n = apply_readability_guards(
        data, wl=load_yaml(officecli_locale_yaml("officecli_whitelist.yaml", "zh")),
    )
    if not n:
        return 0
    dump_json(rp, data)
    print(f"[normalize] 已摊平/护栏修正 {n} 处 → role_params.json（无需 --overwrite 重填参）")
    return n


def run_one(outline_dir: Path, temperature: float | None, max_tokens: int, roundtrip: bool,
            preview: bool = False, overwrite: bool = False, figures: bool = True,
            rerender: bool = False,
            respecimen: bool = False,
            share_max_group_size: int = DEFAULT_SHARE_MAX_GROUP_SIZE,
            share_max_groups: int = DEFAULT_SHARE_MAX_GROUPS,
            share_retries: int = DEFAULT_SHARE_RETRIES,
            missing_retries: int = DEFAULT_MISSING_RETRIES,
            out_dir: Path | None = None) -> bool:
    outline_dir = Path(outline_dir).resolve()
    # 缓存：默认 TEMPLATES_DIR/slug；pipeline_auto topic 树可传入 out_dir（step4_styles）
    out_dir = Path(out_dir).resolve() if out_dir else (TEMPLATES_DIR / outline_dir.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    if rerender or respecimen or preview:
        print("[hint] specimen / template.docx 已移至 step5（run_step5.py --rerender / --respecimen）")
        if rerender or respecimen:
            from LongDocFmtEval.pipeline_auto.auto_paths import sibling_step_dir  # noqa: PLC0415
            from LongDocFmtEval.pipeline_auto.scripts.run_step5 import run_one as run_step5  # noqa: PLC0415
            tpl_out = sibling_step_dir(out_dir, "template") or out_dir
            return run_step5(
                out_dir, out_dir=tpl_out, overwrite=False, figures=figures,
                rerender=True, respecimen=respecimen,
            )

    if not overwrite and (out_dir / "template.json").is_file() and (out_dir / "role_params.json").is_file():
        print(f"[cache] step4 已有 T*，跳过：{out_dir}")
        print("         （强制重填参请加 --overwrite；渲 template.docx 请跑 step5）")
        return True

    plan_path = outline_dir / "plan.json"
    lib_path = outline_dir / "selected_library.yaml"
    if not plan_path.exists() or not lib_path.exists():
        print(f"[skip] 缺 plan.json 或 selected_library.yaml: {outline_dir}")
        return False
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    lib = yaml.safe_load(lib_path.read_text(encoding="utf-8"))
    from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import (  # noqa: PLC0415
        drop_abstract_role_params,
        strip_abstract_roles,
    )
    try:
        abs_notes = strip_abstract_roles(plan, lib)
        if abs_notes:
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            ss = plan.get("stylesheet")
            if isinstance(ss, list):
                (outline_dir / "stylesheet.json").write_text(
                    json.dumps(ss, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            for msg in abs_notes[:8]:
                print(f"[fix] {msg}")
            print(f"[fix] strip_abstract_roles: {len(abs_notes)} 处 abstract 基类已剔除/改挂叶子")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] strip_abstract_roles 失败: {e}")
    meta = plan.get("meta", {})
    genre, locale = meta.get("genre", "?"), meta.get("locale", "zh")
    slug = out_dir.name

    # 大纲只留在 step3_outlines；step4/5 用 resolve_work_file 读取，不再复制
    from LongDocFmtEval.pipeline_auto.auto_paths import drop_redundant_copies  # noqa: PLC0415
    dropped = drop_redundant_copies(out_dir)
    if dropped:
        print(f"[dedupe] 去掉与 step3 重复的 {', '.join(dropped)}")

    if (out_dir / "template.json").exists() and not overwrite:
        print(f"[cache] T* 已存在，跳过 LLM：{out_dir}")
        print("         （强制重填参请加 --overwrite；渲 template.docx 请跑 step5）")
        return True

    from LongDocFmtEval.common.llm_usage import print_usage_summary, usage_scope, write_step_usage_report  # noqa: PLC0415

    with usage_scope("step4") as _usage_rows:
        selected_roles, expected = assemble_selected_roles(plan, lib)
        layer_counts = count_from_selection(plan.get("selection") or {})
        # per-role 说明（示例文本据此对齐）：库内 discriminators
        desc_by_role: dict[str, str] = {}
        for r in selected_roles["section"] + selected_roles["paragraph"] + selected_roles["image"] + selected_roles["table"]:
            desc_by_role[r.get("role_id")] = r.get("discriminators", "")
        for r in selected_roles["run"]:
            disc = r.get("discriminators") or ""
            if isinstance(disc, list):
                disc = "；".join(str(x) for x in disc if x)
            desc_by_role[r["span_role"]] = disc or (
                "行内语义标记（run）：" + "、".join(r.get("attaches_to", [])[:3]))

        # per-role 自然名称：stylesheet / 库 display_name；禁止回退成 style_id
        name_by_role: dict[str, str] = {}
        for r in selected_roles["section"] + selected_roles["paragraph"] + selected_roles["image"] + selected_roles["table"]:
            rid_ = r.get("role_id")
            name_by_role[rid_] = public_display_name(
                rid_, r.get("display_name"), locale=locale,
            ) or str(r.get("display_name") or "")
        for r in selected_roles["run"]:
            sid = r["span_role"]
            name_by_role[sid] = public_display_name(
                sid, r.get("display_name"), locale=locale,
            ) or str(r.get("display_name") or "")
        for item in plan.get("stylesheet") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("style_id") or "")
            dn = public_display_name(sid, item.get("display_name"), locale=locale)
            if sid and dn:
                name_by_role[sid] = dn
            desc = str(item.get("description") or "").strip()
            if sid and desc:
                desc_by_role[sid] = desc

        genre_label = f"{lib.get('display_name', genre)}（{genre}）"
        if _is_en(locale):
            genre_label = f"{lib.get('display_name', genre)} ({genre})"
        context = format_theme_context(
            locale,
            genre=genre_label,
            theme=str(meta.get("theme", "") or ""),
            writing_requirements=str(meta.get("writing_requirements", "") or ""),
        )

        wl = load_yaml(officecli_locale_yaml("officecli_whitelist.yaml", locale))
        apply_officecli_keys_to_selected_roles(selected_roles, wl)
        prompt = load_prompt(_prompt_path(locale, "step4_fill_role_params.yaml", "step3_fill_role_params.yaml"))
        user = render_template(prompt["user_template"], context=context, selected_roles=selected_roles,
                               property_whitelist=whitelist_text(wl, locale))
        user = taxonomy_brief(locale=locale) + "\n\n" + user

        print(f"\n=== step4 template T*: {slug} ===  ({len(expected)} 角色)")
        print(f"[style-layers] {format_ref_roles(layer_counts)}")
        try:
            dump_json(
                out_dir / "style_layers.json",
                layers_payload(selection=plan.get("selection"), stylesheet=plan.get("stylesheet")),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 style_layers.json 失败: {e}")
        zero = share_max_group_size <= 1 and share_max_groups <= 0
        policy = "不允许同格式碰撞" if zero else (
            f"单组≤{share_max_group_size} 角色、组数≤{share_max_groups}"
        )
        print(f"[share-threshold] {policy}；"
              f"超阈最多补丁修订 {share_retries} 轮；"
              f"缺角色最多补丁 {missing_retries} 轮（页眉计入 section 签名）")
        temperature = temperature if temperature is not None else 0.2

        role_params: dict = {}
        raw = ""
        groups: list[list[str]] = []
        for attempt in range(share_retries + 1):
            if attempt == 0:
                user_round = user
                system_round = prompt["system"]
            else:
                fix_roles = roles_to_differentiate(
                    groups,
                    max_group_size=share_max_group_size,
                    max_groups=share_max_groups,
                )
                user_round = collision_patch_user(
                    groups, role_params, name_by_role,
                    max_group_size=share_max_group_size,
                    max_groups=share_max_groups,
                    locale=locale,
                )
                system_round = (
                    prompt["system"]
                    + ("\n\n## Patch mode\n"
                       "You are fixing format collisions only. Output a **partial** "
                       "`role_params` covering just the roles to revise; the host merges it."
                       if _is_en(locale) else
                       "\n\n## 补丁模式\n"
                       "你只需修订格式碰撞角色。输出**局部** `role_params`（仅含待改角色）；"
                       "宿主程序会合并进上一版，禁止整表重写。")
                )
                print(
                    f"    [share-retry {attempt}/{share_retries}] 同格式超阈 → "
                    f"仅修订 {len(fix_roles) or '?'} 个碰撞角色（补丁合并，非整表重生成）…"
                )
            raw = stream_llm_text(system=system_round, user=user_round,
                                  temperature=temperature, max_tokens=max_tokens)
            try:
                data = lenient_json_load(raw)
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] 解析失败: {e}")
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "raw.txt").write_text(raw, encoding="utf-8")
                try:
                    write_step_usage_report(out_dir, _usage_rows, step="step4")
                except Exception:  # noqa: BLE001
                    pass
                return False
            incoming = data.get("role_params") or {}
            dropped = drop_abstract_role_params(incoming, lib)
            if dropped:
                print(f"    [fix] 丢弃 abstract T*: {', '.join(dropped)}")
            if attempt == 0:
                role_params = incoming
            else:
                expected_fix = roles_to_differentiate(
                    groups,
                    max_group_size=share_max_group_size,
                    max_groups=share_max_groups,
                )
                role_params, merge_warns = merge_role_params_patch(
                    role_params, incoming, expected_fix=expected_fix,
                )
                for w in merge_warns:
                    print(f"    [share-patch] {w}")
                # If model returned a full table, merging still OK; log size hint
                if len(incoming) > max(4, len(expected_fix) * 3):
                    print(
                        f"    [share-patch] 模型返回 {len(incoming)} 个角色"
                        f"（建议仅 {len(expected_fix)}）；已按键合并，未覆盖的角色保持上一版"
                    )
            groups = find_collisions(role_params)
            if not share_exceeds_threshold(
                groups, max_group_size=share_max_group_size, max_groups=share_max_groups,
            ):
                break
        else:
            # 仍超阈：保留最后一版，记入 validation problems（默认零碰撞时 ok=False）
            print(f"    [share] 仍超阈：{len(groups)} 组（max_size="
                  f"{max((len(g) for g in groups), default=0)}），保留末版并记入 validation 问题")

        # 缺失角色：与同格式补丁类似，只补漏项（常见于长表末尾漏掉 run）
        for miss_i in range(1, max(0, int(missing_retries)) + 1):
            missing_now = [r for r in expected if r not in role_params]
            if not missing_now:
                break
            print(
                f"    [missing-retry {miss_i}/{missing_retries}] "
                f"缺少 {len(missing_now)} 个角色 → 仅补丁填充：{missing_now}"
            )
            user_round = missing_roles_patch_user(
                missing_now, selected_roles, locale=locale,
            )
            system_round = (
                prompt["system"]
                + ("\n\n## Patch mode\n"
                   "You are filling **missing** roles only. Output a **partial** "
                   "`role_params` with just those keys; the host merges it."
                   if _is_en(locale) else
                   "\n\n## 补丁模式\n"
                   "你只需补全**缺失**角色。输出**局部** `role_params`（仅含缺失键）；"
                   "宿主程序会合并进上一版。")
            )
            raw = stream_llm_text(system=system_round, user=user_round,
                                  temperature=temperature, max_tokens=max_tokens)
            try:
                data = lenient_json_load(raw)
            except Exception as e:  # noqa: BLE001
                print(f"    [missing-patch] 解析失败: {e}")
                continue
            incoming = data.get("role_params") or {}
            drop_abstract_role_params(incoming, lib)
            role_params, merge_warns = merge_role_params_patch(
                role_params, incoming,
                expected_fix=missing_now,
                allow_new=True,
                allowed_new=missing_now,
            )
            for w in merge_warns:
                print(f"    [missing-patch] {w}")
        still_missing = [r for r in expected if r not in role_params]
        if still_missing:
            print(f"    [missing] 仍缺角色：{still_missing}（将记入 validation 问题）")
        groups = find_collisions(role_params)
        # missing 补丁可能重新引入同格式碰撞；再跑一轮 share 补丁
        for attempt in range(1, share_retries + 1):
            if not share_exceeds_threshold(
                groups, max_group_size=share_max_group_size, max_groups=share_max_groups,
            ):
                break
            fix_roles = roles_to_differentiate(
                groups,
                max_group_size=share_max_group_size,
                max_groups=share_max_groups,
            )
            print(
                f"    [share-retry after-missing {attempt}/{share_retries}] 同格式超阈 → "
                f"仅修订 {len(fix_roles) or '?'} 个碰撞角色（补丁合并，非整表重生成）…"
            )
            user_round = collision_patch_user(
                groups, role_params, name_by_role,
                max_group_size=share_max_group_size,
                max_groups=share_max_groups,
                locale=locale,
            )
            system_round = (
                prompt["system"]
                + ("\n\n## Patch mode\n"
                   "You are fixing format collisions only. Output a **partial** "
                   "`role_params` covering just the roles to revise; the host merges it."
                   if _is_en(locale) else
                   "\n\n## 补丁模式\n"
                   "你只需修订格式碰撞角色。输出**局部** `role_params`（仅含待改角色）；"
                   "宿主程序会合并进上一版，禁止整表重写。")
            )
            raw = stream_llm_text(system=system_round, user=user_round,
                                  temperature=temperature, max_tokens=max_tokens)
            try:
                data = lenient_json_load(raw)
            except Exception as e:  # noqa: BLE001
                print(f"    [share-patch] 解析失败: {e}")
                break
            incoming = data.get("role_params") or {}
            drop_abstract_role_params(incoming, lib)
            role_params, merge_warns = merge_role_params_patch(
                role_params, incoming, expected_fix=fix_roles,
            )
            for w in merge_warns:
                print(f"    [share-patch] {w}")
            groups = find_collisions(role_params)

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw.txt").write_text(raw, encoding="utf-8")

        if groups:
            for g in groups:
                labels = [name_by_role.get(r) or r for r in sorted(g)]
                obj = (role_params.get(g[0]) or {}).get("object", "?")
                print(f"    [share][{obj}] 同格式: {' ≈ '.join(labels)}")

        n_hl = apply_heading_level_to_role_params(role_params, plan)
        if n_hl:
            print(f"    heading_level → outlineLvl：已落实 {n_hl} 处")
        dropped_abs = drop_abstract_role_params(role_params, lib)
        if dropped_abs:
            print(f"    [fix] 丢弃 abstract T*: {', '.join(dropped_abs)}")
        n_guard = apply_readability_guards(role_params, wl=wl)
        if n_guard:
            print(f"    映射护栏：已修正 {n_guard} 处（focus/snake_case→officecli 键值 / 丢掉不支持的键 / 图片居中 / 强制单栏 / 字号下限 等）")

        overlay_role_names_and_purposes(
            role_params,
            name_by_role=name_by_role,
            desc_by_role=desc_by_role,
            stylesheet=plan.get("stylesheet"),
            locale=locale,
        )

        dump_json(out_dir / "role_params.json", role_params)
        report = validate_params(role_params, expected, wl)
        if groups:
            report["style_shares"] = [
                {"object": (role_params.get(g[0]) or {}).get("object", "?"),
                 "members": sorted(g),
                 "member_labels": [name_by_role.get(r) or r for r in sorted(g)]}
                for g in groups
            ]
            if share_exceeds_threshold(
                groups, max_group_size=share_max_group_size, max_groups=share_max_groups,
            ):
                msg = (
                    f"同格式仍超阈（组数={len(groups)}，上限组数={share_max_groups}，"
                    f"单组上限={share_max_group_size}）："
                    + "；".join(
                        f"{(role_params.get(g[0]) or {}).get('object','?')}:"
                        + ",".join(sorted(g)) for g in groups
                    )
                )
                report["problems"].append(msg)
                report["ok"] = False
            elif groups:
                # 旧阈值下可能仍有「允许的成对共用」；零碰撞时 groups 应为空
                report["warnings"].append(
                    "存在同格式组但未超阈（当前 CLI 阈值仍允许）："
                    + "；".join(",".join(sorted(g)) for g in groups)
                )
        no_example = sorted(r for r, s in role_params.items() if not str(s.get("example", "")).strip())
        if no_example:
            report["warnings"].append(f"缺示例文本 example 的角色: {no_example}")
        if roundtrip:
            print("    round-trip 验证中（officecli）…")
            report["warnings"] += roundtrip_check(role_params, out_dir)
        dump_json(out_dir / "validation.json", report)

        pos_para = {t.get("role_id"): {pos: (m or {}).get("paragraph")
                                       for pos, m in ((t.get("table_layout") or {}).get("position_map") or {}).items()}
                    for t in (lib.get("roles", {}).get("table") or [])}
        dump_json(out_dir / "template.json", build_template(
            plan, role_params, desc_by_role, name_by_role, locale=locale,
        ))
        (out_dir / "template.md").write_text(
            template_md(genre, role_params, desc_by_role, name_by_role, pos_para, locale), encoding="utf-8")

        tag = "OK" if report["ok"] else "有问题"
        print(f"[out] {out_dir}")
        print(f"[validate] {tag} | 角色={report['n_roles']} 问题={len(report['problems'])} 警告={len(report['warnings'])}")
        for p in report["problems"][:12]:
            print(f"   ✗ {p}")
        for w in report["warnings"][:8]:
            print(f"   · {w}")

        try:
            _ureport = write_step_usage_report(out_dir, _usage_rows, step="step4")
            print_usage_summary(_ureport["summary"], prefix="[step4 tokens]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 llm_usage.json 失败: {e}")
        return report["ok"]


def _outline_ids_under(theme_path: Path) -> list[int]:
    ids: list[int] = []
    if not theme_path.is_dir():
        return ids
    for p in sorted(theme_path.glob("outline_*")):
        if not p.is_dir():
            continue
        m = re.match(r"outline_(\d+)$", p.name)
        if m:
            ids.append(int(m.group(1)))
    return ids


def _jobs_for_topic_ids(
    genre_ids: list[int],
    theme_ids: list[int],
    *,
    rerender: bool,
) -> list[tuple[Path, Path, str]]:
    """Resolve (outline_dir, template_out, label) for pipeline_auto topic tree.

    Modern: ``datasets/{id}_*/C*/step3_outlines`` → ``C*/step4_styles``.
    Legacy: ``work/topics/.../step3/theme_XX/outline_YY``.
    Flat fallback: ``work/step3_outlines``.
    """
    from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.topic_layout import lookup_genre_row  # noqa: PLC0415

    def _label(path: Path) -> str:
        for root in (DATASETS_DIR, TOPICS_DIR):
            try:
                return str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
        return path.name

    jobs: list[tuple[Path, Path, str]] = []
    for gid in genre_ids:
        try:
            topic = resolve_topic_dir(gid)
        except FileNotFoundError:
            topic = None
        if topic is None or not topic.is_dir():
            info = lookup_genre_row(gid)
            try:
                draft = resolve_draft_dir(
                    ROLE_LIBRARY_DRAFTS_DIR,
                    gid,
                    genre=info.get("genre") or "",
                    locale=info.get("locale") or "",
                )
            except FileNotFoundError as e:
                raise FileNotFoundError(str(e)) from e
            for tid in theme_ids:
                d = resolve_outline_dir(OUTLINES_DIR, draft, tid, genre_id=gid)
                jobs.append((d, TEMPLATES_DIR / d.name, f"id={gid} theme={tid} flat→{d.name}"))
            continue

        for tid in theme_ids:
            od = topic_outline_dir(topic, tid)
            td = topic_styles_dir(topic, tid)
            if rerender:
                if not (td / "template.json").is_file():
                    continue
                src = od if (od / "plan.json").is_file() else td
                jobs.append((src, td, f"id={gid} theme={tid} → {_label(td)}"))
            else:
                if not (od / "plan.json").is_file():
                    continue
                jobs.append((od, td, f"id={gid} theme={tid} → {_label(td)}"))
    return jobs


def main():
    ap = argparse.ArgumentParser(
        description="Step4：为 step3 大纲填 T* → role_params.json / template.json。推荐 --id + --theme。"
    )
    ap.add_argument("--id", dest="genre_id", type=int, default=None,
                    help="体裁 id（与 step1/step2/step3 相同）")
    ap.add_argument("--ids", default="", help="多个体裁 id，如 1,26")
    ap.add_argument("--theme", type=int, default=None, help="主题 id（默认 1）")
    ap.add_argument("--themes", default="", help="多个主题 id，如 1,2 或 1-3")
    ap.add_argument("--outline-dir", help="（兼容）单条 step3 产出目录")
    ap.add_argument("--all", action="store_true", help="批量：处理 work/step3_outlines 下全部目录")
    ap.add_argument("--roundtrip", action="store_true", help="额外用 officecli 写回验证（较慢）")
    ap.add_argument("--overwrite", action="store_true",
                    help="强制重跑 LLM 覆盖 T*（默认：已有 template.json 则跳过）")
    ap.add_argument("--rerender", action="store_true",
                    help="（已移至 step5）转发到 run_step5.py --rerender")
    ap.add_argument("--respecimen", action="store_true",
                    help="（已移至 step5）转发到 run_step5.py --respecimen")
    ap.add_argument("--no-preview", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-figures", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--temperature", type=float, default=None, help="不填默认 0.2")
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--share-max-group-size", type=int, default=DEFAULT_SHARE_MAX_GROUP_SIZE,
                    help=f"同格式单组最多角色数（默认 {DEFAULT_SHARE_MAX_GROUP_SIZE}=零碰撞；2=允许成对）")
    ap.add_argument("--share-max-groups", type=int, default=DEFAULT_SHARE_MAX_GROUPS,
                    help=f"同格式组数上限（默认 {DEFAULT_SHARE_MAX_GROUPS}=零碰撞）；超阈带反馈补丁")
    ap.add_argument("--share-retries", type=int, default=DEFAULT_SHARE_RETRIES,
                    help=f"超阈后最多再补丁轮数（默认 {DEFAULT_SHARE_RETRIES}）")
    ap.add_argument("--missing-retries", type=int, default=DEFAULT_MISSING_RETRIES,
                    help=f"缺少角色参数时最多补丁轮数（默认 {DEFAULT_MISSING_RETRIES}）")
    args = ap.parse_args()

    if args.overwrite and args.rerender:
        print("[warn] 同时指定了 --overwrite 与 --rerender：本次只按 --rerender（不重跑 LLM）")

    share_kw = dict(
        share_max_group_size=args.share_max_group_size,
        share_max_groups=args.share_max_groups,
        share_retries=args.share_retries,
        missing_retries=args.missing_retries,
    )

    theme_ids = parse_ids_arg(args.themes) if args.themes else ([] if args.theme is None else [args.theme])
    genre_ids = parse_ids_arg(args.ids) if args.ids else ([] if args.genre_id is None else [args.genre_id])
    if genre_ids and not theme_ids:
        theme_ids = [1]

    if genre_ids:
        try:
            jobs = _jobs_for_topic_ids(genre_ids, theme_ids, rerender=bool(args.rerender))
        except FileNotFoundError as e:
            ap.error(str(e))
        if not jobs:
            ap.error(
                "找不到可处理的 step2/step3 产物（topic 树 work/topics/topic_XX_* 或扁平 step2_outlines）。\n"
                "请先跑 pipeline step2/step3，或检查 --ids / --theme。"
            )
        print(f"[batch] {len(jobs)} 个样例参与（优先 topics/ 树）")
        ok = 0
        for idx, (od, td, label) in enumerate(jobs, 1):
            print(f"\n----- ({idx}/{len(jobs)}) {label} -----")
            if run_one(
                od, args.temperature, args.max_tokens, args.roundtrip,
                preview=False, overwrite=args.overwrite,
                figures=not args.no_figures, rerender=args.rerender,
                respecimen=bool(args.respecimen),
                out_dir=td, **share_kw,
            ):
                ok += 1
        print(f"\n[done] {ok}/{len(jobs)} 通过校验")
        return

    if args.all:
        # Prefer topic-tree step3 outlines → step4 templates; else flat outlines
        jobs: list[tuple[Path, Path, str]] = []
        if TOPICS_DIR.is_dir():
            for topic in sorted(p for p in TOPICS_DIR.iterdir() if p.is_dir() and p.name.startswith("topic_")):
                for step_outlines in (topic / "step3", topic / "step2"):
                    if not step_outlines.is_dir():
                        continue
                    for th in sorted(step_outlines.glob("theme_*")):
                        m = re.match(r"theme_(\d+)$", th.name)
                        if not m:
                            continue
                        tid = int(m.group(1))
                        for oi in _outline_ids_under(th):
                            od = topic_outline_dir(topic, tid, oi)
                            td = topic_styles_dir(topic, tid, oi)
                            if args.rerender and not (td / "template.json").is_file():
                                continue
                            if not args.rerender and not (od / "plan.json").is_file():
                                continue
                            jobs.append((od if (od / "plan.json").is_file() else td, td, f"{topic.name}/{th.name}/outline_{oi:02d}"))
                    if jobs:
                        break
        if not jobs:
            dirs = sorted([d for d in OUTLINES_DIR.iterdir() if d.is_dir()]) if OUTLINES_DIR.exists() else []
            if not dirs:
                ap.error(f"{TOPICS_DIR} / {OUTLINES_DIR} 下没有可处理目录，请先跑 step3")
            jobs = [(d, TEMPLATES_DIR / d.name, d.name) for d in dirs]
        print(f"[batch] {len(jobs)} 个样例参与")
        ok = 0
        for idx, (od, td, label) in enumerate(jobs, 1):
            print(f"\n----- ({idx}/{len(jobs)}) {label} -----")
            if run_one(
                od, args.temperature, args.max_tokens, args.roundtrip,
                preview=False, overwrite=args.overwrite,
                figures=not args.no_figures, rerender=args.rerender,
                respecimen=bool(args.respecimen),
                out_dir=td, **share_kw,
            ):
                ok += 1
        print(f"\n[done] {ok}/{len(jobs)} 通过校验")
        return
    if args.outline_dir:
        d = Path(args.outline_dir)
        if not d.exists():
            alt = Path("LongDocFmtEval") / args.outline_dir
            if not str(args.outline_dir).replace("\\", "/").startswith("LongDocFmtEval/") and alt.exists():
                d = alt
            else:
                ap.error(f"目录不存在: {d}")
        # If user points at a step3 folder, treat it as both outline+out
        out = d if (d / "template.json").is_file() else (TEMPLATES_DIR / d.name)
        run_one(d, args.temperature, args.max_tokens, args.roundtrip,
                preview=False, overwrite=args.overwrite,
                figures=not args.no_figures, rerender=args.rerender,
                respecimen=bool(args.respecimen),
                out_dir=out, **share_kw)
        return
    ap.error("请指定 --id <体裁id> [--theme <主题id>]，或 --outline-dir / --all")


if __name__ == "__main__":
    main()
