"""Role-library canonical model + normalization + validation.

Canonical (on-disk) encoding = **flat + parent**:
  each tree layer is a flat list of nodes; every node has `role_id` and `parent`
  (root nodes have parent: null). No nested `derives`.

This module accepts ANY of three input encodings (from the LLM or humans):
  1) nested objects   : node["derives"] = [child node dicts]
  2) flat + child ids : node["derives"] = ["ChildId", ...]
  3) flat + parent    : node["parent"]  = "ParentId" | null
and normalizes them to the canonical flat+parent form. It can also rebuild the
nested forest for visualization / consumption.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

# Known format_focus intent tags (snake_case / flags). These are NOT officecli
# camelCase keys — step4 maps them via officecli_whitelist.yaml.
# Unknown tags are dropped in normalize; they do not fail step1.
WHITELIST: set[str] = set(
    """outline_level font_family font_size bold italic color alignment line_spacing space_before space_after
first_line_indent hanging_indent left_indent right_indent list_ordered list_unordered keep_with_next page_break_before
cell_align_h_left cell_align_h_center cell_align_h_right
run_bold run_italic run_superscript run_subscript run_caps run_small_caps run_color run_underline run_font_ea run_font_latin
cell_border_top cell_border_bottom cell_border_all cell_border_none cell_shading
cell_valign_top cell_valign_center cell_valign_bottom
table_border_top table_border_bottom table_border_all table_border_none table_align_center table_align_left
table_width_percent table_width_autofit header_repeat
inline_only size_lock_aspect space_before_after caption_below
orientation_portrait orientation_landscape columns_one columns_two margin_intent header_footer_independent
section_break_nextPage""".split()
)

# Common LLM misspellings → canonical whitelist tags
_FOCUS_ALIASES: dict[str, str] = {
    "cell_align_center": "cell_align_h_center",
    "cell_align_left": "cell_align_h_left",
    "cell_align_right": "cell_align_h_right",
    "cell_align_middle": "cell_align_h_center",
}


def _remap_focus_list(vals: Any, dropped: set[str] | None = None) -> list[str]:
    if not isinstance(vals, list):
        return []
    out: list[str] = []
    for x in vals:
        s = str(x).strip()
        if not s:
            continue
        s = _FOCUS_ALIASES.get(s, s)
        if s not in WHITELIST:
            if dropped is not None:
                dropped.add(s)
            continue
        if s not in out:
            out.append(s)
    return out


def _remap_focus_in_roles(roles: dict, dropped: set[str] | None = None) -> None:
    """Rewrite known focus aliases in-place (format_focus / cell_focus / table_focus)."""

    def walk_nodes(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if "format_focus" in n:
                n["format_focus"] = _remap_focus_list(n.get("format_focus"), dropped)
            tl = n.get("table_layout")
            if isinstance(tl, dict):
                if "table_focus" in tl:
                    tl["table_focus"] = _remap_focus_list(tl.get("table_focus"), dropped)
                pm = tl.get("position_map")
                if isinstance(pm, dict):
                    for entry in pm.values():
                        if isinstance(entry, dict) and "cell_focus" in entry:
                            entry["cell_focus"] = _remap_focus_list(
                                entry.get("cell_focus"), dropped,
                            )
            kids = n.get("children")
            if isinstance(kids, list):
                walk_nodes(kids)
            derives = n.get("derives")
            if isinstance(derives, list) and derives and isinstance(derives[0], dict):
                walk_nodes(derives)

    for key in ("section", "table", "image"):
        walk_nodes(roles.get(key))
    para = roles.get("paragraph")
    if isinstance(para, dict):
        for key in ("body", "table_cell"):
            walk_nodes(para.get(key))
    walk_nodes(roles.get("paragraph_runs"))

# Layers that form trees (path within roles{}).
TREE_LAYER_PATHS: list[tuple[str, list[str]]] = [
    ("section", ["section"]),
    ("paragraph.body", ["paragraph", "body"]),
    ("paragraph.table_cell", ["paragraph", "table_cell"]),
    ("table", ["table"]),
    ("image", ["image"]),
]

# Step1 structured output: require sibling layers under roles (not nested in paragraph).
# strict=false so node objects may carry table_layout / image_kind / etc.
ROLE_LIBRARY_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "role_library",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["usage", "genre", "locale", "roles"],
            "properties": {
                "usage": {"type": "string"},
                "genre": {"type": "string"},
                "display_name": {"type": "string"},
                "locale": {"type": "string"},
                "selection_policy": {"type": "object"},
                "roles": {
                    "type": "object",
                    "required": ["section", "paragraph", "paragraph_runs", "table", "image"],
                    "properties": {
                        "section": {"type": "array", "items": {"type": "object"}},
                        "paragraph": {
                            "type": "object",
                            "required": ["body", "table_cell"],
                            "properties": {
                                "body": {"type": "array", "items": {"type": "object"}},
                                "table_cell": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                        "paragraph_runs": {"type": "array", "items": {"type": "object"}},
                        "table": {"type": "array", "items": {"type": "object"}},
                        "image": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "constraints": {"type": "object"},
            },
        },
    },
}
ROLE_LIBRARY_JSON_OBJECT: dict[str, Any] = {"type": "json_object"}

_NODE_ORDER = ["role_id", "parent", "abstract", "display_name", "section_family",
               "heading_level", "format_focus", "discriminators"]


def rid(n: dict) -> str:
    return n.get("role_id") or n.get("span_role") or "?"


def looks_truncated_json(text: str) -> bool:
    """Heuristic: unbalanced braces/brackets or dangling comma/open structure."""
    return classify_json_failure(text, err=None) == "truncated"


def classify_json_failure(text: str, err: BaseException | None = None) -> str:
    """Classify why JSON parse failed → recovery branch.

    Returns one of:
      - ``truncated``: output cut off / unbalanced → continue
      - ``syntax``: locally malformed but looks complete → repair
      - ``no_json``: empty / no object / mostly prose → regenerate
      - ``unknown``: unclear; prefer repair if body is substantial else regenerate
    """
    t = (text or "").strip()
    msg = str(err or "").lower()

    if not t or "{" not in t:
        return "no_json"
    # Only a tiny fragment — not worth repairing
    if len(t) < 80 and t.count("{") <= 1 and t.count("}") == 0:
        return "no_json"

    scrub = re.sub(r'"(?:\\.|[^"\\])*"', '""', t)
    open_obj = scrub.count("{") - scrub.count("}")
    open_arr = scrub.count("[") - scrub.count("]")
    dangling = bool(re.search(r"[,\[\{]\s*$", t) or t.endswith(":") or t.rstrip().endswith("\\"))
    unbalanced = open_obj > 0 or open_arr > 0

    # Parser messages that usually mean cut-off mid-token / mid-structure
    trunc_hints = (
        "unterminated string",
        "unterminated",
        "eof while",
        "unexpected end",
        "end of data",
        "expecting value",  # often at EOF after comma/colon
    )
    # Local syntax with a mostly-closed document
    syntax_hints = (
        "expecting ',' delimiter",
        "expecting ':' delimiter",
        "expecting property name",
        "delimiter",
        "invalid \\escape",
        "invalid control character",
        "extra data",
    )

    if unbalanced or dangling:
        return "truncated"
    if any(h in msg for h in trunc_hints):
        # "Expecting value" at end of nearly-balanced text → still truncated-ish
        if "expecting value" in msg and (dangling or open_obj >= 0):
            # If braces balance and error is near the end → often trailing junk / truncate
            pos = getattr(err, "pos", None)
            if isinstance(pos, int) and len(t) > 0 and pos >= max(0, len(t) - 40):
                return "truncated"
        if "unterminated" in msg or "eof" in msg or "unexpected end" in msg or "end of data" in msg:
            return "truncated"

    if any(h in msg for h in syntax_hints):
        # Balanced + delimiter error mid-document → syntax repair
        if not unbalanced:
            return "syntax"

    # No JSON object shape / decoder found nothing useful
    if "no json object" in msg or "top-level json is not an object" in msg:
        return "no_json"

    # Substantial balanced body with unknown error → try repair first
    if len(t) >= 500 and not unbalanced:
        return "syntax"
    if len(t) < 200:
        return "no_json"
    return "unknown"


def continue_truncated_json_text(
    *,
    partial: str,
    stream_llm_text,
    temperature: float = 0.2,
    max_tokens: int = 16384,
    max_rounds: int = 3,
    tail_chars: int = 2500,
    label: str = "json_continue",
) -> str:
    """Ask the model to resume a cut-off JSON object; returns concatenated text."""
    text = partial
    for i in range(1, max_rounds + 1):
        if not looks_truncated_json(text):
            try:
                lenient_json_load(text)
                return text
            except Exception:  # noqa: BLE001
                pass
        tail = text[-tail_chars:] if len(text) > tail_chars else text
        print(f"[continue] JSON 疑似截断，第 {i}/{max_rounds} 次续写（已有 {len(text)} 字符）…")
        piece = stream_llm_text(
            system=(
                "你是 JSON 续写器。用户给出被截断的大型 JSON 前缀末尾。"
                "你只输出从断点起的**剩余 JSON 文本**："
                "不要重复已出现的内容，不要 Markdown 围栏，不要解释，不要从头再开一个根对象。"
            ),
            user=(
                "下面是被截断 JSON 的末尾（仅供对齐断点）。请从断点严格续写，直到根对象合法闭合。\n\n"
                f"---TAIL---\n{tail}\n---CONTINUE---\n"
            ),
            temperature=temperature,
            max_tokens=max_tokens,
            label=f"{label}:{i}",
        )
        piece = (piece or "").strip()
        if piece.startswith("```"):
            piece = piece.strip("`")
            if piece.lower().startswith("json"):
                piece = piece[4:].lstrip()
        # If model restarts a root object, keep only the suffix after the first incomplete join
        if piece.startswith("{") and text.rstrip().endswith("}"):
            # rare: model returned a full new object — prefer append only if parse of concat fails later
            pass
        if piece.startswith("{") and looks_truncated_json(text):
            # Model restarted; try to strip a leading root and keep from first structural token
            # that continues an array/object (best-effort: drop leading `{...` until a known key)
            m = re.search(r'(\[\s*\{|"[A-Za-z0-9_]+"\s*:)', piece)
            if m and m.start() > 0:
                piece = piece[m.start() :]
        text = text.rstrip() + "\n" + piece
        try:
            lenient_json_load(text)
            print(f"[continue] 续写成功，合计 {len(text)} 字符")
            return text
        except Exception:  # noqa: BLE001
            continue
    return text


def repair_malformed_json_text(
    *,
    broken: str,
    error: str,
    call_llm_text,
    temperature: float = 0.1,
    max_tokens: int = 32768,
    label: str = "json_repair",
    max_input_chars: int = 120_000,
) -> str:
    """Ask the model to fix JSON syntax only; returns repaired full-object text."""
    body = broken or ""
    if len(body) > max_input_chars:
        # Keep head+tail so both structure start and the error vicinity survive
        keep = max_input_chars // 2 - 40
        body = body[:keep] + "\n/* … truncated middle for repair prompt … */\n" + body[-keep:]
    print(f"[repair] JSON 语法修复（broken={len(broken or '')} chars, err={str(error)[:120]}）…")
    fixed = call_llm_text(
        system=(
            "You are a JSON syntax repair tool. "
            "Return ONE complete valid JSON object. "
            "Fix only syntax (commas, brackets, quotes, truncation closure). "
            "Do NOT invent new semantic fields beyond what the broken text already implies. "
            "No markdown fences, no commentary."
        ),
        user=(
            f"Parser error:\n{error}\n\n"
            "Broken JSON follows. Output the repaired full JSON object only.\n\n"
            f"---BROKEN---\n{body}\n---END---\n"
        ),
        temperature=temperature,
        max_tokens=max_tokens,
        label=label,
    )
    text = (fixed or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    if not text.startswith("{") and "{" in text:
        text = text[text.find("{") :]
    return text


def recover_llm_json_dict(
    raw: str,
    *,
    stream_llm_text=None,
    call_llm_text=None,
    temperature: float = 0.2,
    max_tokens: int = 16384,
    continue_rounds: int = 3,
    allow_repair: bool = True,
    regenerate=None,
    parse=None,
) -> tuple[dict, str, list[str]]:
    """Parse LLM JSON with **error-type–driven** recovery (not a fixed sequence).

    Branching:
      - ``truncated`` → continue (then re-classify if still failing)
      - ``syntax`` / ``unknown`` (substantial) → repair once
      - ``no_json`` → regenerate once (if provided); else repair as weak fallback
      - after a branch fails → re-classify and try the next matching branch at most once each

    ``parse`` defaults to ``lenient_json_load``. Pass a stricter loader (e.g. step2
    themes root) so a nested fragment is not treated as success.

    Returns ``(obj, final_raw, strategies)``.
    """
    parse_fn = parse or lenient_json_load
    strategies: list[str] = []
    text = raw or ""
    last_err: Exception | None = None
    tried: set[str] = set()

    try:
        return parse_fn(text), text, ["ok"]
    except Exception as e:  # noqa: BLE001
        last_err = e
        print(f"[recover] 首轮解析失败: {e}")

    def _try_parse(label: str) -> tuple[dict, str, list[str]] | None:
        nonlocal last_err
        try:
            return parse_fn(text), text, list(strategies)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[recover] {label} 后仍失败: {e}")
            return None

    # Up to a few classified attempts (continue / repair / regenerate each at most once)
    for _ in range(4):
        kind = classify_json_failure(text, last_err)
        print(f"[recover] 分类={kind}（已试={sorted(tried) or '-'}）")

        if kind == "truncated" and "continue" not in tried and stream_llm_text is not None:
            tried.add("continue")
            text = continue_truncated_json_text(
                partial=text,
                stream_llm_text=stream_llm_text,
                temperature=min(0.2, float(temperature)),
                max_tokens=min(16384, int(max_tokens)),
                max_rounds=int(continue_rounds),
            )
            strategies.append("continue")
            hit = _try_parse("continue")
            if hit:
                return hit
            continue

        if kind in ("syntax", "unknown") and "repair" not in tried and allow_repair and call_llm_text is not None:
            tried.add("repair")
            text = repair_malformed_json_text(
                broken=text,
                error=str(last_err or "JSONDecodeError"),
                call_llm_text=call_llm_text,
                temperature=0.1,
                max_tokens=max(16384, int(max_tokens)),
            )
            strategies.append("repair")
            hit = _try_parse("repair")
            if hit:
                return hit
            continue

        if kind == "no_json" and "regenerate" not in tried and regenerate is not None:
            tried.add("regenerate")
            print("[recover] 无有效 JSON 对象 → 整篇重生成（1 次）…")
            text = (regenerate() or "").strip()
            strategies.append("regenerate")
            hit = _try_parse("regenerate")
            if hit:
                return hit
            continue

        # Fallbacks when preferred branch unavailable or already tried
        if kind == "truncated" and "repair" not in tried and allow_repair and call_llm_text is not None:
            # Continue unavailable/exhausted but still truncated → repair may close braces
            tried.add("repair")
            text = repair_malformed_json_text(
                broken=text,
                error=str(last_err or "truncated JSON"),
                call_llm_text=call_llm_text,
                temperature=0.1,
                max_tokens=max(16384, int(max_tokens)),
            )
            strategies.append("repair")
            hit = _try_parse("repair-after-truncate")
            if hit:
                return hit
            continue

        if kind in ("syntax", "unknown", "no_json") and "regenerate" not in tried and regenerate is not None:
            tried.add("regenerate")
            print("[recover] 修复无效或无可修复对象 → 整篇重生成（1 次）…")
            text = (regenerate() or "").strip()
            strategies.append("regenerate")
            hit = _try_parse("regenerate")
            if hit:
                return hit
            continue

        if "regenerate" not in tried and regenerate is not None:
            tried.add("regenerate")
            print("[recover] 修复后仍失败 → 整篇重生成（1 次）…")
            text = (regenerate() or "").strip()
            strategies.append("regenerate")
            hit = _try_parse("regenerate")
            if hit:
                return hit
            continue

        break

    raise last_err if last_err is not None else ValueError("JSON recover failed")


def _role_library_score(obj: dict) -> tuple[int, int, int]:
    """Rank parsed objects: populated ``roles`` tree beats tool-call stubs."""
    if not isinstance(obj, dict):
        return (0, 0, 0)
    keys = set(obj.keys())
    if keys and keys <= {"cmd", "yield_time_ms", "max_output_tokens", "timeout", "tool"}:
        return (0, 0, 0)
    roles = obj.get("roles")
    n_nodes = 0
    if isinstance(roles, dict) and roles:
        for v in roles.values():
            if isinstance(v, list):
                n_nodes += len(v)
            elif isinstance(v, dict):
                for sub in v.values():
                    if isinstance(sub, list):
                        n_nodes += len(sub)
        return (3, n_nodes, len(str(obj)))
    if obj.get("usage") == "reference" or ("genre" in obj and "selection_policy" in obj):
        return (2, n_nodes, len(str(obj)))
    return (1, 0, len(str(obj)))


def _iter_json_dicts(text: str) -> list[dict]:
    """Collect top-level JSON objects from fences and from every ``{`` that decodes."""
    found: list[dict] = []
    seen: set[int] = set()
    dec = json.JSONDecoder()

    def _scan(s: str) -> None:
        s = s.strip()
        if s[:4].lower() == "json" and (len(s) == 4 or s[4].isspace() or s[4] == "{"):
            s = s[4:].strip()
        i = 0
        n = len(s)
        while i < n:
            brace = s.find("{", i)
            if brace < 0:
                break
            try:
                obj, end = dec.raw_decode(s, brace)
            except json.JSONDecodeError:
                i = brace + 1
                continue
            if isinstance(obj, dict):
                marker = hash((tuple(sorted(obj.keys())), len(str(obj))))
                if marker not in seen:
                    seen.add(marker)
                    found.append(obj)
            i = brace + max(end - brace, 1)

    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:
                _scan(part)
    _scan(text)
    return found


def lenient_json_load(text: str) -> dict:
    """Parse LLM JSON tolerant of leading prose, fences, trailing junk, and decoy objects.

    Thinking models often emit a tiny tool-call JSON (e.g. ``{"cmd": "..."}``) *before*
    the real Role library. Prefer the object with a populated ``roles`` tree.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("No JSON object found in LLM output")

    candidates = _iter_json_dicts(text)
    if not candidates:
        raise ValueError("No JSON object found in LLM output")

    best = max(candidates, key=_role_library_score)
    score = _role_library_score(best)
    if score[0] <= 0:
        raise ValueError(
            "No Role-library JSON found (only tool-call/thinking stubs). "
            f"saw {len(candidates)} object(s)."
        )
    if len(candidates) > 1 and score[0] >= 3:
        n_stub = sum(1 for o in candidates if _role_library_score(o)[0] <= 0)
        if n_stub:
            print(f"[parse] 丢弃 {n_stub} 个工具调用/思考 JSON，采用含 roles 的对象")
    if not isinstance(best, dict):
        raise ValueError("Top-level JSON is not an object")
    return best


def build_forest(nodes: list) -> list:
    """Return root nodes, each with a normalized 'children' list (any encoding in)."""
    if not nodes:
        return []
    if any(isinstance(n.get("derives"), list) and n["derives"] and isinstance(n["derives"][0], dict) for n in nodes):
        def conv(n):
            m = {k: v for k, v in n.items() if k != "derives"}
            m["children"] = [conv(c) for c in n.get("derives", []) if isinstance(c, dict)]
            return m
        return [conv(n) for n in nodes]

    index = {rid(n): {k: v for k, v in n.items() if k != "derives"} for n in nodes}
    for n in index.values():
        n["children"] = []

    if any(isinstance(n.get("derives"), list) and n["derives"] and isinstance(n["derives"][0], str) for n in nodes):
        child_ids: set[str] = set()
        for n in nodes:
            for cid in n.get("derives", []) or []:
                if isinstance(cid, str) and cid in index:
                    index[rid(n)]["children"].append(index[cid])
                    child_ids.add(cid)
        return [index[rid(n)] for n in nodes if rid(n) not in child_ids]

    if any(n.get("parent") for n in nodes):
        for n in nodes:
            p = n.get("parent")
            if p and p in index:
                index[p]["children"].append(index[rid(n)])
        return [index[rid(n)] for n in nodes if not n.get("parent")]

    return [index[rid(n)] for n in nodes]


def _flatten(roots: list) -> list:
    """Forest -> flat list with `parent` set, nice key order, no children/derives."""
    out: list[dict] = []

    def walk(node: dict, parent: str | None):
        clean = {k: v for k, v in node.items() if k not in ("children", "derives", "parent")}
        ordered: dict[str, Any] = {}
        ordered["role_id"] = clean.pop("role_id", None)
        ordered["parent"] = parent
        for k in _NODE_ORDER[2:]:
            if k in clean:
                ordered[k] = clean.pop(k)
        ordered.update(clean)  # any remaining layer-specific keys (table_layout, attaches_to, ...)
        out.append(ordered)
        for c in node.get("children", []):
            walk(c, ordered["role_id"])

    for r in roots:
        walk(r, None)
    return out


def _get(roles: dict, path: list[str]):
    cur = roles
    for p in path[:-1]:
        cur = cur.get(p) or {}
    return cur.get(path[-1])


def _set(roles: dict, path: list[str], value):
    cur = roles
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value


def _default_cell_focus(pos: str) -> list[str]:
    """Best-effort cell_focus when LLM used shorthand position_map values."""
    s = str(pos).lower()
    if "header" in s or "head" in s:
        return ["cell_border_bottom", "cell_valign_center"]
    if "note" in s:
        return ["cell_border_none"]
    if "stub" in s or "label" in s:
        return ["cell_border_none", "cell_valign_center"]
    return ["cell_border_none"]


def normalize_position_map(pm: Any, dropped: set[str] | None = None) -> dict:
    """Coerce position_map entries to {cell_focus: [...], paragraph: <role_id>}.

    Accepts:
      - dict form ``{"header": "ParaCellHeader"}`` or full objects
      - list form ``[{paragraph, cell_focus}, ...]`` (LLM shorthand without keys)
    """
    if isinstance(pm, list):
        coerced: dict = {}
        used: set[str] = set()
        for i, entry in enumerate(pm):
            if isinstance(entry, str):
                pos = f"cell_{i}"
                coerced[pos] = entry
                continue
            if not isinstance(entry, dict):
                continue
            para = str(entry.get("paragraph") or "")
            pos = str(entry.get("position") or entry.get("pos") or "").strip()
            if not pos:
                pl = para.lower()
                if "header" in pl:
                    pos = "header"
                elif "stub" in pl or "label" in pl:
                    pos = "stub"
                elif "note" in pl:
                    pos = "note"
                elif "data" in pl or "body" in pl or "value" in pl:
                    pos = "data"
                else:
                    pos = f"cell_{i}"
            if pos in used:
                pos = f"{pos}_{i}"
            used.add(pos)
            coerced[pos] = entry
        pm = coerced
    if not isinstance(pm, dict):
        return {}
    out: dict = {}
    for pos, m in pm.items():
        if isinstance(m, str):
            out[pos] = {
                "cell_focus": _default_cell_focus(pos),
                "paragraph": m,
            }
        elif isinstance(m, dict):
            entry = dict(m)
            para = entry.get("paragraph")
            if isinstance(para, dict):
                # rare: nested object with role_id
                para = para.get("role_id") or para.get("paragraph") or ""
            if not para and isinstance(entry.get("role_id"), str):
                para = entry["role_id"]
            entry["paragraph"] = para or ""
            if not isinstance(entry.get("cell_focus"), list):
                entry["cell_focus"] = _default_cell_focus(pos)
            else:
                entry["cell_focus"] = _remap_focus_list(
                    entry.get("cell_focus"), dropped,
                )
            out[pos] = entry
        else:
            out[pos] = {
                "cell_focus": _default_cell_focus(pos),
                "paragraph": str(m) if m is not None else "",
            }
    return out


def _normalize_table_layouts(roles: dict, dropped: set[str] | None = None) -> None:
    for n in roles.get("table") or []:
        if not isinstance(n, dict):
            continue

        # Abstract bases only share format_focus — never keep table_layout / grid maps.
        if n.get("abstract"):
            for key in ("table_layout", "position_map", "table_focus", "pattern"):
                n.pop(key, None)
            continue

        tl = n.get("table_layout")
        # LLM sometimes emits a prose description instead of the required object
        if isinstance(tl, str):
            note = tl.strip()
            n["table_layout"] = {
                "pattern": note[:120] if note else "unspecified",
                "table_focus": [],
                "position_map": {},
                "coerced_from": "string_description",
            }
            if note:
                n.setdefault("discriminators", note)
            tl = n["table_layout"]
        if not isinstance(tl, dict):
            # Only create a layout object when there are misplaced top-level keys to lift
            has_top = any(k in n for k in ("pattern", "table_focus", "position_map"))
            if tl is None and not has_top:
                continue
            n["table_layout"] = {
                "pattern": "unspecified",
                "table_focus": [],
                "position_map": {},
                "coerced_from": type(tl).__name__ if tl is not None else "lifted_top_level",
            }
            tl = n["table_layout"]

        # Lift misplaced top-level table keys into table_layout
        for key in ("pattern", "table_focus", "position_map"):
            if key in n and key not in tl:
                tl[key] = n.pop(key)
            elif key in n and key in tl:
                cur = tl.get(key)
                top = n.pop(key)
                if (not cur) and top:
                    tl[key] = top

        if "position_map" in tl:
            tl["position_map"] = normalize_position_map(
                tl.get("position_map"), dropped,
            )
        else:
            tl.setdefault("position_map", {})
        if "table_focus" in tl:
            tl["table_focus"] = _remap_focus_list(tl.get("table_focus"), dropped)
        pm = tl.get("position_map")
        if isinstance(pm, dict):
            for entry in pm.values():
                if isinstance(entry, dict) and "cell_focus" in entry:
                    entry["cell_focus"] = _remap_focus_list(
                        entry.get("cell_focus"), dropped,
                    )


def normalize_selection_policy(data: dict) -> dict:
    """Enforce research richness policy on selection_policy (in-place + return).

    Documents must be section/paragraph/table/image-rich with **multiple types per
    object**. Never allow table/image (or others) in single_variant_allowed.
    """
    default_mins = {
        "section": 4,
        "paragraph.body": 8,
        "paragraph.table_cell": 3,
        "paragraph_runs": 2,
        "table": 3,
        "image": 3,
    }
    sp = data.get("selection_policy")
    if not isinstance(sp, dict):
        sp = {}
        data["selection_policy"] = sp

    sp["objects_required"] = ["section", "paragraph", "table", "image"]
    # Always empty: our pipeline never permits a single-variant object class
    sp["single_variant_allowed"] = []
    scarce = sp.get("keep_all_when_scarce")
    if not isinstance(scarce, list) or not scarce:
        sp["keep_all_when_scarce"] = ["table", "image"]
    else:
        # ensure table/image stay listed when present in library spirit
        for x in ("table", "image"):
            if x not in scarce:
                scarce.append(x)
        sp["keep_all_when_scarce"] = scarce

    mins = sp.get("min_variants_default")
    if isinstance(mins, (int, float)):
        # old scalar form → expand
        n = max(2, int(mins))
        sp["min_variants_default"] = {k: max(n, v) for k, v in default_mins.items()}
    elif isinstance(mins, dict):
        merged = dict(default_mins)
        for k, v in mins.items():
            try:
                merged[str(k)] = max(int(v), default_mins.get(str(k), 2))
            except (TypeError, ValueError):
                pass
        # floors for table/image especially
        merged["table"] = max(merged.get("table", 3), 3)
        merged["image"] = max(merged.get("image", 3), 3)
        sp["min_variants_default"] = merged
    else:
        sp["min_variants_default"] = dict(default_mins)
    return data


def _looks_like_table_cell_role(n: dict) -> bool:
    """Heuristic: cell paragraph roles wrongly placed under paragraph.body."""
    rid_s = str(n.get("role_id") or "")
    parent = str(n.get("parent") or "")
    if not rid_s:
        return False
    if "Caption" in rid_s:  # table caption stays in body
        return False
    if rid_s.endswith("CellBase") or rid_s.endswith("TableCellBase"):
        return True
    if rid_s.endswith("Cell") and ("Table" in rid_s or "Tbl" in rid_s or "Cell" in rid_s):
        return True
    if parent.endswith("CellBase") or "TableCellBase" in parent:
        return True
    ff = n.get("format_focus") or []
    if isinstance(ff, list) and any(str(x).startswith("cell_") for x in ff):
        if "Table" in rid_s or "Cell" in rid_s or "Tbl" in rid_s:
            return True
    return False


def _rescue_misplaced_table_cells(roles: dict) -> None:
    """Move cell roles from paragraph.body → paragraph.table_cell when layer is missing.

    Some LLM runs invent ParaTable*Cell under body and omit table_cell entirely;
    validate then reports every position_map.paragraph as undefined.
    """
    para = roles.get("paragraph")
    if not isinstance(para, dict):
        return
    body = para.get("body")
    if not isinstance(body, list) or not body:
        return
    tc = para.get("table_cell")
    if isinstance(tc, list) and tc:
        return
    cellish = [n for n in body if isinstance(n, dict) and _looks_like_table_cell_role(n)]
    if not cellish:
        return
    cell_ids = {rid(n) for n in cellish}
    # Also pull any body node whose parent is among rescued cell nodes
    changed = True
    while changed:
        changed = False
        for n in body:
            if not isinstance(n, dict) or rid(n) in cell_ids:
                continue
            if str(n.get("parent") or "") in cell_ids:
                cellish.append(n)
                cell_ids.add(rid(n))
                changed = True
    rest = [n for n in body if isinstance(n, dict) and rid(n) not in cell_ids]
    para["body"] = rest
    para["table_cell"] = cellish


def _lift_misplaced_role_layers(roles: dict) -> None:
    """Move paragraph_runs/table/image out of ``roles.paragraph`` (common LLM nesting).

    Models often emit ``roles.paragraph.{body, table_cell, paragraph_runs, table, image}``
    instead of sibling keys under ``roles``. Counts then show runs/table/image = 0.
    """
    if not isinstance(roles, dict):
        return
    para = roles.get("paragraph")
    if not isinstance(para, dict):
        return
    for key in ("paragraph_runs", "table", "image"):
        if key not in para:
            continue
        nested = para.pop(key)
        existing = roles.get(key)
        empty = existing in (None, [], {})
        if empty:
            roles[key] = nested
            print(f"[normalize] 从 roles.paragraph 抬出 {key}")
        # if roles already has a real layer, drop the nested copy


def normalize_to_parent(data: dict, dropped_focus: set[str] | None = None) -> dict:
    """Return data with every tree layer in canonical flat+parent form.

    Also lifts misplaced paragraph_runs/table/image into roles{} (top-level or
    wrongly nested under paragraph), normalizes table position_map shorthand,
    and coerces selection_policy to the multi-variant richness contract.
    Unknown format_focus tags are dropped (collected into dropped_focus if given).
    """
    roles = data.setdefault("roles", {})
    for k in ("paragraph_runs", "table", "image", "section", "paragraph"):
        if k in data and k not in roles:
            roles[k] = data.pop(k)
    _lift_misplaced_role_layers(roles)
    # Before flattening trees: rescue cell roles misplaced under body
    if isinstance(roles.get("paragraph"), dict):
        _rescue_misplaced_table_cells(roles)
    for _name, path in TREE_LAYER_PATHS:
        nodes = _get(roles, path)
        if nodes:
            _set(roles, path, _flatten(build_forest(nodes)))
    _normalize_table_layouts(roles, dropped_focus)
    _remap_focus_in_roles(roles, dropped_focus)
    normalize_selection_policy(data)
    return data


def leaves(nodes: list) -> list[str]:
    """Selectable role_ids = every NON-abstract node (with or without children).

    An intermediate non-abstract node is itself a usable role (e.g. a right-aligned
    data cell) even when a more specialized child (right-aligned + bold) derives from it.
    Only `abstract` bases are non-selectable.
    """
    roots = build_forest(nodes)
    out: list[str] = []

    def walk(n):
        if not n.get("abstract"):
            out.append(rid(n))
        for c in n.get("children", []):
            walk(c)

    for r in roots:
        walk(r)
    return out


# Section-family roots that are almost always abstract: true (even if a plan omits the flag).
ABSTRACT_SECTION_BASES: frozenset[str] = frozenset({
    "SecRoot", "SecFrontMatter", "SecMainBody", "SecBackMatter",
})


def iter_layer_nodes(library_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """All tree-layer nodes (section / paragraph.body / table_cell / table / image / runs)."""
    roles = (library_data or {}).get("roles") or {}
    if not isinstance(roles, dict):
        return []
    out: list[dict[str, Any]] = []
    for _name, path in TREE_LAYER_PATHS:
        nodes = _get(roles, path) or []
        if isinstance(nodes, list):
            out.extend(n for n in nodes if isinstance(n, dict) and rid(n) and rid(n) != "?")
    runs = roles.get("paragraph_runs")
    if isinstance(runs, list):
        out.extend(n for n in runs if isinstance(n, dict) and rid(n) and rid(n) != "?")
    return out


def abstract_role_ids(library_data: dict[str, Any] | None) -> set[str]:
    """role_ids marked ``abstract: true`` in the library."""
    return {rid(n) for n in iter_layer_nodes(library_data) if n.get("abstract")}


def is_abstract_role_id(role_id: str, library_data: dict[str, Any] | None = None) -> bool:
    """True when this id must not become a Word named style / T* key.

    Library ``abstract: true`` is source of truth when the id is known.
    Otherwise treat ``*Base`` suffixes and the three section-family roots as abstract.
    """
    r = str(role_id or "").strip()
    if not r:
        return False
    if library_data:
        by_id = {rid(n): n for n in iter_layer_nodes(library_data)}
        if r in by_id:
            return bool(by_id[r].get("abstract"))
    if r in ABSTRACT_SECTION_BASES:
        return True
    return r.endswith("Base")


def selectable_descendants(library_data: dict[str, Any] | None, root_id: str) -> list[str]:
    """Non-abstract descendants of ``root_id`` (BFS, closer nodes first)."""
    nodes = iter_layer_nodes(library_data)
    by_id = {rid(n): n for n in nodes}
    children: dict[str, list[str]] = {}
    for n in nodes:
        p = n.get("parent")
        if p:
            children.setdefault(str(p), []).append(rid(n))
    out: list[str] = []
    seen: set[str] = set()
    queue = list(children.get(str(root_id or "").strip()) or [])
    while queue:
        x = queue.pop(0)
        if x in seen:
            continue
        seen.add(x)
        n = by_id.get(x) or {}
        if not n.get("abstract"):
            out.append(x)
        queue.extend(children.get(x) or [])
    return out


def effective_focus(nodes: list) -> dict[str, list[str]]:
    """role_id -> ordered union of format_focus along root→node (parent chain).

    Works on a flat+parent layer. Duplicates removed, root focus first.
    """
    index = {rid(n): n for n in nodes}
    cache: dict[str, list[str]] = {}

    def resolve(node_id: str, guard: int = 0) -> list[str]:
        if node_id in cache:
            return cache[node_id]
        n = index.get(node_id)
        if n is None or guard > 100:
            return []
        parent = n.get("parent")
        base = list(resolve(parent, guard + 1)) if parent else []
        for f in n.get("format_focus") or []:
            if f not in base:
                base.append(f)
        cache[node_id] = base
        return base

    return {rid(n): resolve(rid(n)) for n in nodes}


def _collect_tokens(o, used: set):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("format_focus", "table_focus", "cell_focus") and isinstance(v, list):
                used.update(v)
            else:
                _collect_tokens(v, used)
    elif isinstance(o, list):
        for x in o:
            _collect_tokens(x, used)


def validate(data: dict) -> list[str]:
    """Return a list of structural problems (empty = OK)."""
    problems: list[str] = []
    roles = data.get("roles", {})
    para = roles.get("paragraph", {})
    cell_leaves = set(leaves(para.get("table_cell") or []))

    for layer, pred in (
        ("section", lambda: leaves(roles.get("section") or [])),
        ("paragraph.body", lambda: leaves(para.get("body") or [])),
        ("paragraph.table_cell", lambda: leaves(para.get("table_cell") or [])),
        ("paragraph_runs", lambda: [rid(r) for r in (roles.get("paragraph_runs") or [])]),
        ("table", lambda: leaves(roles.get("table") or [])),
        ("image", lambda: leaves(roles.get("image") or [])),
    ):
        if not pred():
            problems.append(
                f"{layer} 为空：须是 roles 下的独立层（paragraph 只能含 body/table_cell；"
                "paragraph_runs/table/image 不要塞进 paragraph）"
            )

    # tree integrity: parent must exist within the same layer
    for _name, path in TREE_LAYER_PATHS:
        nodes = _get(roles, path) or []
        ids = {rid(n) for n in nodes}
        for n in nodes:
            p = n.get("parent")
            if p and p not in ids:
                problems.append(f"{_name}:{rid(n)} parent 不存在: {p}")
            if n.get("abstract") and n.get("table_layout"):
                problems.append(
                    f"{_name}:{rid(n)} abstract 不应带 table_layout"
                    f"（abstract 只作共享 format_focus 基类；格子/网格写在具体表叶子上）"
                )

    # paragraph.body 不得扁平：叶子不应直接挂在唯一根下，须有与 section 对齐的中间 abstract 族
    body_nodes = para.get("body") or []
    if body_nodes:
        body_ids = {rid(n) for n in body_nodes}
        roots = [n for n in body_nodes if not n.get("parent")]
        abstracts = [n for n in body_nodes if n.get("abstract")]
        mid_abstracts = [
            n for n in abstracts
            if n.get("parent") and rid(n) in body_ids
            and any(rid(c) for c in body_nodes if c.get("parent") == rid(n) and not c.get("abstract"))
        ]
        leaf_direct = [
            rid(n) for n in body_nodes
            if not n.get("abstract") and n.get("parent") in {rid(r) for r in roots}
        ]
        if len(roots) == 1 and leaf_direct and len(mid_abstracts) < 2:
            problems.append(
                "paragraph.body 层次过平：叶子直接挂在根下。"
                "须设与 section 对齐的中间 abstract 族"
                "（如 ParaFrontMatterBase / ParaMainBodyBase / ParaBackMatterBase），"
                "叶子挂到对应族，并建议标注 section_family。"
            )
        fam_ok = {"front_matter", "body", "back_matter"}
        for n in mid_abstracts:
            fam = n.get("section_family")
            if fam and fam not in fam_ok:
                problems.append(
                    f"paragraph.body:{rid(n)} section_family 非法: {fam}（应为 {sorted(fam_ok)}）"
                )

    # table roles: leaves must carry table_layout with table_focus + position_map
    for t in build_forest(roles.get("table") or []):
        def check_table(n):
            if not isinstance(n, dict):
                problems.append(f"table 节点类型异常: {type(n).__name__}")
                return
            kids = n.get("children", [])
            if not isinstance(kids, list):
                kids = []
            if not kids and not n.get("abstract"):
                tl = n.get("table_layout")
                if not isinstance(tl, dict):
                    problems.append(
                        f"table {rid(n)} 的 table_layout 须为对象"
                        f"（含 table_focus / position_map），实际为 {type(tl).__name__}"
                    )
                    return
                if "table_focus" not in tl:
                    problems.append(f"table {rid(n)} 缺 table_focus")
                pm = tl.get("position_map") or {}
                if not isinstance(pm, dict):
                    problems.append(
                        f"table {rid(n)} 的 position_map 须为对象，实际: {type(pm).__name__}"
                    )
                    return
                if not pm:
                    problems.append(f"table {rid(n)} 缺 position_map")
                for pos, m in pm.items():
                    if not isinstance(m, dict):
                        problems.append(f"{rid(n)}.{pos} position_map 值须为对象，实际: {type(m).__name__}")
                        continue
                    if "cell_focus" not in m:
                        problems.append(f"{rid(n)}.{pos} 缺 cell_focus")
                    para = m.get("paragraph")
                    if para not in cell_leaves:
                        problems.append(f"{rid(n)}.{pos} paragraph 未定义: {para}")
            for c in kids:
                check_table(c)
        check_table(t)

    # Unknown format_focus tags are stripped in normalize; leftover names do not
    # fail step1. Step4 maps remaining tags to officecli keys and drops the rest.
    return problems


def prune(data: dict, keep_by_layer: dict[str, set]) -> dict:
    """Return a copy of the library keeping only selected roles (+ their ancestors) per layer.

    keep_by_layer keys: section / paragraph.body / paragraph.table_cell / table / image / paragraph_runs.
    Ancestors of kept nodes are retained so the tree stays connected for visualization.
    """
    out = copy.deepcopy(data)
    roles = out.setdefault("roles", {})
    for name, path in TREE_LAYER_PATHS:
        nodes = _get(roles, path) or []
        if not nodes:
            continue
        index = {rid(n): n for n in nodes}
        keep = set(keep_by_layer.get(name) or [])
        for i in list(keep):
            cur = index.get(i)
            guard = 0
            while cur is not None and guard < 100:
                p = cur.get("parent")
                if not p:
                    break
                keep.add(p)
                cur = index.get(p)
                guard += 1
        _set(roles, path, [n for n in nodes if rid(n) in keep])
    runs = roles.get("paragraph_runs")
    if isinstance(runs, list):
        kr = set(keep_by_layer.get("paragraph_runs") or [])
        if kr:
            roles["paragraph_runs"] = [r for r in runs if rid(r) in kr]
    return out


def summary(data: dict) -> dict:
    roles = data.get("roles", {})
    para = roles.get("paragraph", {})
    return {
        "section": leaves(roles.get("section") or []),
        "paragraph.body": leaves(para.get("body") or []),
        "paragraph.table_cell": leaves(para.get("table_cell") or []),
        "paragraph_runs": [rid(r) for r in roles.get("paragraph_runs", [])],
        "table": leaves(roles.get("table") or []),
        "image": leaves(roles.get("image") or []),
    }


def _esc_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _disc(n: dict) -> str:
    d = n.get("discriminators")
    if isinstance(d, list):
        return "；".join(str(x) for x in d)
    return str(d or "")


def write_inventory_md(data: dict, path) -> Any:
    """Human-readable inventory (Markdown tables) for reviewing a Role library draft."""
    from pathlib import Path as _Path

    path = _Path(path)
    roles = data.get("roles", {})
    para = roles.get("paragraph", {})
    sm = summary(data)
    lines: list[str] = [
        f"# {data.get('display_name') or data.get('genre') or path.stem} — 内容参考清单",
        "",
        f"- **genre**: `{data.get('genre', '')}`",
        f"- **locale**: `{data.get('locale', '')}`",
        f"- **usage**: `{data.get('usage', 'reference')}`（软先验：供大纲/样式参考，非硬选型）",
        "",
        "## 数量一览",
        "",
        "| 层 | 可用标签数（非 abstract） |",
        "|---|---:|",
    ]
    for k, ids in sm.items():
        lines.append(f"| {k} | {len(ids)} |")
    lines += ["", "---", ""]

    def tree_table(title: str, nodes: list, extra_cols: list[str] | None = None):
        extra_cols = extra_cols or []
        lines.append(f"## {title}")
        lines.append("")
        header = ["role_id", "display_name", "parent", "abstract", "discriminators", "format_focus"] + extra_cols
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for n in nodes or []:
            row = [
                _esc_cell(rid(n)),
                _esc_cell(n.get("display_name")),
                _esc_cell(n.get("parent") or "—"),
                "✓" if n.get("abstract") else "",
                _esc_cell(_disc(n)),
                _esc_cell(", ".join(n.get("format_focus") or [])),
            ]
            for col in extra_cols:
                if col == "image_kind":
                    row.append(_esc_cell(n.get("image_kind") or ""))
                elif col == "content_hint":
                    row.append(_esc_cell(n.get("content_hint") or ""))
                elif col == "size_intent":
                    row.append(_esc_cell(n.get("size_intent") or ""))
                elif col == "pattern":
                    tl = n.get("table_layout") or {}
                    row.append(_esc_cell(tl.get("pattern") or ""))
                elif col == "positions":
                    tl = n.get("table_layout") or {}
                    pm = tl.get("position_map") or {}
                    row.append(_esc_cell(", ".join(pm.keys())))
                else:
                    row.append(_esc_cell(n.get(col) or ""))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    tree_table("section（节）", roles.get("section") or [])
    tree_table("paragraph.body（正文段落）", para.get("body") or [])
    tree_table("paragraph.table_cell（单元格文本）", para.get("table_cell") or [])

    lines.append("## paragraph_runs（段内片段）")
    lines.append("")
    lines.append("| span_role / role_id | display_name | attaches_to | format_focus | example_substr |")
    lines.append("|---|---|---|---|---|")
    for n in roles.get("paragraph_runs") or []:
        lines.append(
            "| {rid} | {dn} | {at} | {ff} | {ex} |".format(
                rid=_esc_cell(rid(n)),
                dn=_esc_cell(n.get("display_name") or n.get("span_role")),
                at=_esc_cell(", ".join(n.get("attaches_to") or [])),
                ff=_esc_cell(", ".join(n.get("format_focus") or [])),
                ex=_esc_cell(n.get("example_substr") or ""),
            )
        )
    lines.append("")

    tree_table("table（表）", roles.get("table") or [], extra_cols=["pattern", "positions"])
    tree_table(
        "image（图）",
        roles.get("image") or [],
        extra_cols=["image_kind", "content_hint", "size_intent"],
    )

    sp = data.get("selection_policy")
    if sp:
        lines += ["## selection_policy（软建议）", "", "```json", json.dumps(sp, ensure_ascii=False, indent=2), "```", ""]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_draft_readme(out_dir, slug: str, counts: dict, validate_ok: bool) -> None:
    """Small index so reviewers know which file to open first."""
    from pathlib import Path as _Path

    out_dir = _Path(out_dir)
    lines = [
        f"# {slug} — Step1 产物说明",
        "",
        "Role 库 = **体裁内容参考清单**（`usage=reference`），供人工浏览与 step2 参考。",
        "",
        "## 建议阅读顺序",
        "",
        "1. **`inventory.md`** ← 人工审阅首选（分层面表格：名称 / 说明 / focus）",
        "2. **`tree.md`** — 树形 + Mermaid 图（看层级关系）",
        "3. **`draft.yaml`** — 定稿用完整机器可读草稿（审改后拷到 `config/role_library/`）",
        "4. **`themes.xlsx`** — 由 **Step1.5** 生成（本步默认不写）",
        "5. **`raw.txt`** — LLM 原始输出（排错用，一般不用看）",
        "",
        "## 本次数值",
        "",
        "| 层 | 数量 |",
        "|---|---:|",
    ]
    for k, v in counts.items():
        n = len(v) if isinstance(v, list) else int(v)
        lines.append(f"| {k} | {n} |")
    lines += [
        "",
        f"- **validate**: {'OK' if validate_ok else '有问题，见运行日志'}",
        "",
    ]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_role_library_for_prompt(data: dict) -> str:
    """Serialize a Role library for step2 prompt injection.

    Puts a **format-intent digest** first (format_focus / discriminators / table /
    image extras) so the model attends to valuable priors, then appends the full
    canonical YAML so nothing is dropped.
    """
    import yaml as _yaml

    roles = data.get("roles", {}) or {}
    para = roles.get("paragraph", {}) or {}
    lines: list[str] = [
        f"usage: {data.get('usage', 'reference')}（体裁内容参考清单 / 软先验）",
        f"genre: {data.get('genre', '')}",
        f"display_name: {data.get('display_name', '')}",
        f"locale: {data.get('locale', '')}",
        "",
        "### 如何使用本库（给规划师）",
        "- 本库列出该体裁**通常有哪些内容类型**及各自的**格式意图标签**（format_focus 等）。",
        "- 写大纲时可多用/少用/合并；同一内容类型多处出现应共用同一 style_id。",
        "- 写 stylesheet.expectation 时：应**继承并细化**下列 format_focus / discriminators /",
        "  table_layout / image_kind·content_hint（仍只写意图级描述，禁止 pt/cm/色值）。",
        "- 优先复用下列 role_id 作为 style_id；不够用时可少量新造并在 selection.notes 说明。",
        "",
        "### 格式意图速览（务必参考）",
        "",
    ]

    def _disc_one(n: dict) -> str:
        d = n.get("discriminators")
        if isinstance(d, list):
            return "；".join(str(x) for x in d)
        return str(d or "")

    def digest_layer(title: str, nodes: list, extras: list[str] | None = None):
        extras = extras or []
        lines.append(f"#### {title}")
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            tag = " [abstract]" if n.get("abstract") else ""
            ff = ", ".join(n.get("format_focus") or []) or "—"
            bits = [
                f"- `{rid(n)}`{tag} | {n.get('display_name') or ''}",
                f"  parent={n.get('parent') or 'null'}",
                f"  format_focus=[{ff}]",
            ]
            disc = _disc_one(n)
            if disc:
                bits.append(f"  discriminators: {disc}")
            if "image_kind" in extras and n.get("image_kind"):
                bits.append(
                    f"  image_kind={n.get('image_kind')}; "
                    f"content_hint={n.get('content_hint') or ''}; "
                    f"size_intent={n.get('size_intent') or ''}"
                )
            if "table_layout" in extras and isinstance(n.get("table_layout"), dict):
                tl = n["table_layout"]
                pm = tl.get("position_map") or {}
                pos_brief = []
                for pos, m in pm.items():
                    if isinstance(m, dict):
                        pos_brief.append(
                            f"{pos}→{m.get('paragraph')}(cell_focus={','.join(m.get('cell_focus') or [])})"
                        )
                    else:
                        pos_brief.append(f"{pos}→{m}")
                bits.append(
                    f"  table_layout.pattern={tl.get('pattern')}; "
                    f"table_focus=[{', '.join(tl.get('table_focus') or [])}]; "
                    f"positions: {'; '.join(pos_brief)}"
                )
            if n.get("requires_runs") or n.get("optional_runs"):
                bits.append(
                    f"  requires_runs={n.get('requires_runs') or []}; "
                    f"optional_runs={n.get('optional_runs') or []}"
                )
            if n.get("reusable") is not None or n.get("instance_overridable"):
                bits.append(
                    f"  reusable={n.get('reusable')}; "
                    f"instance_overridable={n.get('instance_overridable') or []}"
                )
            lines.extend(bits)
        lines.append("")

    digest_layer("section", roles.get("section") or [])
    digest_layer("paragraph.body", para.get("body") or [])
    digest_layer("paragraph.table_cell", para.get("table_cell") or [])

    lines.append("#### paragraph_runs")
    for n in roles.get("paragraph_runs") or []:
        if not isinstance(n, dict):
            continue
        lines.append(
            f"- `{rid(n)}` | {n.get('display_name') or n.get('span_role') or ''} | "
            f"attaches_to={n.get('attaches_to') or []} | "
            f"format_focus=[{', '.join(n.get('format_focus') or [])}] | "
            f"example={n.get('example_substr') or ''}"
        )
    lines.append("")

    digest_layer("table", roles.get("table") or [], extras=["table_layout"])
    digest_layer("image", roles.get("image") or [], extras=["image_kind"])

    if data.get("selection_policy"):
        lines += [
            "#### selection_policy（软建议，可偏离）",
            _yaml.safe_dump(data["selection_policy"], allow_unicode=True, sort_keys=False).rstrip(),
            "",
        ]
    if data.get("constraints"):
        lines += [
            "#### constraints（软参考）",
            _yaml.safe_dump(data["constraints"], allow_unicode=True, sort_keys=False).rstrip(),
            "",
        ]

    # Full YAML appendix — drop comment-only noise by dumping the structured object
    clean = {k: v for k, v in data.items()}
    lines += [
        "### 完整库 YAML（权威细节；与上方速览一致）",
        "```yaml",
        _yaml.safe_dump(clean, allow_unicode=True, sort_keys=False, width=1000).rstrip(),
        "```",
    ]
    return "\n".join(lines)

