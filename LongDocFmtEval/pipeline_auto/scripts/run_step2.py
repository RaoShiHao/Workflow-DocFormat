"""Step 2: Role library → C1/C2/C3 themes（step2 产物，与 complexity 绑定）.

产物::
  datasets/{id}_{lang}_{topic_name}/step2_themes/themes.xlsx
  datasets/{id}_{lang}_{topic_name}/step2_themes/structure_briefs.json
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.draft_resolve import parse_ids_arg, resolve_draft_dir
from LongDocFmtEval.common.llm import load_prompt, render_template
from LongDocFmtEval.pipeline_auto.auto_llm import call_llm_text, llm_config_for
from LongDocFmtEval.pipeline_auto.seed.writing_outline import render_role_inventory_compact
from LongDocFmtEval.common.theme_diversity import DEFAULT_SIM_THRESHOLD, filter_themes_by_similarity
from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS, _norm_complexity, make_themes_template
from LongDocFmtEval.pipeline_auto.auto_paths import (
    CONFIG_DIR,
    DEFAULT_THEME_COUNT,
    ROLE_LIBRARY_DRAFTS_DIR,
)
from LongDocFmtEval.pipeline_auto.topic_layout import resolve_topic_dir, themes_dir

DRAFTS_DIR = ROLE_LIBRARY_DRAFTS_DIR
STEP2_ERROR_FILENAME = "error.json"
STEP2_FAIL_MARKER = "STEP2_FAILED"

# Structured output (strict=False: extra notes/keys allowed; root must be themes[]).
STEP2_THEMES_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step2_themes",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["themes"],
            "properties": {
                "themes": {
                    "type": "array",
                    "minItems": 3,
                    "items": {
                        "type": "object",
                        "required": ["complexity", "theme", "structure_brief"],
                        "properties": {
                            "complexity": {"type": "string"},
                            "theme": {"type": "string"},
                            "writing_requirements": {"type": "string"},
                            "supplementary_notes": {"type": "string"},
                            "structure_brief": {
                                "type": "object",
                                "required": ["recommend", "stylesheet_plan"],
                                "properties": {
                                    "recommend": {
                                        "type": "object",
                                        "properties": {
                                            "section_count": {"type": "integer"},
                                            "heading_depth": {"type": "integer"},
                                            "stylesheet_role_count": {"type": "integer"},
                                        },
                                    },
                                    "recommend_notes": {"type": "object"},
                                    "stylesheet_by_category": {"type": "object"},
                                    "stylesheet_plan": {
                                        "type": "object",
                                        "required": [
                                            "keep_distinct",
                                            "merge_groups",
                                            "omit_roles",
                                            "tier_rule",
                                        ],
                                        "properties": {
                                            "keep_distinct": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "merge_groups": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "into": {"type": "string"},
                                                        "from_examples": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                        },
                                                        "note": {"type": "string"},
                                                    },
                                                },
                                            },
                                            "omit_roles": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "tier_rule": {"type": "string"},
                                        },
                                    },
                                    "role_selection_policy": {"type": "string"},
                                    "rationale": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _themes_response_format(*, count: int) -> dict:
    fmt = copy.deepcopy(STEP2_THEMES_RESPONSE_FORMAT)
    n = max(1, int(count or 1))
    themes_schema = fmt["json_schema"]["schema"]["properties"]["themes"]
    themes_schema["minItems"] = n
    if n == 3:
        themes_schema["maxItems"] = 3
    return fmt


def _prompt_path(locale: str, name: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name)


def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:
                s = part.strip()
                if s[:4].lower() == "json" and (len(s) == 4 or s[4].isspace() or s[4] == "{"):
                    s = s[4:].strip()
                if s.startswith("{"):
                    return s
    if t[:4].lower() == "json" and (len(t) == 4 or (len(t) > 4 and (t[4].isspace() or t[4] == "{"))):
        t = t[4:].strip()
    return t


_THEME_KEY_ALIASES = (
    "theme", "title", "topic", "theme_name", "name", "document_title", "doc_title",
)
_WRITING_KEY_ALIASES = (
    "writing_requirements", "writing", "writing_requirement", "requirements",
)
_QUOTED_TITLE = re.compile(
    r'[“「『"]([^”」』"\n]{4,60})[”」』"]'
)
_AS_SHARED_THEME = re.compile(
    r'以\s*[“「『"]?([^”」』"\n]{4,60})[”」』"]?\s*为同一主题'
)


def _norm_theme_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def _item_heading_depth(item: dict) -> int | None:
    sb = item.get("structure_brief") if isinstance(item.get("structure_brief"), dict) else {}
    rec = sb.get("recommend") if isinstance(sb.get("recommend"), dict) else {}
    raw = (rec or {}).get("heading_depth")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _c123_identical_gaps(obj: dict) -> list[str]:
    """Notes when C1/C2/C3 share the same theme or the same heading_depth."""
    items = [t for t in (obj.get("themes") or []) if isinstance(t, dict)]
    if len(items) < 3:
        return []
    by_cx: dict[str, dict] = {}
    for t in items:
        cx = _norm_complexity(t.get("complexity"))
        if cx and cx not in by_cx:
            by_cx[cx] = t
    trio = [by_cx[c] for c in COMPLEXITY_TIERS if c in by_cx]
    if len(trio) < 3:
        trio = items[:3]
    notes: list[str] = []
    themes = [_norm_theme_key(str(t.get("theme") or "")) for t in trio]
    if themes[0] and len(set(themes)) == 1:
        notes.append(f"theme 三档相同「{trio[0].get('theme')}」")
    depths = [_item_heading_depth(t) for t in trio]
    if all(d is not None for d in depths) and len(set(depths)) == 1:
        notes.append(f"heading_depth 三档相同 {depths[0]}")
    return notes


def _usable_theme_items(obj: dict) -> list[dict]:
    return [
        t for t in (obj.get("themes") or [])
        if isinstance(t, dict) and str(t.get("theme") or "").strip()
    ]


def _themes_cover_count(obj: dict, count: int) -> bool:
    items = _usable_theme_items(obj)
    if len(items) < count:
        return False
    if count == 3:
        have = {_norm_complexity(t.get("complexity")) for t in items}
        have.discard("")
        return have >= set(COMPLEXITY_TIERS) or len(items) >= 3
    return True


def _themes_payload_ok(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    themes = obj.get("themes")
    if not isinstance(themes, list) or not themes:
        return False
    return any(
        isinstance(t, dict) and str(t.get("theme") or "").strip()
        for t in themes
    )


def _first_nonempty_str(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _title_from_prose(text: str) -> str:
    if not text:
        return ""
    m = _AS_SHARED_THEME.search(text)
    if m:
        return m.group(1).strip()
    m = _QUOTED_TITLE.search(text)
    if m:
        return m.group(1).strip()
    return ""


def _coerce_themes_root(obj: dict) -> dict:
    """Accept slightly off-schema wrappers; always return a dict with themes[]."""
    if isinstance(obj.get("themes"), list):
        return obj
    for key in ("items", "results", "data", "payload"):
        inner = obj.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("themes"), list):
            return inner
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            obj = dict(obj)
            obj["themes"] = inner
            return obj
    return obj


def _fill_missing_theme_fields(obj: dict) -> list[str]:
    """Copy alias keys / quoted titles into ``theme``. Returns log notes."""
    notes: list[str] = []
    themes = obj.get("themes")
    if not isinstance(themes, list):
        return notes
    for t in themes:
        if not isinstance(t, dict):
            continue
        writing = _first_nonempty_str(t, _WRITING_KEY_ALIASES)
        if writing and not str(t.get("writing_requirements") or "").strip():
            t["writing_requirements"] = writing
        theme = _first_nonempty_str(t, _THEME_KEY_ALIASES)
        if not theme:
            sb = t.get("structure_brief") if isinstance(t.get("structure_brief"), dict) else {}
            theme = _title_from_prose(
                " ".join(
                    str(x) for x in (
                        t.get("rationale"),
                        t.get("supplementary_notes"),
                        writing,
                        (sb or {}).get("rationale"),
                        (sb or {}).get("role_selection_policy"),
                    ) if x
                )
            )
            if theme:
                notes.append(f"{t.get('complexity') or '?'}: theme←prose「{theme}」")
        elif "theme" not in t or not str(t.get("theme") or "").strip():
            notes.append(f"{t.get('complexity') or '?'}: theme←alias「{theme}」")
        if theme:
            t["theme"] = theme
    return notes


def parse_themes_json(text: str, *, require_count: int = 1) -> dict:
    """Require a root object with non-empty ``themes[].theme``.

    When ``require_count==3``, C1/C2/C3 must all be present as objects (strings
    in the array do not count). Incomplete coverage is a parse failure.
    """
    t = _strip_json_fence(text)
    if t and not t.startswith("{") and "{" in t:
        t = t[t.find("{") :]
    if not t:
        raise ValueError("No JSON object found in LLM output")
    try:
        obj = json.loads(t)
    except json.JSONDecodeError as e:
        raise ValueError(f"themes JSON syntax error: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("themes JSON 根必须是对象")
    obj = _coerce_themes_root(obj)
    notes = _fill_missing_theme_fields(obj)
    if notes:
        print("[parse] 补全 theme: " + "；".join(notes))
    if not _themes_payload_ok(obj):
        raise ValueError("未解析到任何 theme（根对象缺少非空 themes[].theme）")
    need = max(1, int(require_count or 1))
    if not _themes_cover_count(obj, need):
        have = [
            _norm_complexity(t.get("complexity")) or "?"
            for t in _usable_theme_items(obj)
        ]
        raise ValueError(f"themes 未覆盖 C1/C2/C3（有效对象 {have or '无'}）")
    return obj


def _write_step2_error(
    out_dir: Path,
    *,
    stage: str,
    error: str,
    extra: dict | None = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "ok": False,
        "step": "step2",
        "marker": STEP2_FAIL_MARKER,
        "stage": stage,
        "error": error,
        "hint": (
            "themes JSON 解析失败（整步重试 1 次后仍无效）。"
            "下游勿依赖本目录产物。请检查 themes.raw.txt 后 --overwrite 重跑 step2。"
        ),
        "artifacts": {
            "raw": str((out_dir / "themes.raw.txt").resolve()) if (out_dir / "themes.raw.txt").is_file() else None,
            "attempt1": (
                str((out_dir / "themes.raw.attempt1.txt").resolve())
                if (out_dir / "themes.raw.attempt1.txt").is_file()
                else None
            ),
        },
    }
    if extra:
        payload.update(extra)
    path = out_dir / STEP2_ERROR_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _clear_step2_error(out_dir: Path) -> None:
    path = Path(out_dir) / STEP2_ERROR_FILENAME
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def resolve_step1_dir(path: Path) -> Path:
    path = path.resolve()
    if path.is_file() and path.name == "draft.yaml":
        return path.parent
    if path.is_dir():
        return path
    raise FileNotFoundError(f"--from-step1 需要 Step1 草稿目录或 draft.yaml：{path}")


def resolve_library(step1_dir: Path, genre: str, locale: str, override: str | None) -> Path:
    if override:
        p = Path(override)
        if p.exists():
            return p
        raise FileNotFoundError(f"--library 不存在: {p}")
    slug = f"{genre}_{'en' if str(locale).lower().startswith('en') else 'zh'}"
    for cand in (
        step1_dir / "draft.yaml",
        CONFIG_DIR / "role_library" / f"{slug}.yaml",
        DRAFTS_DIR / slug / "draft.yaml",
    ):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"找不到 Role 库（先跑 step1）: {slug}")


def _infer_meta(step1_dir: Path, lib_data: dict, genre_arg: str, locale_arg: str) -> tuple[str, str, str]:
    loc = locale_arg or str(lib_data.get("locale") or "zh")
    loc = "en" if str(loc).lower().startswith("en") else "zh"
    folder = step1_dir.name
    # topic_01_foo_zh/step1 → parent name; or 01_foo_zh flat draft
    if folder.endswith(f"_{loc}"):
        genre = folder[: -(len(loc) + 1)]
        # strip leading id_ if present
        if "_" in genre and genre.split("_", 1)[0].isdigit():
            genre = genre.split("_", 1)[1]
    else:
        genre = genre_arg or str(lib_data.get("genre") or folder).replace(f"_{loc}", "")
    display = str(lib_data.get("display_name") or genre)
    return genre, loc, display


def _assign_complexities(rows: list[dict], *, count: int) -> list[dict]:
    """Ensure complexity filled; when count==3 force C1/C2/C3 coverage."""
    for i, r in enumerate(rows):
        cx = _norm_complexity(r.get("complexity"))
        if not cx:
            cx = COMPLEXITY_TIERS[min(i, 2)]
        r["complexity"] = cx
    if count == 3 and len(rows) >= 3:
        # Prefer order C1,C2,C3 for first three; re-label if model missed coverage
        have = {_norm_complexity(r.get("complexity")) for r in rows[:3]}
        if have != set(COMPLEXITY_TIERS):
            for i, tier in enumerate(COMPLEXITY_TIERS):
                rows[i]["complexity"] = tier
        else:
            # sort so theme_id 1/2/3 map to C1/C2/C3
            order = {t: i for i, t in enumerate(COMPLEXITY_TIERS)}
            head = sorted(rows[:3], key=lambda r: order.get(r["complexity"], 9))
            rows = head + rows[3:]
    return rows


def run_one(
    step1_dir: Path,
    *,
    count: int,
    library: str | None,
    genre_arg: str,
    locale_arg: str,
    extra_notes: str,
    temperature: float | None,
    max_tokens: int,
    overwrite: bool,
    sim_threshold: float,
    enable_sim_filter: bool,
    out_dir: Path | None = None,
) -> bool:
    temperature = 0.9 if temperature is None else temperature
    step1_dir = resolve_step1_dir(step1_dir)
    dest = Path(out_dir) if out_dir else step1_dir
    dest.mkdir(parents=True, exist_ok=True)
    out_xlsx = dest / "themes.xlsx"
    meta_path = dest / "themes_meta.json"

    if out_xlsx.exists() and not overwrite:
        print(f"[cache] 已存在，跳过：{out_xlsx}（--overwrite 可强制重跑）")
        return True

    draft_peek = step1_dir / "draft.yaml"
    peek = yaml.safe_load(draft_peek.read_text(encoding="utf-8")) if draft_peek.exists() else {}
    if not isinstance(peek, dict):
        peek = {}
    genre, loc, display = _infer_meta(step1_dir, peek, genre_arg, locale_arg)

    lib_path = resolve_library(step1_dir, genre, loc, library)
    lib_data = yaml.safe_load(lib_path.read_text(encoding="utf-8"))
    if not isinstance(lib_data, dict):
        raise ValueError(f"Role 库无效: {lib_path}")
    lib_data.setdefault("usage", "reference")
    genre, loc, display = _infer_meta(step1_dir, lib_data, genre or genre_arg, loc)

    role_inventory = render_role_inventory_compact(lib_data)
    prompt = load_prompt(_prompt_path(loc, "step2_suggest_themes.yaml"))
    if count == 3:
        tier_hint = (
            "Must emit exactly three objects: C1 (low), C2 (mid), C3 (high). "
            "Do not stop after C1. Each tier needs a DISTINCT theme string (same genre family, different document scope "
            "matched to that tier's heading_depth). heading_depth should progress overall "
            "(C1 ≤ C2 ≤ C3 and C1 < C3); neighbors may share a depth. Typical ladders: "
            "3/4/5 (shallower genres) or 4/5/6 (deep-nested genres such as annual reports, "
            "dissertations, five-year plans). Do not copy the same title on all three tiers."
            if loc == "en"
            else
            "必须一次输出三个难度不同的对象：C1（低）、C2（中）、C3（高）各一条；写完 C1 必须继续写 C2、C3，禁止只交一档。"
            "三档 theme 必须互不相同（同一体裁家族、不同文书范围，分别适配该档 heading_depth）。"
            "heading_depth 整体递进即可（C1 ≤ C2 ≤ C3 且 C1 < C3；相邻可相同）。"
            "较浅体裁参考 3/4/5，年报/学位论文/五年规划等深嵌套参考 4/5/6。"
            "禁止三档同一题目。"
        )
    else:
        tier_hint = (
            "Diversify by C1/C2/C3 relative complexity."
            if loc == "en"
            else "按 C1/C2/C3 相对复杂度拉开。"
        )

    notes = (extra_notes or ("无" if loc != "en" else "none")).strip()
    notes = f"{notes}\n{tier_hint}".strip()

    user = render_template(
        prompt["user_template"],
        genre=genre,
        genre_display_name=display,
        locale=loc,
        count=str(count),
        extra_notes=notes,
        role_inventory=role_inventory,
    )

    print(f"\n=== step2 themes: {step1_dir.name} ×{count} (C1–C3) ===")
    print(f"[library] {lib_path}")
    print(f"[inventory] compact Role catalog {len(role_inventory)} chars (not full YAML)")
    print(f"[out] {out_xlsx}")
    (dest / "role_inventory.md").write_text(role_inventory, encoding="utf-8")
    print(f"[llm] route=role_library → {llm_config_for('role_library')}")
    from LongDocFmtEval.common.llm_usage import print_usage_summary, usage_scope, write_step_usage_report  # noqa: PLC0415

    def _gen_themes(*, label: str, extra_user: str = "") -> str:
        u = user if not extra_user else f"{user}\n\n{extra_user}"
        return call_llm_text(
            system=prompt["system"], user=u, temperature=temperature, max_tokens=max_tokens,
            route="role_library",
            label=label,
            response_format=_themes_response_format(count=count),
        )

    def _fail_parse(err: Exception, *, raw_text: str, strategies: list[str]) -> bool:
        ep = _write_step2_error(
            dest,
            stage="parse_themes",
            error=str(err),
            extra={
                "raw_chars": len(raw_text or ""),
                "recover_strategies": strategies,
            },
        )
        print(f"[FAIL] 解析失败（整步重试 1 次后仍无效）: {err}")
        print(f"       详见 {ep}")
        try:
            report = write_step_usage_report(dest, _usage_rows, step="step2")
            print_usage_summary(report["summary"], prefix="[step2 tokens]")
        except Exception as ue:  # noqa: BLE001
            print(f"[warn] 写出 usage/llm_usage.json 失败: {ue}")
        return False

    recover_strategies: list[str] = []
    with usage_scope("step2") as _usage_rows:
        raw = _gen_themes(label="suggest_themes")
        (dest / "themes.raw.txt").write_text(raw, encoding="utf-8")
        try:
            data = parse_themes_json(raw, require_count=count)
            recover_strategies = ["ok"]
        except Exception as e:  # noqa: BLE001
            print(f"[retry] 首轮解析失败: {e}")
            print("[retry] 整步重跑 step2 主题生成（1 次）…", flush=True)
            (dest / "themes.raw.attempt1.txt").write_text(raw, encoding="utf-8")
            cover_hint = (
                "Previous draft did not emit three theme objects covering C1, C2, and C3. "
                "Output exactly 3 objects in themes[], complexity C1 then C2 then C3. Do not stop after C1."
                if loc == "en"
                else
                "上一稿 themes 未写全 C1/C2/C3。必须恰好输出 3 条对象，complexity 依次为 C1、C2、C3。"
                "写完 C1 必须继续写 C2、C3，禁止只交一档就结束。"
            )
            extra = cover_hint if "未覆盖 C1/C2/C3" in str(e) else ""
            raw = _gen_themes(label="suggest_themes_retry", extra_user=extra)
            (dest / "themes.raw.txt").write_text(raw, encoding="utf-8")
            recover_strategies = ["retry"]
            try:
                data = parse_themes_json(raw, require_count=count)
                print("[retry] 重试解析成功")
            except Exception as e2:  # noqa: BLE001
                return _fail_parse(e2, raw_text=raw, strategies=recover_strategies)
        if count == 3:
            gaps = _c123_identical_gaps(data)
            if gaps:
                print("[retry] C1/C2/C3 无差异：" + "；".join(gaps))
                print("[retry] 请模型改一稿（仅此 1 次；改后不再校验差异）…", flush=True)
                (dest / "themes.raw.nodiff.txt").write_text(raw, encoding="utf-8")
                feedback = (
                    "Previous draft had no C1/C2/C3 spread: "
                    + "; ".join(gaps)
                    + ". Rewrite one draft: distinct theme per tier; heading_depth should "
                    "progress overall (neighbors may match; not all three the same). "
                    "Typical ladders: 3/4/5 or 4/5/6."
                    if loc == "en"
                    else
                    "上一稿 C1/C2/C3 没有拉开："
                    + "；".join(gaps)
                    + "。请改一稿：三档 theme 互异；heading_depth 整体递进即可"
                    "（相邻可相同；不要三档一样深）。参考 3/4/5 或 4/5/6。"
                )
                raw2 = _gen_themes(label="suggest_themes_spread", extra_user=feedback)
                (dest / "themes.raw.txt").write_text(raw2, encoding="utf-8")
                recover_strategies = list(recover_strategies) + ["spread_retry"]
                try:
                    data = parse_themes_json(raw2, require_count=count)
                    print("[retry] 改稿已解析，不再校验差异")
                except Exception as e_spread:  # noqa: BLE001
                    print(f"[warn] 改稿解析失败，沿用无差异的首稿: {e_spread}")
                    (dest / "themes.raw.txt").write_text(raw, encoding="utf-8")
        try:
            report = write_step_usage_report(dest, _usage_rows, step="step2")
            print_usage_summary(report["summary"], prefix="[step2 tokens]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 usage/llm_usage.json 失败: {e}")

    rows: list[dict] = []
    for t in data.get("themes") or []:
        if not isinstance(t, dict):
            continue
        theme = str(t.get("theme") or "").strip()
        if not theme:
            continue
        rows.append({
            "genre": genre,
            "locale": loc,
            "complexity": _norm_complexity(t.get("complexity")),
            "theme": theme,
            "writing_requirements": str(t.get("writing_requirements") or "").strip(),
            "supplementary_notes": str(t.get("supplementary_notes") or "").strip(),
            "structure_brief": t.get("structure_brief") if isinstance(t.get("structure_brief"), dict) else {},
            "enabled": 1,
        })

    if count == 3 and len(rows) < 3:
        ep = _write_step2_error(
            dest,
            stage="parse_themes",
            error=f"themes 未覆盖 C1/C2/C3（有效 {len(rows)} 条）",
            extra={"recover_strategies": recover_strategies},
        )
        print("[FAIL] themes 未写全 C1/C2/C3")
        print(f"       详见 {ep}")
        return False

    if not rows:
        ep = _write_step2_error(
            dest,
            stage="parse_themes",
            error="未解析到任何 theme",
            extra={"recover_strategies": recover_strategies},
        )
        print("[FAIL] 未解析到任何 theme")
        print(f"       详见 {ep}")
        return False

    _clear_step2_error(dest)

    rows = _assign_complexities(rows, count=count)

    for r in rows:
        r["writing"] = r["writing_requirements"]
        r["notes"] = r["supplementary_notes"]

    kept, sim_meta = filter_themes_by_similarity(
        rows,
        threshold=sim_threshold,
        enabled=enable_sim_filter,
        model=None,
    )
    print(f"[diversity] similarity_filter={sim_meta.get('similarity_filter')}: {sim_meta.get('reason')}")
    if enable_sim_filter and sim_meta.get("similarity_filter") == "skipped":
        print("    （embedding 未接入；当前仅依赖 prompt 多样性。）")

    kept = _assign_complexities(kept, count=count)

    from LongDocFmtEval.pipeline_auto.seed.structure_brief import (  # noqa: PLC0415
        normalize_brief_for_complexity,
        validate_complexity_brief_order,
        write_structure_briefs,
    )

    briefs_by_cx: dict[str, dict] = {}
    theme_meta: dict[str, dict] = {}
    excel_rows = []
    for i, r in enumerate(kept, 1):
        cx = r.get("complexity") or COMPLEXITY_TIERS[min(i - 1, 2)]
        brief = normalize_brief_for_complexity(
            r.get("structure_brief") if isinstance(r.get("structure_brief"), dict) else {},
            cx,
            theme=r.get("theme") or "",
            writing=r.get("writing_requirements") or "",
            genre=genre,
            locale=loc,
        )
        briefs_by_cx[cx] = brief
        theme_meta[cx] = {"theme_id": i, "theme": r.get("theme") or ""}
        excel_rows.append({
            "theme_id": i,
            "complexity": cx,
            "genre": r["genre"],
            "locale": r["locale"],
            "theme": r["theme"],
            "writing_requirements": r.get("writing_requirements") or r.get("writing") or "",
            "supplementary_notes": r.get("supplementary_notes") or r.get("notes") or "",
            "enabled": 1,
        })
        print(
            f"  [{cx}] sec={brief.get('recommend', {}).get('section_count')} "
            f"depth={brief.get('recommend', {}).get('heading_depth')} "
            f"styles={brief.get('recommend', {}).get('stylesheet_role_count')}"
        )

    order_errs = validate_complexity_brief_order(briefs_by_cx)
    if order_errs:
        print("[FAIL] C1/C2/C3 structure_brief 相对顺序校验失败：")
        for e in order_errs:
            print(f"       - {e}")
        (dest / "structure_brief_order_errors.json").write_text(
            json.dumps({"errors": order_errs, "briefs": briefs_by_cx}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return False

    make_themes_template(out_xlsx, blank_rows=0, rows=excel_rows)
    briefs_path = write_structure_briefs(dest, briefs_by_cx, theme_meta=theme_meta)
    print(f"[ok] structure_briefs → {briefs_path}")

    meta = {
        "step": 2,
        "genre": genre,
        "locale": loc,
        "display_name": display,
        "role_library_path": str(lib_path).replace("\\", "/"),
        "count_requested": count,
        "count_written": len(excel_rows),
        "complexities": [r.get("complexity") for r in excel_rows],
        "temperature": temperature,
        "similarity": sim_meta,
        "out": str(out_xlsx).replace("\\", "/"),
        "structure_briefs": str(briefs_path).replace("\\", "/"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] themes={len(excel_rows)} complexities={meta['complexities']} → {out_xlsx}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Step2：按 C1/C2/C3 生成 themes.xlsx（参考 Role 库）")
    ap.add_argument("--id", type=int, default=None, help="genres.xlsx 体裁 id（自动找 step1 草稿）")
    ap.add_argument("--ids", default="", help="多个 id")
    ap.add_argument("--from-step1", default="", help="step1 草稿目录或 draft.yaml")
    ap.add_argument("--library", default=None, help="覆盖 Role 库路径")
    ap.add_argument("--genre", default="", help="覆盖 genre")
    ap.add_argument("--locale", default="", help="覆盖 locale")
    ap.add_argument("--count", type=int, default=DEFAULT_THEME_COUNT,
                    help=f"主题条数（默认 {DEFAULT_THEME_COUNT}=C1+C2+C3）")
    ap.add_argument("--extra-notes", default="", help="额外提示")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--enable-sim-filter", action="store_true")
    ap.add_argument("--sim-threshold", type=float, default=DEFAULT_SIM_THRESHOLD)
    ap.add_argument("--out-dir", default="", help="themes.xlsx 输出目录（默认 topic/step2 或 step1 旁）")
    args = ap.parse_args()

    targets: list[tuple[Path, Path | None]] = []
    if args.from_step1:
        s1 = resolve_step1_dir(Path(args.from_step1))
        out = Path(args.out_dir) if args.out_dir else None
        targets.append((s1, out))
    else:
        ids = parse_ids_arg(args.ids) if args.ids else ([args.id] if args.id is not None else [])
        if not ids:
            ap.error("请指定 --id / --ids 或 --from-step1")
        for gid in ids:
            from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: PLC0415
                lookup_genre_row,
                resolve_topic_dir,
                role_dir,
                themes_dir,
            )
            topic = resolve_topic_dir(gid)
            s1 = role_dir(topic)
            if not (s1 / "draft.yaml").is_file():
                info = lookup_genre_row(gid)
                draft = resolve_draft_dir(
                    DRAFTS_DIR,
                    gid,
                    genre=info.get("genre") or "",
                    locale=info.get("locale") or "",
                )
                s1 = draft
            out = Path(args.out_dir) if args.out_dir else themes_dir(topic)
            targets.append((s1, out))

    ok = 0
    for s1, out in targets:
        if run_one(
            s1,
            count=int(args.count),
            library=args.library,
            genre_arg=args.genre,
            locale_arg=args.locale,
            extra_notes=args.extra_notes,
            temperature=args.temperature,
            max_tokens=int(args.max_tokens),
            overwrite=bool(args.overwrite),
            sim_threshold=float(args.sim_threshold),
            enable_sim_filter=bool(args.enable_sim_filter),
            out_dir=out,
        ):
            ok += 1
    print(f"\n完成 {ok}/{len(targets)}")


if __name__ == "__main__":
    main()
