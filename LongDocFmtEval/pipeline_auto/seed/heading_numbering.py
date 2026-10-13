"""Rule-based heading text numbering for SecMainBody paragraph headings."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR
from LongDocFmtEval.pipeline_auto.seed.heading_contract import (
    heading_index,
    is_paragraph_heading_id,
    parse_heading_level,
    parse_title_kind,
)

SCHEMES_PATH = CONFIG_DIR / "heading_schemes.yaml"

_FRONT_RE = re.compile(
    r"(Cover|Toc|TOC|FrontMatter|FormalHead|TableOfContents|目录|封面|文头|索引)",
    re.I,
)
_BACK_RE = re.compile(
    r"(Appendix|Annex|BackMatter|附表|附图|附录)",
    re.I,
)
_FRONT_ROLES = frozenset({
    "SecCover", "SecTOC", "SecToC", "SecToc", "SecTableOfContents", "SecFrontMatter",
})
_BACK_ROLES = frozenset({
    "SecAppendix", "SecAnnex", "SecBackMatter", "SecNotes",
    "SecAppendixTables", "SecAppendixFigures",
})

_CN_DIGITS = "零一二三四五六七八九"


def load_schemes(path: Path | None = None) -> dict[str, Any]:
    p = path or SCHEMES_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return (raw or {}).get("schemes") or {}


def section_zone(role_id: str, title: str = "") -> str:
    rid = str(role_id or "")
    tit = str(title or "")
    if rid in _FRONT_ROLES or _FRONT_RE.search(rid) or _FRONT_RE.search(tit):
        return "front"
    if rid in _BACK_ROLES or _BACK_RE.search(rid) or _BACK_RE.search(tit):
        return "back"
    return "main"


def to_chinese_numeral(n: int) -> str:
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" if n == 10 else "十" + _CN_DIGITS[n % 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        s = _CN_DIGITS[tens] + "十"
        if ones:
            s += _CN_DIGITS[ones]
        return s
    return str(n)


def to_roman(n: int, upper: bool = True) -> str:
    if n <= 0:
        return str(n)
    vals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out: list[str] = []
    x = n
    for v, sym in vals:
        while x >= v:
            out.append(sym)
            x -= v
    s = "".join(out)
    return s if upper else s.lower()


def to_alpha(n: int, upper: bool = True) -> str:
    if n <= 0:
        return str(n)
    n0 = ((n - 1) % 26) + 1
    ch = chr(ord("A" if upper else "a") + n0 - 1)
    return ch


def to_cn_lower(n: int) -> str:
    if 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    return f"({n})"


def _counter_names(level: int) -> list[str]:
    return ["n", "m", "p", "q", "r", "s", "t"][:level]


def format_prefix(
    scheme: dict[str, Any],
    level: int,
    counters: list[int],
) -> str:
    levels = scheme.get("levels") or {}
    spec = levels.get(level) or levels.get(str(level))
    if not spec:
        return ""
    template = str(spec.get("template") or "")
    sep = str(spec.get("separator", " "))
    names = _counter_names(level)
    ctx: dict[str, Any] = {}
    for i, name in enumerate(names):
        val = counters[i] if i < len(counters) else 1
        ctx[name] = val
        ctx[name.upper()] = val
    # {CN}/{ROMAN}/{ALPHA}/… bind to the *current* heading level counter so
    # e.g. L2 （一）（二） resets under each new L1 一、二、.
    leaf = int(counters[level - 1]) if len(counters) >= level else 1
    root = int(counters[0]) if counters else 1
    ctx["CN"] = to_chinese_numeral(leaf)
    ctx["CN_ROOT"] = to_chinese_numeral(root)
    ctx["ROMAN"] = to_roman(leaf, upper=True)
    ctx["roman"] = to_roman(leaf, upper=False)
    ctx["ALPHA"] = to_alpha(leaf, upper=True)
    ctx["alpha"] = to_alpha(leaf, upper=False)
    ctx["CN_LOWER"] = to_cn_lower(leaf)
    try:
        body = template.format(**ctx)
    except (KeyError, ValueError):
        body = template
    return f"{body}{sep}" if sep else body


def build_section_map(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for sec in plan.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        iid = str(sec.get("instance_id") or "")
        if not iid:
            continue
        rid = str(sec.get("role_id") or "")
        title = str(sec.get("title") or "")
        out[iid] = {
            "role_id": rid,
            "title": title,
            "zone": section_zone(rid, title),
        }
    return out


def iter_heading_blocks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Document-order heading blocks in main zone with heading_level."""
    idx = heading_index(plan)
    sec_map = build_section_map(plan)
    out: list[dict[str, Any]] = []
    for entry in plan.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        iid = str(entry.get("instance_id") or "")
        zone = (sec_map.get(iid) or {}).get("zone", "main")
        if zone != "main":
            continue
        for b in entry.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("block_id") or "")
            rid = str(b.get("role_id") or "")
            rec = idx.get(rid) or {}
            if parse_title_kind(rec.get("title_kind")):
                continue
            hl = parse_heading_level(rec.get("heading_level"))
            if hl is None and is_paragraph_heading_id(rid, rec):
                from LongDocFmtEval.pipeline_auto.seed.heading_detect import (  # noqa: PLC0415
                    heading_level as detect_hl,
                )
                hl = detect_hl(rid, rec.get("display_name") or rid)
            if not hl:
                continue
            out.append({
                "block_id": bid,
                "role_id": rid,
                "heading_level": int(hl),
                "instance_id": iid,
                "zone": zone,
            })
    return out


def compute_numbering_map(
    plan: dict[str, Any],
    scheme_id: str,
    *,
    schemes: dict[str, Any] | None = None,
) -> dict[str, str]:
    """block_id → prefix string (no title body)."""
    schemes = schemes or load_schemes()
    scheme = schemes.get(scheme_id) or {}
    prefixes: dict[str, str] = {}
    counters = [0] * 7
    for item in iter_heading_blocks(plan):
        level = int(item["heading_level"])
        if level < 1 or level > 7:
            continue
        counters[level - 1] += 1
        for j in range(level, 7):
            counters[j] = 0
        active = counters[:level]
        pref = format_prefix(scheme, level, active)
        if pref:
            prefixes[str(item["block_id"])] = pref
    return prefixes


def compute_prefixes_for_heading_levels(
    levels: list[int],
    scheme_id: str,
    *,
    schemes: dict[str, Any] | None = None,
) -> list[str]:
    """Document-order heading levels (1..7) → prefix strings for one scheme."""
    schemes = schemes or load_schemes()
    scheme = schemes.get(scheme_id) or {}
    counters = [0] * 7
    out: list[str] = []
    for raw in levels:
        level = int(raw)
        if level < 1 or level > 7:
            out.append("")
            continue
        counters[level - 1] += 1
        for j in range(level, 7):
            counters[j] = 0
        active = counters[:level]
        out.append(format_prefix(scheme, level, active))
    return out


def apply_prefix_to_text(base_text: str, prefix: str) -> str:
    body = str(base_text or "").strip()
    if not prefix:
        return body
    return f"{prefix}{body}" if body else prefix.strip()


def _scheme_level_keys(scheme: dict[str, Any]) -> list[int]:
    levels = scheme.get("levels") or {}
    out: list[int] = []
    for key in levels:
        try:
            out.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def validate_schemes_automation(schemes: dict[str, Any] | None = None) -> list[str]:
    """Verify every scheme level renders non-empty prefixes with expected numeral forms."""
    schemes = schemes or load_schemes()
    errors: list[str] = []
    probe_counters = {
        1: [3],
        2: [2, 4],
        3: [1, 2, 3],
        4: [2, 1, 3, 2],
    }
    for scheme_id, scheme in sorted(schemes.items()):
        for level in _scheme_level_keys(scheme):
            spec = (scheme.get("levels") or {}).get(level) or (scheme.get("levels") or {}).get(str(level))
            if not spec:
                continue
            template = str(spec.get("template") or "")
            counters = probe_counters.get(level, [1] * level)
            pref = format_prefix(scheme, level, counters)
            if not str(pref).strip():
                errors.append(f"{scheme_id} L{level}: empty prefix for counters {counters}")
                continue
            leaf_val = int(counters[level - 1]) if len(counters) >= level else 1
            if "{CN}" in template and to_chinese_numeral(leaf_val) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{CN}} but prefix {pref!r} "
                    f"lacks {to_chinese_numeral(leaf_val)!r}"
                )
            if "{CN}" in template and level == 2:
                under_ch1 = format_prefix(scheme, level, [1, 1])
                under_ch2 = format_prefix(scheme, level, [2, 1])
                if under_ch1 != under_ch2:
                    errors.append(
                        f"{scheme_id} L2: expected reset under new parent "
                        f"({under_ch1!r} vs {under_ch2!r})"
                    )
                sibling = format_prefix(scheme, level, [1, 2])
                if under_ch1 == sibling:
                    errors.append(
                        f"{scheme_id} L2: expected sibling counters to differ "
                        f"({under_ch1!r} vs {sibling!r})"
                    )
            if "{ROMAN}" in template and to_roman(leaf_val, upper=True) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{ROMAN}} but prefix {pref!r} "
                    f"lacks {to_roman(leaf_val, upper=True)!r}"
                )
            if "{roman}" in template and to_roman(leaf_val, upper=False) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{roman}} but prefix {pref!r} "
                    f"lacks {to_roman(leaf_val, upper=False)!r}"
                )
            if "{ALPHA}" in template and to_alpha(leaf_val, upper=True) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{ALPHA}} but prefix {pref!r} "
                    f"lacks {to_alpha(leaf_val, upper=True)!r}"
                )
            if "{alpha}" in template and to_alpha(leaf_val, upper=False) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{alpha}} but prefix {pref!r} "
                    f"lacks {to_alpha(leaf_val, upper=False)!r}"
                )
            if "{CN_LOWER}" in template and to_cn_lower(leaf_val) not in pref:
                errors.append(
                    f"{scheme_id} L{level}: template has {{CN_LOWER}} but prefix {pref!r} "
                    f"lacks {to_cn_lower(leaf_val)!r}"
                )
    return errors


def targets_from_factors(factors: dict[str, Any]) -> dict[str, bool]:
    """Resolve document/template numbering flags from nested or legacy factors."""
    init = factors.get("init")
    if isinstance(init, dict):
        num = bool((init.get("numbering") or {}).get("enabled"))
        mode = str(factors.get("input_mode") or "")
        return {
            "document": num,
            "template": num and mode in ("template", "template_w_text"),
        }
    if "numbering_enabled" in factors:
        num = bool(factors.get("numbering_enabled"))
        mode = str(factors.get("input_mode") or "")
        return {
            "document": num,
            "template": num and mode in ("template", "template_w_text"),
        }
    if "document_numbered" in factors or "template_numbered" in factors:
        return {
            "document": bool(factors.get("document_numbered")),
            "template": bool(factors.get("template_numbered")),
        }
    profile = str(factors.get("numbering_profile") or "both_none")
    return targets_for_profile(profile)


def targets_for_profile(profile: str) -> dict[str, bool]:
    p = str(profile or "both_none")
    return {
        "document": p in ("both_numbered", "document_only"),
        "template": p in ("both_numbered", "template_only"),
    }


def apply_numbering_to_content_json(
    content: dict[str, Any],
    base_blocks: dict[str, Any],
    prefixes: dict[str, str],
    *,
    apply: bool,
) -> dict[str, Any]:
    out = copy.deepcopy(content)
    blocks = out.setdefault("blocks", {})
    if not isinstance(blocks, dict):
        return out
    for bid, base_val in (base_blocks or {}).items():
        if bid not in blocks and bid not in prefixes:
            continue
        if not isinstance(base_val, dict):
            continue
        base_text = str(base_val.get("text") or "")
        if "text" not in base_val:
            continue
        target = blocks.setdefault(bid, copy.deepcopy(base_val))
        if apply and bid in prefixes:
            target["text"] = apply_prefix_to_text(base_text, prefixes[bid])
        else:
            target["text"] = base_text
    return out


def apply_numbering_to_specimen_plan(
    specimen: dict[str, Any],
    base_outline: list[dict[str, Any]],
    prefixes: dict[str, str],
    *,
    apply: bool,
) -> dict[str, Any]:
    out = copy.deepcopy(specimen)
    base_by_id: dict[str, dict[str, Any]] = {}
    for entry in base_outline or []:
        for b in entry.get("blocks") or []:
            if isinstance(b, dict) and b.get("block_id"):
                base_by_id[str(b["block_id"])] = b
    for entry in out.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        for b in entry.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("block_id") or "")
            base_b = base_by_id.get(bid) or {}
            base_text = str(base_b.get("main_point") or b.get("main_point") or "")
            if apply and bid in prefixes:
                b["main_point"] = apply_prefix_to_text(base_text, prefixes[bid])
            else:
                b["main_point"] = base_text
    return out
