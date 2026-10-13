"""Structure brief helpers: slim recommend budgets + C1/C2/C3 relative order."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from LongDocFmtEval.common.role_library import is_abstract_role_id
from LongDocFmtEval.pipeline_auto.seed.heading_detect import collect_heading_hits  # noqa: E402

# Hard-order dimensions only (soft targets; rich libraries should show clear gaps).
_ORDER_KEYS = (
    "section_count",
    "heading_depth",
    "stylesheet_role_count",
)

# Soft guides for rich libraries (annual-report scale). Small libraries: stay inside inventory;
# still prefer clear ~10-style gaps when the catalog allows.
# Anchors ≈ C1 30 / C2 40 / C3 50 (post-merge stylesheet styles); never exceed concrete Role leaves.
_RECOMMEND_GUIDE: dict[str, dict[str, tuple[int, int]]] = {
    "C1": {"section_count": (5, 7), "heading_depth": (3, 4), "stylesheet_role_count": (28, 34)},
    "C2": {"section_count": (6, 8), "heading_depth": (4, 5), "stylesheet_role_count": (38, 44)},
    "C3": {"section_count": (7, 9), "heading_depth": (5, 6), "stylesheet_role_count": (48, 54)},
}

# Outline block quotas were removed: tier gaps live in stylesheet (+ section_count /
# heading_depth). Block volume is theme-driven long-doc writing, not a C-tier dial.

_STYLE_CATEGORIES = ("section", "paragraph", "table", "figure", "run", "other")


def _mid(lo: int, hi: int) -> int:
    return (int(lo) + int(hi)) // 2


def _as_int(val: Any, default: int | None = None) -> int | None:
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _recommend_defaults(complexity: str) -> dict[str, int]:
    cx = str(complexity or "C2").strip().upper()
    if cx not in _RECOMMEND_GUIDE:
        cx = "C3" if cx.startswith("C") else "C2"
    guide = _RECOMMEND_GUIDE[cx]
    return {k: _mid(*guide[k]) for k in _ORDER_KEYS}


def _default_category_suggest(complexity: str, *, locale: str = "zh") -> dict[str, Any]:
    """Per-class hints. section/paragraph: merge intensity only (no count quotas)."""
    cx = str(complexity or "C2").strip().upper()
    en = str(locale).lower().startswith("en")
    if cx == "C1":
        notes = {
            "section": (
                "Strong merge: collapse near-duplicate section families; omit niche layouts."
                if en else
                "强合并：近义节族并入少数 section style；省略冷门版式。"
            ),
            "paragraph": (
                "Strong merge on body AND cell-text: fold near-body variants; "
                "share one small cell ladder (header/stub/data/note) across tables — "
                "do not keep a separate Para*Cell family per Tbl*."
                if en else
                "正文与格内文字都强合并：近义正文并入少数 style；"
                "跨表共用一套格内阶梯（表头/行名/数据/附注），禁止每个 Tbl* 各留一套 Para*Cell。"
            ),
            "table": (
                "Near-parity with richer tiers: keep purpose-distinct table families "
                "(same as C3 or at most −1). Do not thin tables to hit the C1 total-style budget."
                if en else
                "与高档近齐：用途可区分的表家族尽量保留（与 C3 同档或至多少 1）；"
                "勿为压总 style 数而砍表。"
            ),
            "figure": (
                "Near-parity with richer tiers: keep distinct image_kind families "
                "(same as C3 or at most −1)."
                if en else
                "与高档近齐：可区分的图类型尽量保留（与 C3 同档或至多少 1）。"
            ),
            "run": (
                "Minimal run styles."
                if en else
                "run 样式从简。"
            ),
            "other": "",
        }
        # Table/image: stay near C2/C3 (library is usually small); C-tier dial is section/paragraph.
        soft_nums = {"table": 4, "figure": 3, "run": 2, "other": 0}
    elif cx == "C2":
        notes = {
            "section": (
                "Light merge: keep more section distinctions than C1 when format differs."
                if en else
                "轻合并：格式可区分的 section 家族比 C1 多保留。"
            ),
            "paragraph": (
                "Light merge: keep risk/list/body distinctions when the library supports them; "
                "share similar Para*Cell across tables when grid chrome matches."
                if en else
                "轻合并：库中可区分的正文/风险/列表尽量保留；"
                "网格相近时跨表共用 Para*Cell。"
            ),
            "table": (
                "Keep purpose-distinct table families ≈ library (parity with C3 or −0/−1)."
                if en else
                "用途可区分的表家族≈库内可用（与 C3 齐或至多少 1）。"
            ),
            "figure": (
                "Keep purpose-distinct figure families ≈ library (parity with C3 or −0/−1)."
                if en else
                "用途可区分的图家族≈库内可用（与 C3 齐或至多少 1）。"
            ),
            "run": (
                "Light run coverage."
                if en else
                "run 轻度覆盖。"
            ),
            "other": "",
        }
        soft_nums = {"table": 4, "figure": 3, "run": 2, "other": 0}
    else:
        notes = {
            "section": (
                "Minimal merge: preserve distinguishable section roles inside the library."
                if en else
                "少合并：库内可区分的 section 尽量保留。"
            ),
            "paragraph": (
                "Minimal merge: widest practical paragraph distinctions ≤ library."
                if en else
                "少合并：paragraph 尽量保留库内可区分项，仍不超库。"
            ),
            "table": (
                "Keep all purpose-distinct table families ≤ library (anchor for lower tiers)."
                if en else
                "用途可区分的表家族尽量全留（≤库；作为低档对齐锚点）。"
            ),
            "figure": (
                "Keep all purpose-distinct figure families ≤ library (anchor for lower tiers)."
                if en else
                "用途可区分的图家族尽量全留（≤库；作为低档对齐锚点）。"
            ),
            "run": (
                "Keep useful run distinctions."
                if en else
                "保留有用的 run 区分。"
            ),
            "other": (
                "Optional extras if present in library."
                if en else
                "库中有则可选保留。"
            ),
        }
        soft_nums = {"table": 5, "figure": 4, "run": 2, "other": 1}
    out: dict[str, Any] = {}
    for cat in _STYLE_CATEGORIES:
        # section / paragraph: no numeric quota — merge intensity via note only
        if cat in ("section", "paragraph"):
            out[cat] = {"suggest": None, "note": notes.get(cat, "")}
        else:
            out[cat] = {"suggest": int(soft_nums.get(cat, 0)), "note": notes.get(cat, "")}
    return out


def _parse_recommend(raw: Any) -> dict[str, int]:
    src: dict[str, Any] = {}
    if isinstance(raw, dict):
        if isinstance(raw.get("recommend"), dict):
            src.update(raw["recommend"])
        for k in _ORDER_KEYS:
            if k in raw and raw[k] is not None:
                src.setdefault(k, raw[k])
        if "heading_depth" not in src:
            legacy = _as_int(raw.get("heading_depth_target")) or _as_int(raw.get("heading_depth_min"))
            if legacy is not None:
                src["heading_depth"] = legacy
        br = raw.get("branching") if isinstance(raw.get("branching"), dict) else {}
        if "section_count" not in src and br.get("h1_count"):
            m = re.match(r"(\d+)\s*[-–~]\s*(\d+)", str(br.get("h1_count")))
            if m:
                src["section_count"] = _mid(int(m.group(1)), int(m.group(2)))
            else:
                n = _as_int(br.get("h1_count"))
                if n is not None:
                    src["section_count"] = n
        # legacy fat recommend → keep only order keys
    out: dict[str, int] = {}
    for k in _ORDER_KEYS:
        n = _as_int(src.get(k))
        if n is not None and n >= 0:
            out[k] = n
    return out


def _parse_recommend_notes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    src = raw.get("recommend_notes") if isinstance(raw.get("recommend_notes"), dict) else {}
    out: dict[str, str] = {}
    for k in _ORDER_KEYS:
        val = src.get(k)
        if isinstance(val, str) and val.strip():
            out[k] = val.strip()
    return out


def _parse_stylesheet_by_category(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    src = raw.get("stylesheet_by_category")
    if not isinstance(src, dict):
        return {}
    out: dict[str, Any] = {}
    for cat, val in src.items():
        key = str(cat).strip().lower()
        if not key:
            continue
        if isinstance(val, dict):
            note = str(val.get("note") or "").strip()
            raw_suggest = val.get("suggest")
            # section/paragraph (and explicit null): keep qualitative — no forced count
            if raw_suggest is None or str(raw_suggest).strip().lower() in ("", "null", "none", "-"):
                out[key] = {"suggest": None, "note": note}
            else:
                suggest = _as_int(raw_suggest, None)
                out[key] = {"suggest": None if suggest is None else max(0, suggest), "note": note}
        else:
            n = _as_int(val, None)
            out[key] = {"suggest": None if n is None else max(0, n), "note": ""}
    return out


def _default_stylesheet_plan(complexity: str, *, locale: str = "zh") -> dict[str, Any]:
    """Actionable merge/omit/keep plan for step3 (stronger than bare counts).

    ``keep_distinct`` stays a short *anchor* list (must-not-merge-away), never a near-full inventory.
    """
    cx = str(complexity or "C2").strip().upper()
    en = str(locale).lower().startswith("en")
    if cx == "C1":
        return {
            "keep_distinct": [],
            "merge_groups": [
                {
                    "into": "ParaBody",
                    "from_examples": ["ParaRisk*", "ParaNarrative*", "ParaMD&A*", "ParaBullet*"],
                    "note": (
                        "C1: collapse near-body variants into one body style."
                        if en else
                        "C1：近义正文/风险/列表叙述并入同一正文 style，勿各造一种。"
                    ),
                },
                {
                    "into": "SecChapter",
                    "from_examples": ["SecHighlights*", "SecLetter*", "SecGovernance*", "SecAnnex*"],
                    "note": (
                        "C1: reuse one concrete main-body section leaf when page rhythm matches "
                        "(never merge into abstract SecMainBody / *Base)."
                        if en else
                        "C1：版式节奏相同的主文/函件/治理/附录节族可共用同一具体 section 叶子"
                        "（禁止并入 abstract 的 SecMainBody / *Base）。"
                    ),
                },
                {
                    "into": "ParaDataHeaderCell",
                    "from_examples": [
                        "ParaShareHeaderCell", "ParaStatementHeaderCell",
                        "ParaInfoLabelCell", "*HeaderCell",
                    ],
                    "note": (
                        "C1 cell-text: one shared header-cell paragraph across table families "
                        "(table chrome still differs via Tbl*)."
                        if en else
                        "C1 格内文字：跨表共用一套表头单元格段落（表框差异仍由 Tbl* 承担）。"
                    ),
                },
                {
                    "into": "ParaDataValueCell",
                    "from_examples": [
                        "ParaShareValueCell", "ParaStatementAmountCell",
                        "ParaStatementSubtotalCell", "ParaInfoValueCell", "*ValueCell", "*AmountCell",
                    ],
                    "note": (
                        "C1 cell-text: one shared data/value cell paragraph across grids."
                        if en else
                        "C1 格内文字：跨表共用一套数据/金额单元格段落。"
                    ),
                },
                {
                    "into": "ParaDataStubCell",
                    "from_examples": [
                        "ParaShareNameCell", "ParaStatementLineCell", "*StubCell", "*NameCell", "*LineCell",
                    ],
                    "note": (
                        "C1 cell-text: one shared stub/line-label cell paragraph."
                        if en else
                        "C1 格内文字：跨表共用一套行名/科目单元格段落。"
                    ),
                },
                {
                    "into": "ParaDataNoteCell",
                    "from_examples": ["ParaAnnexCell", "*NoteCell"],
                    "note": (
                        "C1 cell-text: fold note/annex cells into one note-cell paragraph."
                        if en else
                        "C1 格内文字：附注/附录单元格并入同一 note-cell 段落。"
                    ),
                },
            ],
            "omit_roles": [
                "ParaHeading4", "ParaHeading5", "ParaSubsubsectionHeading",
                "SecLandscape*",
                "*Base",
                "SecRoot", "SecFrontMatter", "SecMainBody", "SecBackMatter",
            ],
            "tier_rule": (
                "C1 ≈30 styles: strong section/paragraph(+cell-text) merges; "
                "keep purpose-distinct Tbl*/Img* near C2/C3 (same or −1); "
                "do NOT inflate by per-table Para*Cell families."
                if en else
                "C1 目标约 30 种：靠 section/正文/格内文字强合并；"
                "用途可区分的 Tbl*/Img* 与 C2/C3 近齐；"
                "禁止用「每表一套 Para*Cell」把总数撑到接近 C3。"
            ),
        }
    if cx == "C2":
        return {
            "keep_distinct": [],
            "merge_groups": [
                {
                    "into": "ParaNarrativeReview",
                    "from_examples": [
                        "ParaExecutiveHighlight", "ParaAnnexBody", "ParaLetterBody",
                        "ParaGovernanceDisclosure",
                    ],
                    "note": (
                        "C2 light merge: share one dominant body-prose style across near-narrative variants."
                        if en else
                        "C2 轻合并：近义叙述/亮点/附言正文并入同一主导正文 style。"
                    ),
                },
                {
                    "into": "SecChapter",
                    "from_examples": ["SecHighlights", "SecBusinessReview"],
                    "note": (
                        "C2: reuse one concrete main-body section leaf when page rhythm is the same "
                        "(never merge into abstract SecMainBody / *Base)."
                        if en else
                        "C2：版式节奏相同的主文节族可共用同一具体 section 叶子"
                        "（禁止并入 abstract 的 SecMainBody / *Base）。"
                    ),
                },
                {
                    "into": "ParaNoteTableDataCell",
                    "from_examples": ["ParaShareTableDataCell", "ParaAnnexTableDataCell"],
                    "note": (
                        "C2: share generic numeric/data cell treatment across similar grids."
                        if en else
                        "C2：版式相近的表数据单元格可共用。"
                    ),
                },
                {
                    "into": "ParaNoteTableHeaderCell",
                    "from_examples": ["ParaShareTableHeaderCell", "ParaAnnexTableHeaderCell"],
                    "note": (
                        "C2: share header-cell treatment when the grid system matches."
                        if en else
                        "C2：网格系统相同时表头单元格可共用。"
                    ),
                },
                {
                    "into": "ParaDataHeaderCell",
                    "from_examples": ["ParaShareHeaderCell", "ParaStatementHeaderCell"],
                    "note": (
                        "C2 light cell-text merge: share header-cell paragraphs across similar tables."
                        if en else
                        "C2 轻合并格内文字：相近表的表头单元格段落可共用。"
                    ),
                },
                {
                    "into": "ParaDataValueCell",
                    "from_examples": [
                        "ParaShareValueCell", "ParaStatementAmountCell", "ParaStatementSubtotalCell",
                    ],
                    "note": (
                        "C2 light cell-text merge: share data/amount cell paragraphs."
                        if en else
                        "C2 轻合并格内文字：数据/金额单元格段落可共用。"
                    ),
                },
            ],
            "omit_roles": [
                "ParaHeading5",
                "*Base",
                "ParaBodyBase", "ParaMainBodyBase", "ParaFrontMatterBase", "ParaBackMatterBase",
                "ParaTableCellBase",
                "ParaFrontMatterTableCellBase", "ParaMainBodyTableCellBase", "ParaBackMatterTableCellBase",
                "SecRoot", "SecFrontMatter", "SecMainBody", "SecBackMatter",
                "ParaStatementNoteCell",
            ],
            "tier_rule": (
                "C2 ≈40 styles: light but real merges + omit niche/base roles; "
                "must leave clear headroom below C3 (~50). keep_distinct is a short anchor list only."
                if en else
                "C2 目标约 40 种 style：轻合并须真实生效，并 omit 冷门/抽象 base；"
                "必须明显低于 C3（约 50）。keep_distinct 只是短锚点列表，禁止写成近满库清单。"
            ),
        }
    return {
        "keep_distinct": [],
        "merge_groups": [],
        "omit_roles": [
            "*Base",
            "ParaBodyBase", "ParaMainBodyBase", "ParaFrontMatterBase", "ParaBackMatterBase",
            "ParaTableCellBase",
            "ParaFrontMatterTableCellBase", "ParaMainBodyTableCellBase", "ParaBackMatterTableCellBase",
            "SecRoot", "SecFrontMatter", "SecMainBody", "SecBackMatter",
        ],
        "tier_rule": (
            "C3 ≈50 styles: keep library distinctions; minimize merges; widest practical stylesheet ≤ inventory."
            if en else
            "C3 目标约 50 种 style：尽量保留库内可区分项；少合并；最宽但仍不超库。"
        ),
    }


def _role_token_match(role: str, pattern: str) -> bool:
    """Exact or simple * wildcard match (prefix/suffix/contains)."""
    r = str(role or "").strip()
    p = str(pattern or "").strip()
    if not r or not p:
        return False
    if r == p:
        return True
    if "*" not in p:
        return False
    return fnmatch.fnmatchcase(r, p)


def _keep_max_for_tier(complexity: str, target_count: int) -> int:
    """keep_distinct is anchors only — well below post-merge stylesheet target."""
    cx = str(complexity or "C2").strip().upper()
    t = max(8, int(target_count or 40))
    if cx == "C1":
        return min(10, max(6, t // 3))
    if cx == "C2":
        return min(14, max(8, t // 3))
    return min(20, max(10, t // 2))


def sanitize_stylesheet_plan(
    plan: dict[str, Any] | None,
    *,
    complexity: str,
    target_count: int,
    locale: str = "zh",
) -> dict[str, Any]:
    """Normalize plan: resolve keep/merge/omit conflicts; cap keep_distinct; ensure C1/C2 merges."""
    cx = str(complexity or "C2").strip().upper()
    if cx not in ("C1", "C2", "C3"):
        cx = "C2"
    base = _default_stylesheet_plan(cx, locale=locale)
    out: dict[str, Any] = dict(base)
    if isinstance(plan, dict):
        for k in ("keep_distinct", "omit_roles", "merge_groups", "tier_rule"):
            if k in plan and plan[k] is not None:
                out[k] = plan[k]

    # Deduplicate list fields
    def _dedupe(seq: Any) -> list[str]:
        seen: set[str] = set()
        out_l: list[str] = []
        for x in seq or []:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out_l.append(s)
        return out_l

    keep = _dedupe(out.get("keep_distinct"))
    omit = _dedupe(out.get("omit_roles"))
    groups_raw = out.get("merge_groups") if isinstance(out.get("merge_groups"), list) else []
    groups: list[dict[str, Any]] = []
    for g in groups_raw:
        if not isinstance(g, dict):
            continue
        into = str(g.get("into") or "").strip()
        if not into:
            continue
        from_ex = _dedupe(g.get("from_examples") or g.get("from") or [])
        # Do not merge a role into itself as the only action
        from_ex = [x for x in from_ex if x != into]
        if not from_ex:
            continue
        groups.append({
            "into": into,
            "from_examples": from_ex,
            "note": str(g.get("note") or "").strip(),
        })

    # Never merge into an abstract base (would make *Base / SecMainBody a Word style)
    groups = [g for g in groups if not is_abstract_role_id(str(g.get("into") or ""))]

    # C1/C2 must have concrete merge_groups; if LLM left empty/weak, fall back to defaults
    if cx in ("C1", "C2") and len(groups) < (2 if cx == "C2" else 1):
        groups = [
            g for g in (base.get("merge_groups") or [])
            if isinstance(g, dict) and not is_abstract_role_id(str(g.get("into") or ""))
        ]

    # Ensure abstract bases stay omitted on all tiers
    for extra in base.get("omit_roles") or []:
        if extra not in omit:
            omit.append(extra)
    for extra in ("*Base", "SecRoot", "SecFrontMatter", "SecMainBody", "SecBackMatter"):
        if extra not in omit:
            omit.append(extra)

    banned = set(omit)
    for g in groups:
        for x in g.get("from_examples") or []:
            banned.add(str(x).strip())

    keep = [r for r in keep if not is_abstract_role_id(r)]

    filtered_keep: list[str] = []
    for role in keep:
        if any(_role_token_match(role, pat) for pat in banned):
            continue
        # Also drop if role is listed as a merge source via wildcard against role
        filtered_keep.append(role)

    max_keep = _keep_max_for_tier(cx, target_count)
    if len(filtered_keep) > max_keep:
        # Prefer structural anchors: headings / cover / primary tables first
        def _keep_rank(rid: str) -> tuple[int, str]:
            r = rid.lower()
            if "heading" in r or r.endswith("title"):
                return (0, rid)
            if r.startswith("sec"):
                return (1, rid)
            if r.startswith("tbl") or r.startswith("img"):
                return (2, rid)
            return (3, rid)
        filtered_keep = sorted(filtered_keep, key=_keep_rank)[:max_keep]

    # into targets of merges should stay usable (not omitted)
    for g in groups:
        into = g["into"]
        omit = [x for x in omit if not _role_token_match(into, x) and x != into]

    out["keep_distinct"] = filtered_keep
    out["omit_roles"] = omit
    out["merge_groups"] = groups
    if not str(out.get("tier_rule") or "").strip():
        out["tier_rule"] = str(base.get("tier_rule") or "")
    # Annotate when we had to shrink keep_distinct
    if len(keep) > len(filtered_keep):
        note = (
            f"keep_distinct capped to {len(filtered_keep)} anchors (was {len(keep)}); "
            f"target stylesheet ≈{target_count}."
            if str(locale).lower().startswith("en") else
            f"keep_distinct 已裁为 {len(filtered_keep)} 个锚点（原 {len(keep)}）；"
            f"stylesheet 目标约 {target_count}。"
        )
        prev = str(out.get("tier_rule") or "").strip()
        out["tier_rule"] = f"{prev} | {note}".strip(" |")
    return out


def _parse_stylesheet_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    src = raw.get("stylesheet_plan")
    if not isinstance(src, dict):
        return {}
    out: dict[str, Any] = {}
    keep = src.get("keep_distinct")
    if isinstance(keep, list):
        out["keep_distinct"] = [str(x).strip() for x in keep if str(x).strip()]
    omit = src.get("omit_roles")
    if isinstance(omit, list):
        out["omit_roles"] = [str(x).strip() for x in omit if str(x).strip()]
    if isinstance(src.get("tier_rule"), str) and src["tier_rule"].strip():
        out["tier_rule"] = src["tier_rule"].strip()
    groups = src.get("merge_groups")
    if isinstance(groups, list):
        parsed = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            into = str(g.get("into") or "").strip()
            if not into:
                continue
            from_ex = g.get("from_examples") or g.get("from") or []
            if not isinstance(from_ex, list):
                from_ex = [from_ex]
            parsed.append({
                "into": into,
                "from_examples": [str(x).strip() for x in from_ex if str(x).strip()],
                "note": str(g.get("note") or "").strip(),
            })
        if parsed:
            out["merge_groups"] = parsed
    return out


def brief_for_complexity(
    complexity: str,
    *,
    theme: str = "",
    writing: str = "",
    genre: str = "",
    locale: str = "zh",
) -> dict[str, Any]:
    """Fallback slim budget for a C tier.

    Recommend fields (section_count / heading_depth / stylesheet_role_count) are
    step2 writing targets and C1<C2<C3 planning order — not step3 fail gates.
    ``stylesheet_plan`` (merge/omit/keep) is the primary actuator for step3.
    """
    cx = str(complexity or "").strip().upper()
    if cx not in ("C1", "C2", "C3"):
        cx = "C3" if cx.startswith("C") else "C2"
    en = str(locale).lower().startswith("en")
    rec = _recommend_defaults(cx)
    guide = _RECOMMEND_GUIDE[cx]
    plan = _default_stylesheet_plan(cx, locale=locale)
    if cx == "C1":
        role_policy = (
            "C1: consolidate near-duplicate roles via stylesheet_plan.merge_groups on "
            "section/paragraph; omit niche heading/landscape. "
            "Keep purpose-distinct table/image families near-parity with C2/C3."
            if en else
            "C1：按 stylesheet_plan.merge_groups 强合并 section/paragraph；"
            "省略冷门标题/横向页。表/图用途可区分家族与 C2/C3 近齐。"
        )
    elif cx == "C2":
        role_policy = (
            "C2: keep more paragraph distinctions than C1; only light merges. "
            "Table/image ≈ C3 (same or −1)."
            if en else
            "C2：比 C1 保留更多 paragraph 区分；仅轻度合并。表/图≈C3（同档或少 1）。"
        )
    else:
        role_policy = (
            "C3: preserve library distinctions; minimize merges; widest practical stylesheet. "
            "Table/image at purpose-distinct library ceiling."
            if en else
            "C3：保留库内可区分样式；少合并；stylesheet 尽量丰富但不超库。"
            "表/图按用途可区分家族尽量顶满库。"
        )
    notes = {
        "section_count": (
            f"About {rec['section_count']} top-level parts for this theme "
            f"(more parts = more peer chapters when the theme supports them, not a thinner tree)."
            if en else
            f"本主题约 {rec['section_count']} 个一级章节"
            f"（节数升高宜多开并列章，而不是把同级内容收成单链）。"
        ),
        "heading_depth": (
            f"Suggested max heading depth {rec['heading_depth']} "
            f"(deeper levels still need peer siblings; do not use depth to replace body slots)."
            if en else
            f"建议最大标题深度 {rec['heading_depth']}"
            f"（更深一层仍须同级并列；勿用加深替代正文槽位）。"
        ),
        "stylesheet_role_count": (
            f"Target ~{rec['stylesheet_role_count']} distinct stylesheet styles AFTER merges "
            f"(step3 must obey stylesheet_plan, not just this number)."
            if en else
            f"合并归类后 stylesheet 目标约 {rec['stylesheet_role_count']} 种；"
            f"step3 必须执行 stylesheet_plan，不能只看数字。"
        ),
    }
    return {
        "complexity": cx,
        "recommend": rec,
        "recommend_notes": notes,
        "recommend_guide": {
            **{k: {"lo": guide[k][0], "hi": guide[k][1]} for k in _ORDER_KEYS},
        },
        "stylesheet_by_category": _default_category_suggest(cx, locale=locale),
        "stylesheet_plan": plan,
        "role_selection_policy": role_policy,
        "rationale": (
            f"{cx}: differentiate mainly via stylesheet_plan merge/omit intensity + "
            f"section_count/heading_depth; no outline-block quota."
        ),
        "source": "complexity_recommend_preset",
    }


def normalize_brief_for_complexity(brief: dict[str, Any] | None, complexity: str, **kwargs) -> dict[str, Any]:
    """Merge LLM recommend + notes + per-class suggestions. Soft clamp only for absurd outliers."""
    base = brief_for_complexity(complexity, **kwargs)
    out = dict(base)
    locale = str(kwargs.get("locale") or "zh")
    parsed = _parse_recommend(brief) if isinstance(brief, dict) else {}
    rec = dict(out.get("recommend") or {})
    for k, v in parsed.items():
        rec[k] = v

    cx = str(out.get("complexity") or "C2")
    guide = _RECOMMEND_GUIDE.get(cx, _RECOMMEND_GUIDE["C2"])
    for k in _ORDER_KEYS:
        lo, hi = guide[k]
        n = int(rec.get(k, _mid(lo, hi)))
        if k == "heading_depth":
            rec[k] = n if n > 0 else _mid(lo, hi)
        else:
            soft_lo = 2
            soft_hi = 12 if k == "section_count" else 64
            rec[k] = max(soft_lo, min(soft_hi, n))
    out["recommend"] = rec

    notes = dict(out.get("recommend_notes") or {})
    notes.update(_parse_recommend_notes(brief) if isinstance(brief, dict) else {})
    # Drop legacy block-quota notes (no longer a C-tier dial)
    notes.pop("outline_block_count", None)
    # Ensure every order key has an explanation for step3
    for k in _ORDER_KEYS:
        if k not in notes or not str(notes.get(k) or "").strip():
            notes[k] = str((base.get("recommend_notes") or {}).get(k) or f"{k}={rec.get(k)}")
    out["recommend_notes"] = notes
    rg = dict(out.get("recommend_guide") or {})
    rg.pop("outline_block_count", None)
    for k in _ORDER_KEYS:
        if k not in rg:
            lo, hi = guide[k]
            rg[k] = {"lo": lo, "hi": hi}
    out["recommend_guide"] = rg

    cats = _default_category_suggest(cx, locale=locale)
    parsed_cats = _parse_stylesheet_by_category(brief) if isinstance(brief, dict) else {}
    for cat, val in parsed_cats.items():
        cats[cat] = val
    # section / paragraph: never keep numeric quotas — merge intensity via note only
    for cat in ("section", "paragraph"):
        if cat in cats and isinstance(cats[cat], dict):
            note = str(cats[cat].get("note") or "").strip()
            if not note:
                note = str((_default_category_suggest(cx, locale=locale).get(cat) or {}).get("note") or "")
            cats[cat] = {"suggest": None, "note": note}
    out["stylesheet_by_category"] = cats

    plan = _default_stylesheet_plan(cx, locale=locale)
    parsed_plan = _parse_stylesheet_plan(brief) if isinstance(brief, dict) else {}
    for k, v in parsed_plan.items():
        plan[k] = v
    out["stylesheet_plan"] = sanitize_stylesheet_plan(
        plan,
        complexity=cx,
        target_count=int(rec.get("stylesheet_role_count") or _mid(*guide["stylesheet_role_count"])),
        locale=locale,
    )

    if isinstance(brief, dict) and brief:
        for key in ("role_selection_policy", "rationale", "hierarchy_pattern", "leaf_policy"):
            val = brief.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()
        out["source"] = "complexity_recommend+llm"

    # Drop fat / retired legacy keys from persisted brief when normalizing
    for dead in ("paragraph_count", "table_count", "figure_count", "outline_block_count"):
        out.pop(dead, None)
        if isinstance(out.get("recommend"), dict):
            out["recommend"].pop(dead, None)
        if isinstance(out.get("recommend_notes"), dict):
            out["recommend_notes"].pop(dead, None)
        if isinstance(out.get("recommend_guide"), dict):
            out["recommend_guide"].pop(dead, None)

    out["heading_depth_target"] = rec["heading_depth"]
    out["heading_depth_min"] = max(2, rec["heading_depth"] - 1)
    out["branching"] = {
        "h1_count": str(rec["section_count"]),
        "require_some_h4": rec["heading_depth"] >= 4,
        "h4_note": "topic-driven" if rec["heading_depth"] >= 4 else "optional",
    }
    if kwargs.get("writing"):
        out["writing_echo"] = str(kwargs["writing"])[:240]
    return clamp_structure_brief_for_token_budget(out)


def brief_complexity_score(brief: dict[str, Any]) -> tuple[int, ...]:
    """Sortable score: (section_count, heading_depth, stylesheet_role_count)."""
    if isinstance(brief.get("recommend"), dict):
        block = brief["recommend"]
        return tuple(int(block.get(k) or 0) for k in _ORDER_KEYS)
    rec = _parse_recommend(brief)
    return tuple(int(rec.get(k) or 0) for k in _ORDER_KEYS)


def validate_complexity_brief_order(
    briefs_by_cx: dict[str, dict[str, Any]],
) -> list[str]:
    """Hard-check C1 ≤ C2 ≤ C3 on (section, depth, stylesheet_role_count).

    Equal neighbors are OK (small libraries need not be forced apart);
    only inversions fail; C1 must be strictly less than C3 overall.
    """
    errs: list[str] = []
    present = [cx for cx in ("C1", "C2", "C3") if cx in briefs_by_cx and briefs_by_cx[cx]]
    if len(present) < 2:
        return errs
    scores = {cx: brief_complexity_score(briefs_by_cx[cx]) for cx in present}
    pairs = [(a, b) for a, b in (("C1", "C2"), ("C2", "C3"), ("C1", "C3")) if a in scores and b in scores]
    for a, b in pairs:
        if scores[a] > scores[b]:
            errs.append(
                f"structure_brief 顺序错误: {a}{scores[a]} > {b}{scores[b]} "
                f"（期望 section/depth/stylesheet_role_count 随 C1→C3 非降；"
                f"富库时相邻档 stylesheet 宜有可见差距）"
            )
    if "C1" in scores and "C3" in scores and scores["C1"] >= scores["C3"]:
        errs.append(
            f"structure_brief 缺少递进: C1{scores['C1']} 未严格小于 C3{scores['C3']}"
        )
    return errs


def default_structure_brief(
    *,
    theme: str = "",
    writing: str = "",
    genre: str = "",
) -> dict[str, Any]:
    g = f"{genre} {theme} {writing}".lower()
    if any(k in g for k in ("标书", "投标", "技术方案", "系统设计", "学位论文", "rfp", "proposal")):
        cx = "C3"
    elif any(k in g for k in ("函", "通知", "简报", "memo", "letter")):
        cx = "C1"
    else:
        cx = "C2"
    return brief_for_complexity(cx, theme=theme, writing=writing, genre=genre)


def clamp_structure_brief_for_token_budget(brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(brief, dict):
        return brief
    out = dict(brief)
    rec = dict(out.get("recommend") or _parse_recommend(out) or _recommend_defaults("C2"))
    # Keep only order keys
    rec = {k: int(rec.get(k) or 0) for k in _ORDER_KEYS}
    if rec.get("section_count", 0) > 12:
        rec["section_count"] = 12
    if rec.get("stylesheet_role_count", 0) > 64:
        rec["stylesheet_role_count"] = 64
    out["recommend"] = rec
    out["heading_depth_target"] = rec.get("heading_depth", 3)
    out["heading_depth_min"] = max(2, int(rec.get("heading_depth") or 3) - 1)
    out["output_budget"] = (
        "单次须输出完整合法 JSON；以 recommend + recommend_notes + stylesheet_by_category 为软指引；"
        "section/paragraph 看合并强度 note（suggest 为空），勿按类配额硬凑；"
        "stylesheet 总种数对齐 recommend.stylesheet_role_count；"
        "keep_distinct 仅为短锚点（禁止近满库清单），必须执行 merge_groups/omit_roles；"
        "标题深度跟主题与 heading_depth。"
    )
    # Re-cap keep_distinct if clamp changed the style target
    if isinstance(out.get("stylesheet_plan"), dict):
        out["stylesheet_plan"] = sanitize_stylesheet_plan(
            out.get("stylesheet_plan"),
            complexity=str(out.get("complexity") or "C2"),
            target_count=int(rec.get("stylesheet_role_count") or 40),
            locale="zh",
        )
    out["clamped"] = True
    return out


def headings_snippet_from_library(library_prompt: str, *, max_chars: int = 4000) -> str:
    lines = library_prompt.splitlines()
    keep: list[str] = []
    grab = False
    for ln in lines:
        if re.search(r"Heading|标题|outlineLvl|hierarchy|层级", ln, re.I):
            grab = True
        if grab:
            keep.append(ln)
            if len("\n".join(keep)) > max_chars:
                break
            if ln.strip() == "" and len(keep) > 20:
                grab = False
    if not keep:
        return library_prompt[:max_chars]
    return "\n".join(keep)[:max_chars]


def max_heading_depth(plan: dict[str, Any], *, library: dict[str, Any] | None = None) -> int:
    return int(collect_heading_hits(plan, library=library).get("depth") or 0)


def count_heading_roles(plan: dict[str, Any], *, library: dict[str, Any] | None = None) -> dict[str, int]:
    return dict(collect_heading_hits(plan, library=library).get("counts") or {})


def _count_plan_blocks(
    plan: dict[str, Any], *, library: dict[str, Any] | None = None
) -> dict[str, int]:
    sections = len(plan.get("outline") or []) or len(plan.get("sections") or [])
    style_ids: set[str] = set()
    for o in plan.get("outline") or []:
        for b in o.get("blocks") or []:
            rid = str(b.get("role_id") or "")
            if rid:
                style_ids.add(rid)
    ss = plan.get("stylesheet") or {}
    if isinstance(ss, list):
        for item in ss:
            if isinstance(item, dict):
                rid = item.get("style_id") or item.get("role_id") or item.get("id")
                if rid:
                    style_ids.add(str(rid))
    elif isinstance(ss, dict):
        for bucket in ss.values():
            if isinstance(bucket, list):
                for item in bucket:
                    if isinstance(item, dict):
                        rid = item.get("style_id") or item.get("role_id") or item.get("id")
                        if rid:
                            style_ids.add(str(rid))
            elif isinstance(bucket, dict):
                for rid in bucket:
                    style_ids.add(str(rid))
    return {
        "section_count": sections,
        "heading_depth": max_heading_depth(plan, library=library),
        "stylesheet_role_count": len(style_ids),
    }


def measure_plan_against_brief(
    plan: dict[str, Any],
    brief: dict[str, Any],
    *,
    library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record measured vs step2 recommend. All counts are advisory (non-fatal).

    ``heading_depth`` / ``section_count`` / ``stylesheet_role_count`` are writing
    expectations from step2, not gates. Outline block totals are recorded only
    (no per-tier quota). ``stylesheet_role_count`` far above recommend is noted
    (merge/omit may not have landed) but still not a fail.
    """
    rec = {}
    if brief:
        rec = brief.get("recommend") if isinstance(brief.get("recommend"), dict) else _parse_recommend(brief)
        rec = dict(rec or {})
    got = _count_plan_blocks(plan, library=library)
    hits = collect_heading_hits(plan, library=library)
    got["heading_depth"] = int(hits.get("depth") or 0)
    n_blocks = sum(len(o.get("blocks") or []) for o in (plan.get("outline") or []))
    notes: list[str] = []
    compared: dict[str, Any] = {}
    for key in ("heading_depth", "section_count", "stylesheet_role_count"):
        compared[key] = {
            "measured": int(got.get(key) or 0),
            "recommend": int(rec.get(key) or 0),
            "compared": False,
            "role": "step2_recommend",
        }
    want_styles = int(rec.get("stylesheet_role_count") or 0)
    have_styles = int(got.get("stylesheet_role_count") or 0)
    if want_styles and have_styles > int(want_styles * 1.25):
        notes.append(
            f"stylesheet/role 覆盖={have_styles}，step2 建议约 {want_styles} "
            f"（仅提示 merge/omit 可能未落地，不失败）"
        )
    return {
        "recommend": rec,
        "measured": {
            "heading_depth": got["heading_depth"],
            "section_count": got["section_count"],
            "stylesheet_role_count": got["stylesheet_role_count"],
            "outline_block_count": n_blocks,
        },
        "heading": hits,
        "compared": compared,
        "outline_block_count": {
            "measured": n_blocks,
            "note": "实测块数，无档位配额、不校验",
        },
        "warnings": [],
        "notes": notes,
    }


def validate_plan_against_brief(plan: dict[str, Any], brief: dict[str, Any]) -> list[str]:
    """Kept for callers; heading/block/section counts are no longer warnings."""
    return list(measure_plan_against_brief(plan, brief).get("notes") or [])


def format_brief_check_md(report: dict[str, Any]) -> str:
    rec = report.get("recommend") or {}
    meas = report.get("measured") or {}
    hits = report.get("heading") or {}
    counts = hits.get("counts") or {}
    blk = report.get("outline_block_count") or {}
    lines = [
        "# Brief 对照（step2 建议 vs 大纲实测）",
        "",
        "`heading_depth` / `section_count` / `stylesheet_role_count` 是 step2 的写作预期，**不作为失败条件**。",
        "大纲块数仅记录实测，**无按档配额**。stylesheet 远高于建议时下面会记一条提示（merge/omit）。",
        "",
        "## 实测",
        "",
        f"- **标题深度** = {meas.get('heading_depth')}（step2 建议 {rec.get('heading_depth')}）",
    ]
    bits = [f"H{i}×{counts.get(f'h{i}', 0)}" for i in range(1, 6) if counts.get(f"h{i}")]
    if bits:
        lines.append(f"  - 分层计数：{' '.join(bits)}")
    sig = hits.get("signals_used") or []
    if sig:
        lines.append(f"  - 用到的信号：{', '.join(sig)}")
    by_role = hits.get("by_role") or {}
    if by_role:
        lines.append("  - 命中角色：")
        for rid, info in sorted(
            by_role.items(),
            key=lambda kv: (kv[1].get("level") is None, kv[1].get("level") or 9, kv[0]),
        ):
            dn = info.get("display_name") or rid
            depth_tag = "计入深度" if info.get("counts_toward_depth") else "不计入深度（封面/目录题等）"
            src = info.get("sources_seen") or {}
            src_s = ", ".join(f"{k}→H{v}" for k, v in src.items()) if src else ""
            extra = f"；{src_s}" if src_s else ""
            lvl = info.get("level")
            lvl_s = f"H{lvl}" if lvl else "未分级"
            lines.append(f"    - {lvl_s} `{rid}`（{dn}）×{info.get('count')} — {depth_tag}{extra}")
    elif not meas.get("heading_depth"):
        lines.append("  - 未识别到正文标题（对照 outline_tree / 编号 / stylesheet 描述）")
    lines.append(f"- **一级章节数** = {meas.get('section_count')}（step2 建议 {rec.get('section_count')}）")
    lines.append(
        f"- **stylesheet/role 覆盖** = {meas.get('stylesheet_role_count')}"
        f"（step2 建议 {rec.get('stylesheet_role_count')}）"
    )
    lines.append(
        f"- **outline blocks** = {blk.get('measured', meas.get('outline_block_count'))}"
        f"（仅记录，无配额）"
    )
    lines += ["", "## 深度怎么确认", "", str(hits.get("method") or ""), ""]
    notes = report.get("notes") or []
    lines += ["## 提示", ""]
    if notes:
        lines.extend(f"- {n}" for n in notes)
    else:
        lines.append("（无）")
    lines.append("")
    return "\n".join(lines)


def write_brief_check(
    plan: dict[str, Any],
    brief: dict[str, Any],
    out_dir: Path,
    *,
    library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write brief_check.json + brief_check.md next to plan.json."""
    report = measure_plan_against_brief(plan, brief, library=library)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brief_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "brief_check.md").write_text(format_brief_check_md(report), encoding="utf-8")
    return report


def brief_to_prompt_block(brief: dict[str, Any]) -> str:
    return json.dumps(brief, ensure_ascii=False, indent=2)


def load_brief(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


STRUCTURE_BRIEFS_FILENAME = "structure_briefs.json"


def structure_briefs_path(topic_dir: Path) -> Path:
    """Resolve structure_briefs.json under step2_themes (or legacy topic root)."""
    p = Path(topic_dir)
    # Already a themes dir
    if p.name in ("step2_themes", "step2") or (p / "themes.xlsx").is_file():
        return p / STRUCTURE_BRIEFS_FILENAME
    modern = p / "step2_themes" / STRUCTURE_BRIEFS_FILENAME
    if modern.is_file():
        return modern
    for alt in (p / STRUCTURE_BRIEFS_FILENAME, p / "step2" / STRUCTURE_BRIEFS_FILENAME):
        if alt.is_file():
            return alt
    return modern


def write_structure_briefs(
    topic_dir: Path,
    briefs_by_cx: dict[str, dict[str, Any]],
    *,
    theme_meta: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Write C1/C2/C3 budgets next to themes.xlsx (``step2_themes/structure_briefs.json``)."""
    by_cx: dict[str, Any] = {}
    by_tid: dict[str, Any] = {}
    for cx in ("C1", "C2", "C3"):
        brief = briefs_by_cx.get(cx)
        if not isinstance(brief, dict) or not brief:
            continue
        entry = dict(brief)
        entry["complexity"] = cx
        meta = (theme_meta or {}).get(cx) or {}
        if meta.get("theme_id") is not None:
            entry["theme_id"] = meta["theme_id"]
        if meta.get("theme"):
            entry["theme"] = meta["theme"]
        by_cx[cx] = entry
        tid = entry.get("theme_id")
        if tid is not None:
            by_tid[str(tid)] = entry
    payload = {
        "version": 2,
        "description": (
            "Step2 writing targets for step3: recommend counts are advisory "
            "(C1<C2<C3 planning order). stylesheet_plan (merge/omit/keep) is the actuator."
        ),
        "order_keys": ["section_count", "heading_depth", "stylesheet_role_count"],
        "by_complexity": by_cx,
        "by_theme_id": by_tid,
    }
    path = structure_briefs_path(topic_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_structure_briefs(topic_dir: Path) -> dict[str, dict[str, Any]]:
    """Load ``by_complexity`` map from topic ``structure_briefs.json`` (empty if missing)."""
    path = structure_briefs_path(topic_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    by_cx = data.get("by_complexity")
    if isinstance(by_cx, dict) and by_cx:
        return {str(k).upper(): v for k, v in by_cx.items() if isinstance(v, dict)}
    out = {}
    for k, v in data.items():
        ku = str(k).upper()
        if ku in ("C1", "C2", "C3") and isinstance(v, dict):
            out[ku] = v
    return out
