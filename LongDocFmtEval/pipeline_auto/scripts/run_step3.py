"""Step 3 runner: writing outline → stylesheet → text placement → media placement → blocks → trim catalog.

Preferred CLI:
  python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --id 1 --until-step 3

Outputs (default): datasets/{id}_{lang}_{topic_name}/C*/step3_outlines/
  writing_outline.json / writing_stylesheet.json / writing_text_placement.json /
  writing_media_placement.json / blocks/ / plan.json / stylesheet_scope.json …
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.draft_resolve import outline_dir_name, parse_ids_arg, resolve_draft_dir
from LongDocFmtEval.common.llm import load_prompt, render_template
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import (
    CONFIG_DIR,
    OUTLINES_DIR,
    ROLE_LIBRARY_DRAFTS_DIR,
    migrate_step3_block_files,
    resolve_step3_block_json,
    resolve_step3_block_raw,
    step3_block_attempt_path,
    step3_block_json_path,
    step3_block_raw_path,
    step3_chunks_path,
)
from LongDocFmtEval.pipeline_auto.style_taxonomy import (
    format_ref_roles,
    layers_payload,
    normalize_keep_counts,
    taxonomy_brief,
)
from LongDocFmtEval.common.role_library import (
    lenient_json_load,
    prune,
    render_role_library_for_prompt,
)
from LongDocFmtEval.common.themes_xlsx import make_themes_template, read_themes
from LongDocFmtEval.pipeline_auto.seed.structure_brief import (  # noqa: E402
    brief_to_prompt_block,
    clamp_structure_brief_for_token_budget,
    headings_snippet_from_library,
    load_brief,
)
from LongDocFmtEval.pipeline_auto.seed.heading_contract import (  # noqa: E402
    HEADING_DEPTH_CHECK_FATAL,
    write_heading_check,
)
from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import (  # noqa: E402
    apply_section_page_continuity,
    drop_section_role_blocks,
)
from LongDocFmtEval.pipeline_auto.seed.writing_outline import (  # noqa: E402
    build_only_child_repair_payload,
    check_writing_outline,
    collect_only_child_sites,
    ensure_heading_styles,
    first_body_style_id,
    flatten_only_child_headings,
    format_check_feedback,
    apply_section_roles_to_sheet,
    collect_outline_node_index,
    format_chunk_media_must,
    format_chunk_slots,
    format_chunk_text_must,
    format_chunk_tree,
    format_expectations_from_stylesheet,
    format_heading_style_map,
    format_media_catalog,
    format_section_style_catalog,
    format_stylesheet_catalog,
    format_text_catalog,
    format_writing_outline_compact,
    format_writing_outline_with_ids,
    inject_missing_media_blocks,
    inject_missing_text_styles,
    media_catalog_items,
    media_instance_totals,
    missing_required_media,
    missing_required_text,
    normalize_media_placements,
    normalize_section_roles,
    normalize_text_placements,
    placements_for_chunk,
    heading_signature,
    heading_signature_from_plan,
    infer_selection_from_stylesheet,
    load_writing_outline,
    merge_sections_with_chunks,
    parse_repair_replacements,
    parse_writing_outline,
    render_role_inventory_compact,
    signatures_compatible,
    splice_outline_replacements,
    split_outline_chunks,
    stylesheet_style_ids,
    text_catalog_items,
    trim_stylesheet_to_used,
    unplaced_text_styles,
    uniquify_block_ids,
    walk_heading_nodes,
    write_writing_outline_artifacts,
)

DRAFTS_DIR = ROLE_LIBRARY_DRAFTS_DIR
STEP3_FAIL_MARKER = "STEP3_FAILED"


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


def _outline_payload_ok(obj: object) -> bool:
    """True only for a step3 root plan, not a nested section/block fragment."""
    if not isinstance(obj, dict):
        return False
    sections = obj.get("sections")
    outline = obj.get("outline")
    if not isinstance(sections, list) or not sections:
        return False
    if not isinstance(outline, list) or not outline:
        return False
    n_blocks = sum(
        len(o.get("blocks") or []) for o in outline if isinstance(o, dict)
    )
    return n_blocks >= 1


def parse_outline_json(text: str) -> dict:
    """Require a root outline plan with ``sections[]`` and ``outline[]``.

    ``lenient_json_load`` may return a nested ``{instance_id, blocks}`` when the
    root object is syntactically invalid; that is not a step3 success.
    """
    t = _strip_json_fence(text)
    if not t:
        raise ValueError("No JSON object found in LLM output")
    brace = t.find("{")
    if brace < 0:
        raise ValueError("No JSON object found in LLM output")
    body = t[brace:]
    try:
        obj, _end = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"outline JSON syntax error: {e}") from e
    if not _outline_payload_ok(obj):
        raise ValueError("未解析到有效大纲（需要根对象含非空 sections[] 与 outline[].blocks）")
    return obj


STEP3_STYLESHEET_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step3_stylesheet",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["selection", "stylesheet", "sections"],
            "properties": {
                "meta": {"type": "object"},
                "selection": {"type": "object"},
                "stylesheet": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["style_id"],
                        "properties": {
                            "style_id": {"type": "string"},
                            "display_name": {"type": "string"},
                            "object": {"type": "string"},
                            "heading_level": {"type": ["integer", "null"]},
                            "title_kind": {"type": "string"},
                            "description": {"type": "string"},
                            "expectation": {"type": "string"},
                        },
                    },
                },
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["instance_id", "role_id"],
                        "properties": {
                            "instance_id": {"type": "string"},
                            "role_id": {"type": "string"},
                            "title": {"type": "string"},
                            "purpose": {"type": "string"},
                            "instance_overrides": {"type": "object"},
                        },
                    },
                },
                "format_expectations": {"type": "array"},
                "media_style_diff": {"type": "array"},
            },
        },
    },
}

STEP3_BLOCKS_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step3_blocks",
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
                            "template_exhibit": {"type": "boolean"},
                        },
                    },
                },
            },
        },
    },
}


STEP3_MEDIA_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step3_media_placement",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["placements"],
            "properties": {
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["style_id", "node_id"],
                        "properties": {
                            "style_id": {"type": "string"},
                            "node_id": {"type": "string"},
                            "count": {"type": "integer"},
                        },
                    },
                },
            },
        },
    },
}


STEP3_OUTLINE_REPAIR_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step3_outline_only_child_repair",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["replacements"],
            "properties": {
                "replacements": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["id", "children"],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "kind": {"type": "string"},
                            "heading_level": {"type": ["integer", "null"]},
                            "purpose": {"type": "string"},
                            "children": {"type": "array"},
                        },
                    },
                },
            },
        },
    },
}


def _decode_json_object(text: str) -> dict:
    t = _strip_json_fence(text)
    if not t:
        raise ValueError("No JSON object found in LLM output")
    brace = t.find("{")
    if brace < 0:
        raise ValueError("No JSON object found in LLM output")
    try:
        obj, _end = json.JSONDecoder().raw_decode(t[brace:])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON syntax error: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("根节点必须是对象")
    return obj


def parse_stylesheet_json(text: str) -> dict:
    obj = _decode_json_object(text)
    ss = obj.get("stylesheet")
    if not isinstance(ss, list) or not ss:
        raise ValueError("stylesheet 缺失或为空（本步必须给出闭集 style 目录）")
    if not any(isinstance(x, dict) and (x.get("style_id") or x.get("role_id")) for x in ss):
        raise ValueError("stylesheet 没有有效 style_id")
    if obj.get("outline") or obj.get("blocks"):
        obj.pop("outline", None)
        obj.pop("blocks", None)
    return obj


def parse_media_placement_json(text: str) -> dict:
    obj = _decode_json_object(text)
    pl = obj.get("placements")
    if not isinstance(pl, list):
        raise ValueError("placements 缺失或不是数组")
    return {"placements": [p for p in pl if isinstance(p, dict)]}


def parse_text_placement_json(text: str) -> dict:
    obj = _decode_json_object(text)
    pl = obj.get("placements")
    if not isinstance(pl, list):
        raise ValueError("placements 缺失或不是数组")
    sr = obj.get("section_roles")
    return {
        "placements": [p for p in pl if isinstance(p, dict)],
        "section_roles": [s for s in (sr or []) if isinstance(s, dict)] if isinstance(sr, list) else [],
    }


STEP3_TEXT_PLACEMENT_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "step3_text_placement",
        "strict": False,
        "schema": {
            "type": "object",
            "required": ["placements"],
            "properties": {
                "section_roles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "instance_id": {"type": "string"},
                            "role_id": {"type": "string"},
                        },
                    },
                },
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["style_id", "node_id"],
                        "properties": {
                            "style_id": {"type": "string"},
                            "node_id": {"type": "string"},
                            "count": {"type": "integer"},
                            "kind": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def parse_blocks_json(text: str, instance_id: str) -> dict:
    obj = _decode_json_object(text)
    blocks = obj.get("blocks")
    iid = str(obj.get("instance_id") or instance_id).strip() or instance_id
    if not isinstance(blocks, list) or not blocks:
        outline = obj.get("outline")
        if isinstance(outline, list) and outline and isinstance(outline[0], dict):
            blocks = outline[0].get("blocks")
            iid = str(outline[0].get("instance_id") or iid).strip() or instance_id
    if not isinstance(blocks, list) or not blocks:
        raise ValueError(f"{instance_id}: 未解析到非空 blocks[]")
    clean = [b for b in blocks if isinstance(b, dict) and (b.get("role_id") or b.get("style_id"))]
    if not clean:
        raise ValueError(f"{instance_id}: blocks 没有有效 role_id")
    for b in clean:
        if not b.get("role_id") and b.get("style_id"):
            b["role_id"] = b.get("style_id")
    return {"instance_id": instance_id, "blocks": clean}


def _heading_sequence_text(sig: list[tuple[int, str]]) -> str:
    if not sig:
        return "（本节无正文标题）"
    return "\n".join(f"H{lvl} {title}" for lvl, title in sig)


def _chunk_signature_ok(chunk: dict, blocks: list, stylesheet: list) -> tuple[bool, str]:
    frozen = list(chunk.get("heading_signature") or [])
    planned = heading_signature_from_plan({
        "stylesheet": stylesheet,
        "outline": [{"blocks": blocks}],
    })
    return signatures_compatible(frozen, planned)


def _warn_heading_drift(label: str, ok: bool, reason: str) -> None:
    """Heading-sequence mismatch is informational; do not abort the chapter."""
    if not ok and reason:
        print(f"[warn] {label} 标题序列与写作大纲不完全一致（不中止）：{reason}")


def _write_step3_parse_error(
    out_dir: Path,
    *,
    error: str,
    extra: dict | None = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "step": 3,
        "ok": False,
        "marker": STEP3_FAIL_MARKER,
        "stage": "parse_outline",
        "reason": error,
        "error": error,
        "hint": (
            "outline JSON 解析失败（整步重试 1 次后仍无效）。"
            "不进入后续 template/document。请检查 raw.txt 后 --overwrite 重跑 step3。"
        ),
        "artifacts": {
            "raw": str((out_dir / "raw.txt").resolve()) if (out_dir / "raw.txt").is_file() else None,
            "attempt1": (
                str((out_dir / "raw.attempt1.txt").resolve())
                if (out_dir / "raw.attempt1.txt").is_file()
                else None
            ),
        },
    }
    if extra:
        payload.update(extra)
    path = out_dir / "error.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _prompt_path(locale: str, name: str, *fallbacks: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name, fallbacks=fallbacks)


def _write_structure_brief(
    out_dir: Path,
    *,
    loc: str,
    theme: str,
    writing: str,
    notes: str,
    library_prompt: str,
    lib_path: Path,
    genre: str,
    temperature: float,
    max_tokens: int,
    skip_llm: bool,
    structure_brief: dict | None = None,
    complexity: str = "",
) -> dict:
    """Persist structure_brief for outline. Prefer step2 C-tier budget; no LLM when provided."""
    from LongDocFmtEval.pipeline_auto.seed.structure_brief import (  # noqa: PLC0415
        normalize_brief_for_complexity,
    )

    brief_path = out_dir / "structure_brief.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(structure_brief, dict) and structure_brief:
        cx = complexity or str(structure_brief.get("complexity") or "")
        brief = normalize_brief_for_complexity(
            structure_brief, cx or "C2",
            theme=theme, writing=writing, genre=genre, locale=loc,
        )
        brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[brief] 使用 step2 recommend "
            f"sec/depth/styles="
            f"{brief.get('recommend', {}).get('section_count')}/"
            f"{brief.get('recommend', {}).get('heading_depth')}/"
            f"{brief.get('recommend', {}).get('stylesheet_role_count')} → {brief_path.name}"
        )
        return brief

    if skip_llm or complexity:
        cx = complexity or "C2"
        brief = normalize_brief_for_complexity(
            None, cx, theme=theme, writing=writing, genre=genre, locale=loc,
        )
        brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[brief] C档预设 {cx} → {brief_path.name}（跳过 brief LLM）")
        return brief

    try:
        prompt = load_prompt(_prompt_path(loc, "step3_structure_brief.yaml", "step2_structure_brief.yaml"))
        user = render_template(
            prompt["user_template"],
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            supplementary_notes=notes or "无",
            library_path=str(lib_path).replace("\\", "/"),
            role_library_headings=headings_snippet_from_library(library_prompt),
        )
        print("[brief] 生成 structure_brief …（兼容旧路径）")
        raw = stream_llm_text(
            system=prompt["system"], user=user,
            temperature=min(0.5, float(temperature) + 0.1),
            max_tokens=min(4096, int(max_tokens)),
        )
        (out_dir / "raw_structure_brief.txt").write_text(raw, encoding="utf-8")
        brief = lenient_json_load(raw)
        if not isinstance(brief, dict):
            raise ValueError("brief 非对象")
        brief.setdefault("source", "llm")
    except Exception as exc:  # noqa: BLE001
        print(f"[brief] LLM 失败，改用 C2 预设: {exc}")
        brief = normalize_brief_for_complexity(
            None, "C2", theme=theme, writing=writing, genre=genre, locale=loc,
        )
    brief = clamp_structure_brief_for_token_budget(brief)
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[brief] recommend={brief.get('recommend')} → {brief_path.name}"
        f"{'（已按输出预算收紧）' if brief.get('clamped') else ''}"
    )
    return brief


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(text)).strip("_")
    return s[:32] or "case"


def resolve_library(genre: str, locale: str, override: str | None,
                    step1_dir: Path | None = None) -> Path:
    """Prefer gold config, then Step1 draft in the same dir / drafts tree."""
    if override:
        p = Path(override)
        if p.exists():
            return p
        raise FileNotFoundError(f"--library 指定的文件不存在: {p}")
    slug = f"{genre}_{'en' if str(locale).lower().startswith('en') else 'zh'}"
    cands: list[Path] = []
    if step1_dir is not None:
        cands.append(step1_dir / "draft.yaml")
    cands.append(CONFIG_DIR / "role_library" / f"{slug}.yaml")
    # id-prefixed drafts: 01_genre_zh/draft.yaml
    if DRAFTS_DIR.exists():
        for p in sorted(DRAFTS_DIR.glob(f"*_{slug}")):
            if p.is_dir() and (p / "draft.yaml").exists():
                cands.append(p / "draft.yaml")
        cands.append(DRAFTS_DIR / slug / "draft.yaml")
    for cand in cands:
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"找不到 {slug} 的 Role 库：请先跑 step1，或定稿到 config/role_library/{slug}.yaml，"
        f"或用 --library / --id / --from-step1 指定"
    )


def load_role_library_prompt(lib_path: Path) -> tuple[str, dict]:
    """Load YAML and render a prompt block that surfaces format priors."""
    raw = lib_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Role 库不是映射对象: {lib_path}")
    # Ensure soft-prior marker even if older drafts omit it
    data.setdefault("usage", "reference")
    return render_role_library_for_prompt(data), data


def _brief_recommend(brief: dict, key: str, default: str = "") -> str:
    rec = (brief or {}).get("recommend") or {}
    val = rec.get(key)
    return str(val) if val not in (None, "") else default


def _problems_are_only_child(report: dict) -> bool:
    probs = [str(p) for p in (report.get("problems") or [])]
    if not probs or not report.get("only_child_parents"):
        return False
    return all("独生子" in p or "only-child" in p.lower() or "only child" in p.lower() for p in probs)


def _repair_outline_only_children(
    tree: dict,
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    genre: str,
    brief: dict,
    temperature: float,
    max_tokens: int,
    rounds: int = 2,
) -> dict:
    """Local only-child repair: extract H1 spine + cut, splice by id. No whole-tree rewrite."""
    report = check_writing_outline(tree, brief)
    if not report.get("only_child_parents"):
        return report

    prompt = load_prompt(_prompt_path(loc, "step3_writing_outline_repair.yaml"))
    # 与主大纲一致：不硬顶；跟编排传入的 max_tokens（默认 65536）
    tok = int(max_tokens)

    def _one_round(round_i: int, feedback: str) -> None:
        sites = collect_only_child_sites(tree)
        if not sites:
            return
        payload = build_only_child_repair_payload(sites)
        allowed = {str(s.get("parent_id") or "") for s in sites if s.get("parent_id")}
        print(f"[only-child] 第 {round_i} 轮局部修补 {len(sites)} 处（不整树重写）")
        for s in sites:
            print(f"       · {s.get('issue')}")
        retry = ""
        if feedback.strip():
            retry = "## 上次插回后仍有独生子，请只改这些切口\n" + feedback.strip()
        user = render_template(
            prompt["user_template"],
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            genre=genre,
            repair_payload=json.dumps(payload, ensure_ascii=False, indent=2),
            retry_feedback=retry or "（无）",
        )
        label = f"writing_outline_only_child_r{round_i}"
        print(f"    [writing-outline] {label}")
        raw = stream_llm_text(
            system=prompt["system"], user=user,
            temperature=temperature, max_tokens=tok,
            label=label, response_format=STEP3_OUTLINE_REPAIR_RESPONSE_FORMAT,
        )
        raw_path = out_dir / f"raw_writing_outline_repair_r{round_i}.txt"
        raw_path.write_text(raw, encoding="utf-8")
        try:
            replacements = parse_repair_replacements(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[retry] 独生子修补 JSON 解析失败: {e}")
            (out_dir / f"raw_writing_outline_repair_r{round_i}.attempt1.txt").write_text(
                raw, encoding="utf-8",
            )
            raw = stream_llm_text(
                system=prompt["system"],
                user=render_template(
                    prompt["user_template"],
                    theme=theme,
                    writing_requirements=writing or "（无特别要求）",
                    genre=genre,
                    repair_payload=json.dumps(payload, ensure_ascii=False, indent=2),
                    retry_feedback=f"JSON 无法解析：{e}",
                ),
                temperature=temperature, max_tokens=tok,
                label=f"{label}_parse_retry",
                response_format=STEP3_OUTLINE_REPAIR_RESPONSE_FORMAT,
            )
            raw_path.write_text(raw, encoding="utf-8")
            replacements = parse_repair_replacements(raw)
        notes = splice_outline_replacements(tree, replacements, allowed_ids=allowed)
        for n in notes:
            print(f"[fix] {n}")

    leftover_fb = ""
    for i in range(1, max(1, int(rounds)) + 1):
        report = check_writing_outline(tree, brief)
        if not report.get("only_child_parents"):
            return report
        _one_round(i, leftover_fb)
        report = check_writing_outline(tree, brief)
        leftover_fb = "\n".join(f"- {x}" for x in (report.get("only_child_parents") or []))
        if not report.get("only_child_parents"):
            return report

    if report.get("only_child_parents"):
        print("[only-child] 局部修补后仍有残留，按规则拍平独生层（不整树重写）")
        for n in flatten_only_child_headings(tree)[:20]:
            print(f"[fix] {n}")
        report = check_writing_outline(tree, brief)
    return report


def _flatten_only_children(tree: dict, brief: dict) -> dict:
    """Kept for callers; prefer _repair_outline_only_children."""
    report = check_writing_outline(tree, brief)
    if not report.get("only_child_parents"):
        return report
    notes = flatten_only_child_headings(tree)
    for n in notes[:20]:
        print(f"[fix] {n}")
    if len(notes) > 20:
        print(f"[fix] …共拍平 {len(notes)} 处独生层")
    return check_writing_outline(tree, brief)


def _generate_writing_outline(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    notes: str,
    genre: str,
    library_data: dict,
    brief: dict,
    temperature: float,
    max_tokens: int,
) -> dict:
    """Phase A: thin heading tree. Parse retry ×1; only-child local repair; depth retry ×1."""
    prompt = load_prompt(_prompt_path(loc, "step3_writing_outline.yaml"))
    inventory = render_role_inventory_compact(library_data)
    (out_dir / "role_inventory.md").write_text(inventory, encoding="utf-8")
    heading_depth = _brief_recommend(brief, "heading_depth", "3")
    section_count = _brief_recommend(brief, "section_count", "5")

    def _gen(feedback: str, label: str) -> str:
        retry = ""
        if feedback.strip():
            retry = "## 上次未通过，请只改树\n" + feedback.strip()
        user = render_template(
            prompt["user_template"],
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            supplementary_notes=notes or "无",
            genre=genre,
            locale=loc,
            heading_depth=heading_depth,
            section_count=section_count,
            role_inventory=inventory,
            retry_feedback=retry or "（无）",
        )
        print(f"    [writing-outline] {label} max_tokens={int(max_tokens)}")
        return stream_llm_text(
            system=prompt["system"], user=user,
            temperature=temperature, max_tokens=int(max_tokens),
            label=label,
        )

    raw = _gen("", "writing_outline")
    (out_dir / "raw_writing_outline.txt").write_text(raw, encoding="utf-8")
    try:
        tree = parse_writing_outline(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[retry] 写作大纲解析失败: {e}")
        (out_dir / "raw_writing_outline.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(f"JSON 无法解析：{e}", "writing_outline_parse_retry")
        (out_dir / "raw_writing_outline.txt").write_text(raw, encoding="utf-8")
        tree = parse_writing_outline(raw)

    report = check_writing_outline(tree, brief)
    if report.get("only_child_parents"):
        report = _repair_outline_only_children(
            tree, out_dir=out_dir, loc=loc, theme=theme, writing=writing,
            genre=genre, brief=brief, temperature=temperature, max_tokens=max_tokens,
        )
    if not report.get("ok") and not _problems_are_only_child(report):
        print("[retry] 写作大纲结构未通过（深度/层级缺口），整树带反馈重试 1 次…")
        for p in report.get("problems") or []:
            print(f"       · {p}")
        (out_dir / "raw_writing_outline.attempt1.txt").write_text(raw, encoding="utf-8")
        fb = format_check_feedback(report, locale=loc)
        raw = _gen(fb, "writing_outline_check_retry")
        (out_dir / "raw_writing_outline.txt").write_text(raw, encoding="utf-8")
        tree = parse_writing_outline(raw)
        report = check_writing_outline(tree, brief)
        if report.get("only_child_parents"):
            report = _repair_outline_only_children(
                tree, out_dir=out_dir, loc=loc, theme=theme, writing=writing,
                genre=genre, brief=brief, temperature=temperature, max_tokens=max_tokens,
            )

    write_writing_outline_artifacts(tree, out_dir, brief)
    if not report.get("ok"):
        raise ValueError(
            "写作大纲检查仍未通过：" + "；".join(report.get("problems") or [])
        )
    print(
        f"[writing-outline] PASS  depth={report.get('used_depth')} "
        f"headings={report.get('n_heading_nodes')} "
        f"（无独生子）→ writing_outline.md"
    )
    return tree


def _load_cached_stylesheet(path: Path) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ss = data.get("stylesheet")
    if not isinstance(ss, list) or not ss:
        return None
    return data


def _generate_writing_stylesheet(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    notes: str,
    library_prompt: str,
    lib_path: Path,
    brief: dict,
    writing_outline: dict,
    chunks: list[dict],
    temperature: float,
    max_tokens: int,
) -> dict:
    """Phase B: closed stylesheet + Word sections. Parse retry ×1."""
    prompt = load_prompt(_prompt_path(loc, "step3_writing_stylesheet.yaml"))
    system = prompt["system"] + "\n\n" + taxonomy_brief(locale=loc)
    compact = format_writing_outline_compact(writing_outline, include_purpose=True)
    slots = format_chunk_slots(chunks)
    tok = min(int(max_tokens), 8192)

    def _gen(feedback: str, label: str) -> str:
        retry = ""
        if feedback.strip():
            retry = "## 上次未通过，请只改正 stylesheet / sections\n" + feedback.strip()
        user = render_template(
            prompt["user_template"],
            writing_outline=compact,
            section_slots=slots,
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            supplementary_notes=notes or "无",
            library_path=str(lib_path).replace("\\", "/"),
            structure_brief=brief_to_prompt_block(brief),
            role_library=library_prompt,
            retry_feedback=retry or "（无）",
        )
        print(f"    [stylesheet] {label}")
        return stream_llm_text(
            system=system, user=user, temperature=temperature, max_tokens=tok,
            label=label, response_format=STEP3_STYLESHEET_RESPONSE_FORMAT,
        )

    raw = _gen("", "writing_stylesheet")
    (out_dir / "raw_stylesheet.txt").write_text(raw, encoding="utf-8")
    try:
        payload = parse_stylesheet_json(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[retry] stylesheet 解析失败: {e}")
        (out_dir / "raw_stylesheet.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(f"JSON 无法解析：{e}", "writing_stylesheet_parse_retry")
        (out_dir / "raw_stylesheet.txt").write_text(raw, encoding="utf-8")
        payload = parse_stylesheet_json(raw)

    stylesheet = [x for x in (payload.get("stylesheet") or []) if isinstance(x, dict)]
    walked = walk_heading_nodes(writing_outline.get("_roots") or [])
    used_depth = max((lvl for _n, lvl, _k in walked), default=0)
    for msg in ensure_heading_styles(stylesheet, used_depth):
        print(f"[fix] {msg}")
    payload["stylesheet"] = stylesheet
    payload["selection"] = infer_selection_from_stylesheet(
        stylesheet, payload.get("selection") if isinstance(payload.get("selection"), dict) else {},
    )
    payload["sections"] = merge_sections_with_chunks(chunks, payload.get("sections"), stylesheet)
    payload["format_expectations"] = format_expectations_from_stylesheet(
        stylesheet, payload.get("format_expectations"),
    )
    if not isinstance(payload.get("media_style_diff"), list):
        payload["media_style_diff"] = []
    payload["meta"] = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    payload["meta"]["phase"] = "stylesheet"
    (out_dir / "writing_stylesheet.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(
        f"[stylesheet] styles={len(stylesheet)} sections={len(payload['sections'])} "
        f"→ writing_stylesheet.json"
    )
    return payload


def _load_cached_media_placement(path: Path, catalog: list[dict], node_index: dict) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("placements")
    if not isinstance(raw, list):
        return None
    placements, notes = normalize_media_placements(raw, catalog, node_index)
    catalog_ids = {str(r.get("style_id") or "") for r in catalog}
    covered = {str(x.get("style_id") or "") for x in placements}
    if catalog_ids - covered:
        return None
    data["placements"] = placements
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["n"] = len(placements)
    if notes:
        meta["spread_notes"] = notes
    data["meta"] = meta
    return data


def _load_cached_text_placement(
    path: Path,
    catalog: list[dict],
    node_index: dict,
    chunks: list[dict],
    stylesheet: list,
) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("placements")
    if not isinstance(raw, list):
        return None
    placements, notes = normalize_text_placements(raw, catalog, node_index)
    section_roles = normalize_section_roles(data.get("section_roles"), chunks, stylesheet)
    data["placements"] = placements
    data["section_roles"] = section_roles
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["n"] = len(placements)
    if notes:
        meta["normalize_notes"] = notes
    data["meta"] = meta
    return data


def _generate_text_placement(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    writing_outline: dict,
    stylesheet: list,
    chunks: list[dict],
    temperature: float,
    max_tokens: int,
    overwrite: bool = False,
) -> dict:
    """3c1: hang body/run styles on nodes; assign Sec* per Word slot. Coverage retry ×1 then record."""
    node_index = collect_outline_node_index(writing_outline)
    catalog = text_catalog_items(stylesheet)
    cache_path = out_dir / "writing_text_placement.json"
    if not overwrite:
        cached = _load_cached_text_placement(
            cache_path, catalog, node_index, chunks, stylesheet,
        )
        if cached is not None:
            miss = unplaced_text_styles(catalog, cached.get("placements") or [])
            print(
                f"[cache] 复用 writing_text_placement.json  "
                f"placements={len(cached.get('placements') or [])}"
                + (f" unplaced={miss}" if miss else "")
            )
            return cached

    if not catalog and not chunks:
        payload = {
            "section_roles": [],
            "placements": [],
            "meta": {"phase": "text_placement", "skipped": "empty"},
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print("[text] 无正文/run 待落位，跳过调用")
        return payload

    prompt = load_prompt(_prompt_path(loc, "step3_text_placement.yaml"))
    tree = format_writing_outline_with_ids(writing_outline, include_purpose=True)
    tok = min(int(max_tokens), 2048)
    none = "(none)" if str(loc).startswith("en") else "（无）"

    def _gen(feedback: str, label: str) -> str:
        retry = ""
        if feedback.strip():
            retry = ("## Previous attempt failed; fix section_roles / placements only\n"
                     if str(loc).startswith("en") else
                     "## 上次未通过，请只改正 section_roles / placements\n") + feedback.strip()
        user = render_template(
            prompt["user_template"],
            writing_outline=tree,
            section_slots=format_chunk_slots(chunks),
            section_catalog=format_section_style_catalog(stylesheet),
            text_catalog=format_text_catalog(stylesheet),
            heading_style_map=format_heading_style_map(stylesheet),
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            retry_feedback=retry or none,
        )
        print(f"    [text] {label}")
        return stream_llm_text(
            system=prompt["system"], user=user, temperature=temperature, max_tokens=tok,
            label=label, response_format=STEP3_TEXT_PLACEMENT_RESPONSE_FORMAT,
        )

    raw = _gen("", "writing_text_placement")
    (out_dir / "raw_text_placement.txt").write_text(raw, encoding="utf-8")
    try:
        parsed = parse_text_placement_json(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[retry] text placement 解析失败: {e}")
        (out_dir / "raw_text_placement.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(f"JSON parse error: {e}", "writing_text_placement_parse_retry")
        (out_dir / "raw_text_placement.txt").write_text(raw, encoding="utf-8")
        parsed = parse_text_placement_json(raw)

    placements, notes = normalize_text_placements(
        parsed.get("placements"), catalog, node_index,
    )
    missing = unplaced_text_styles(catalog, placements)
    if missing:
        fb = (
            "These style_id(s) are not placed yet; add placements with node_id from the tree: "
            if str(loc).startswith("en") else
            "以下 style_id 尚未落位，必须补上（node_id 只能用树上已有 id）："
        ) + ", ".join(missing)
        print(f"[retry] text placement 覆盖不全: {fb}")
        (out_dir / "raw_text_placement.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(fb, "writing_text_placement_cover_retry")
        (out_dir / "raw_text_placement.txt").write_text(raw, encoding="utf-8")
        try:
            parsed = parse_text_placement_json(raw)
        except Exception:  # noqa: BLE001
            parsed = {"placements": parsed.get("placements") or [], "section_roles": parsed.get("section_roles") or []}
        placements, notes = normalize_text_placements(
            parsed.get("placements"), catalog, node_index,
        )
        missing = unplaced_text_styles(catalog, placements)
        if missing:
            print(f"[warn] text placement 1 次重试后仍未落位（只记录，不中止）: {missing}")

    section_roles = normalize_section_roles(parsed.get("section_roles"), chunks, stylesheet)
    for msg in notes:
        print(f"[fix] text {msg}")
    payload = {
        "section_roles": section_roles,
        "placements": placements,
        "meta": {
            "phase": "text_placement",
            "n": len(placements),
            "unplaced": missing,
            "notes": notes,
        },
    }
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(
        f"[text] placements={len(placements)} section_roles={len(section_roles)} "
        f"unplaced={len(missing)} → writing_text_placement.json"
    )
    return payload


def _generate_media_placement(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    writing_outline: dict,
    stylesheet: list,
    temperature: float,
    max_tokens: int,
    overwrite: bool = False,
) -> dict:
    """Phase B′: hang Tbl*/Img* types onto outline nodes (reuse body styles). Retry this step only."""
    node_index = collect_outline_node_index(writing_outline)
    catalog = media_catalog_items(stylesheet)
    cache_path = out_dir / "writing_media_placement.json"
    if not overwrite:
        cached = _load_cached_media_placement(cache_path, catalog, node_index)
        if cached is not None:
            tbl_n, img_n = media_instance_totals(cached.get("placements") or [], catalog)
            cache_path.write_text(
                json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            print(
                f"[cache] 复用 writing_media_placement.json  "
                f"placements={len(cached.get('placements') or [])} tbl={tbl_n} img={img_n}"
            )
            return cached

    if not catalog:
        payload = {
            "placements": [],
            "meta": {"phase": "media_placement", "skipped": "no Tbl*/Img* in stylesheet"},
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print("[media] stylesheet 无 Tbl*/Img*，跳过落位调用")
        return payload

    prompt = load_prompt(_prompt_path(loc, "step3_media_placement.yaml"))
    tree = format_writing_outline_with_ids(writing_outline, include_purpose=True)
    cat_txt = format_media_catalog(stylesheet)
    tok = min(int(max_tokens), 2048)

    def _gen(feedback: str, label: str) -> str:
        retry = ""
        if feedback.strip():
            retry = "## 上次未通过，请只改正 placements\n" + feedback.strip()
        user = render_template(
            prompt["user_template"],
            writing_outline=tree,
            media_catalog=cat_txt,
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            retry_feedback=retry or "（无）",
        )
        print(f"    [media] {label}")
        return stream_llm_text(
            system=prompt["system"], user=user, temperature=temperature, max_tokens=tok,
            label=label, response_format=STEP3_MEDIA_RESPONSE_FORMAT,
        )

    raw = _gen("", "writing_media_placement")
    (out_dir / "raw_media_placement.txt").write_text(raw, encoding="utf-8")
    try:
        parsed = parse_media_placement_json(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[retry] media placement 解析失败: {e}")
        (out_dir / "raw_media_placement.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(f"JSON 无法解析：{e}", "writing_media_placement_parse_retry")
        (out_dir / "raw_media_placement.txt").write_text(raw, encoding="utf-8")
        parsed = parse_media_placement_json(raw)

    placements, notes = normalize_media_placements(
        parsed.get("placements"), catalog, node_index,
    )
    missing = {str(r.get("style_id") or "") for r in catalog} - {
        str(p.get("style_id") or "") for p in placements
    }
    if missing:
        fb = "以下 style_id 尚未落位，必须补上（node_id 只能用树上已有 id）：" + ", ".join(sorted(missing))
        print(f"[retry] media placement 覆盖不全: {fb}")
        (out_dir / "raw_media_placement.attempt1.txt").write_text(raw, encoding="utf-8")
        raw = _gen(fb, "writing_media_placement_cover_retry")
        (out_dir / "raw_media_placement.txt").write_text(raw, encoding="utf-8")
        try:
            parsed = parse_media_placement_json(raw)
        except Exception:  # noqa: BLE001
            parsed = {"placements": []}
        placements, notes = normalize_media_placements(
            parsed.get("placements"), catalog, node_index,
        )

    for msg in notes:
        print(f"[fix] media {msg}")
    payload = {
        "placements": placements,
        "meta": {"phase": "media_placement", "n": len(placements), "notes": notes},
    }
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    tbl_n, img_n = media_instance_totals(placements, catalog)
    print(
        f"[media] placements={len(placements)} tbl={tbl_n} img={img_n} "
        f"→ writing_media_placement.json"
    )
    return payload


def _generate_chunk_blocks(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    chunk: dict,
    stylesheet: list,
    temperature: float,
    max_tokens: int,
    media_must: str = "",
    text_must: str = "",
    required_placements: list | None = None,
    text_placements: list | None = None,
    media_catalog: list | None = None,
) -> dict:
    """3d: blocks for one frozen subtree. Parse/freeze/media/text/body-slot retry ×1 each then record."""
    prompt = load_prompt(_prompt_path(loc, "step3_writing_blocks.yaml"))
    iid = str(chunk.get("instance_id") or "I01")
    catalog = format_stylesheet_catalog(stylesheet)
    hmap = format_heading_style_map(stylesheet)
    seq = _heading_sequence_text(list(chunk.get("heading_signature") or []))
    tok = min(int(max_tokens), 8192)
    allowed = stylesheet_style_ids(stylesheet)
    body_fallback = first_body_style_id(stylesheet)
    must_txt = (media_must or "").strip() or (
        "(no required media placements)" if loc.startswith("en") else "（无强制媒体落位）"
    )
    text_must_txt = (text_must or "").strip() or (
        "(no required body/run placements)" if loc.startswith("en") else "（无强制正文/run 落位）"
    )
    required = [p for p in (required_placements or []) if isinstance(p, dict)]
    text_req = [p for p in (text_placements or []) if isinstance(p, dict)]

    def _gen(feedback: str, label: str) -> str:
        retry = ""
        if feedback.strip():
            retry = "## 上次未通过，请只改正本节 blocks\n" + feedback.strip()
        user = render_template(
            prompt["user_template"],
            instance_id=iid,
            chunk_kind=str(chunk.get("kind") or ""),
            chunk_title=str(chunk.get("title") or ""),
            chunk_tree=format_chunk_tree(chunk, include_purpose=True),
            heading_sequence=seq,
            stylesheet_catalog=catalog,
            heading_style_map=hmap,
            text_must=text_must_txt,
            media_must=must_txt,
            theme=theme,
            writing_requirements=writing or "（无特别要求）",
            retry_feedback=retry or "（无）",
        )
        print(f"    [blocks {iid}] {label}")
        return stream_llm_text(
            system=prompt["system"], user=user, temperature=temperature, max_tokens=tok,
            label=label, response_format=STEP3_BLOCKS_RESPONSE_FORMAT,
        )

    raw_path = step3_block_raw_path(out_dir, iid, mkdir=True)
    raw = _gen("", f"writing_blocks_{iid}")
    raw_path.write_text(raw, encoding="utf-8")
    try:
        payload = parse_blocks_json(raw, iid)
    except Exception as e:  # noqa: BLE001
        print(f"[retry] {iid} blocks 解析失败: {e}")
        step3_block_attempt_path(out_dir, iid, mkdir=True).write_text(raw, encoding="utf-8")
        raw = _gen(f"JSON 无法解析：{e}", f"writing_blocks_{iid}_parse_retry")
        raw_path.write_text(raw, encoding="utf-8")
        payload = parse_blocks_json(raw, iid)

    blocks = payload["blocks"]
    unknown = sorted({
        str(b.get("role_id") or "")
        for b in blocks
        if str(b.get("role_id") or "") not in allowed
    } - {""})
    ok, reason = _chunk_signature_ok(chunk, blocks, stylesheet)
    _warn_heading_drift(iid, ok, reason)
    if unknown:
        fb = f"以下 role_id 不在冻结 stylesheet 中，必须改挂目录内 style：{unknown}"
        print(f"[retry] {iid} {fb}")
        step3_block_attempt_path(out_dir, iid, mkdir=True).write_text(raw, encoding="utf-8")
        raw = _gen(fb, f"writing_blocks_{iid}_freeze_retry")
        raw_path.write_text(raw, encoding="utf-8")
        payload = parse_blocks_json(raw, iid)
        blocks = payload["blocks"]
        ok, reason = _chunk_signature_ok(chunk, blocks, stylesheet)
        _warn_heading_drift(iid, ok, reason)
        for b in blocks:
            rid_ = str(b.get("role_id") or "")
            if rid_ and rid_ not in allowed:
                b["role_id"] = body_fallback

    miss = missing_required_media(blocks, required)
    if miss:
        names = ", ".join(f"{m['style_id']}(have {m['have']}/{m['need']})" for m in miss)
        fb = f"本章必须挂上但尚未出现的表/图：{names}。按 need 补够对应块（同一 style 可多处；正文可加题注）。"
        print(f"[retry] {iid} media-must {fb}")
        step3_block_attempt_path(out_dir, iid, mkdir=True).write_text(raw, encoding="utf-8")
        raw = _gen(fb, f"writing_blocks_{iid}_media_retry")
        raw_path.write_text(raw, encoding="utf-8")
        payload = parse_blocks_json(raw, iid)
        blocks = payload["blocks"]
        ok, reason = _chunk_signature_ok(chunk, blocks, stylesheet)
        _warn_heading_drift(iid, ok, reason)
        for b in blocks:
            rid_ = str(b.get("role_id") or "")
            if rid_ and rid_ not in allowed:
                b["role_id"] = body_fallback
        miss = missing_required_media(blocks, required)
        if miss:
            n_add = inject_missing_media_blocks(blocks, miss, media_catalog or [])
            print(f"[fix] {iid} 补挂 {n_add} 个必须表/图块")

    text_miss: list[str] = missing_required_text(blocks, text_req)
    if text_miss:
        fb = (
            f"本章 3c1 必须挂上但尚未出现的正文/run：{text_miss}。"
            "Para* 写成块的 role_id；Run* 只进某段 expected_runs，不要单独成块。"
        )
        print(f"[retry] {iid} text-must {fb}")
        step3_block_attempt_path(out_dir, iid, mkdir=True).write_text(raw, encoding="utf-8")
        raw = _gen(fb, f"writing_blocks_{iid}_text_retry")
        raw_path.write_text(raw, encoding="utf-8")
        payload = parse_blocks_json(raw, iid)
        blocks = payload["blocks"]
        for b in blocks:
            rid_ = str(b.get("role_id") or "")
            if rid_ and rid_ not in allowed:
                b["role_id"] = body_fallback
        text_miss = missing_required_text(blocks, text_req)
        if text_miss:
            n_add = inject_missing_text_styles(blocks, text_miss)
            print(f"[fix] {iid} 程序补 {n_add} 个正文/run（3c1 must）")
            text_miss = missing_required_text(blocks, text_req)
            if text_miss:
                print(f"[warn] {iid} 1 次重试后仍缺正文/run（只记录）: {text_miss}")

    from LongDocFmtEval.pipeline_auto.scripts.errors_clean.plan_body_slots import (  # noqa: PLC0415
        format_body_slot_retry_feedback,
        hard_body_slot_hits,
    )

    slot_hits = hard_body_slot_hits(blocks, stylesheet)
    if slot_hits:
        fb_core = format_body_slot_retry_feedback(slot_hits)
        blocks_json = json.dumps(blocks, ensure_ascii=False, indent=2)
        fb = (
            f"{fb_core}\n\n"
            f"## 上次生成的 blocks（共 {len(blocks)} 块，请在此基础上重写/补块）\n"
            f"{blocks_json}"
        )
        print(f"[retry] {iid} body-slot 硬规则 {len(slot_hits)} 处")
        step3_block_attempt_path(out_dir, iid, mkdir=True).write_text(raw, encoding="utf-8")
        raw = _gen(fb, f"writing_blocks_{iid}_body_slot_retry")
        raw_path.write_text(raw, encoding="utf-8")
        payload = parse_blocks_json(raw, iid)
        blocks = payload["blocks"]
        for b in blocks:
            rid_ = str(b.get("role_id") or "")
            if rid_ and rid_ not in allowed:
                b["role_id"] = body_fallback
        ok, reason = _chunk_signature_ok(chunk, blocks, stylesheet)
        _warn_heading_drift(iid, ok, reason)
        slot_hits = hard_body_slot_hits(blocks, stylesheet)
        if slot_hits:
            print(f"[warn] {iid} body-slot 1 次重试后仍 {len(slot_hits)} 处硬违规（只记录）")
            for _rule, detail, _bids in slot_hits[:8]:
                print(f"    {detail}")

    uniquify_block_ids(iid, blocks)
    payload = {"instance_id": iid, "blocks": blocks}
    if text_miss:
        payload["meta"] = {"text_must_unplaced": text_miss}
    step3_block_json_path(out_dir, iid, mkdir=True).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    n_h = len(chunk.get("heading_signature") or [])
    print(f"[blocks] {iid} → blocks/{iid}.json  blocks={len(blocks)} headings={n_h}")
    return payload


def _assemble_plan(
    *,
    writing_outline: dict,
    sheet: dict,
    chunk_outlines: list[dict],
    media_placement: dict | None = None,
    text_placement: dict | None = None,
) -> dict:
    meta = dict(sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}
    meta["writing_mode"] = "outline_stylesheet_then_blocks"
    wo = writing_outline.get("meta") if isinstance(writing_outline.get("meta"), dict) else {}
    if wo.get("theme"):
        meta.setdefault("theme", wo.get("theme"))
    if wo.get("genre"):
        meta.setdefault("genre", wo.get("genre"))
    if wo.get("locale"):
        meta.setdefault("locale", wo.get("locale"))
    unplaced = []
    if isinstance(text_placement, dict):
        unplaced = list((text_placement.get("meta") or {}).get("unplaced") or [])
    if unplaced:
        meta["text_placement_unplaced"] = unplaced
    plan = {
        "meta": meta,
        "selection": sheet.get("selection") or {},
        "stylesheet": sheet.get("stylesheet") or [],
        "sections": sheet.get("sections") or [],
        "outline": chunk_outlines,
        "format_expectations": sheet.get("format_expectations") or [],
        "media_style_diff": sheet.get("media_style_diff") or [],
        "media_placement": (media_placement or {}).get("placements") or [],
        "text_placement": (text_placement or {}).get("placements") or [],
    }
    apply_section_page_continuity(plan)
    drop_section_role_blocks(plan)
    return plan


def _try_assemble_from_parts(out_dir: Path, writing_outline: dict | None = None) -> dict | None:
    migrate_step3_block_files(out_dir)
    sheet = _load_cached_stylesheet(out_dir / "writing_stylesheet.json")
    if not sheet:
        return None
    wo = writing_outline or load_writing_outline(out_dir / "writing_outline.json")
    if not wo:
        return None
    chunks = split_outline_chunks(wo)
    outlines: list[dict] = []
    for ch in chunks:
        iid = ch["instance_id"]
        p = resolve_step3_block_json(out_dir, iid)
        if p is None:
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
            return None
        outlines.append({"instance_id": iid, "blocks": data["blocks"]})
    media = None
    mp = out_dir / "writing_media_placement.json"
    if mp.is_file():
        try:
            media = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            media = None
    text = None
    tp = out_dir / "writing_text_placement.json"
    if tp.is_file():
        try:
            text = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            text = None
    return _assemble_plan(
        writing_outline=wo, sheet=sheet, chunk_outlines=outlines,
        media_placement=media, text_placement=text,
    )


def _generate_writing_plan(
    *,
    out_dir: Path,
    loc: str,
    theme: str,
    writing: str,
    notes: str,
    library_prompt: str,
    lib_path: Path,
    brief: dict,
    writing_outline: dict,
    temperature: float,
    max_tokens: int,
    overwrite: bool = False,
) -> tuple[str, dict]:
    """3b stylesheet + 3c1 text placement + 3c2 media + 3d per-chapter blocks → plan.json."""
    for msg in migrate_step3_block_files(out_dir):
        print(f"[migrate] {msg}")
    chunks = split_outline_chunks(writing_outline)
    if not chunks:
        raise ValueError("写作大纲无法切分成 Word 节")
    step3_chunks_path(out_dir, mkdir=True).write_text(
        json.dumps(
            [
                {
                    "instance_id": ch["instance_id"],
                    "kind": ch["kind"],
                    "title": ch["title"],
                    "node_id": ch.get("node_id"),
                    "n_headings": len(ch.get("heading_signature") or []),
                }
                for ch in chunks
            ],
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"[chunks] {len(chunks)} 节（前置/H1/后置）→ blocks/chunks.json")

    sheet = None if overwrite else _load_cached_stylesheet(out_dir / "writing_stylesheet.json")
    sheet_cached = bool(sheet and isinstance(sheet.get("sections"), list) and sheet["sections"])
    if sheet_cached:
        print("[cache] 复用 writing_stylesheet.json")
        stylesheet = [x for x in (sheet.get("stylesheet") or []) if isinstance(x, dict)]
        sheet["sections"] = merge_sections_with_chunks(chunks, sheet.get("sections"), stylesheet)
        sheet["selection"] = infer_selection_from_stylesheet(stylesheet, sheet.get("selection"))
    else:
        sheet = _generate_writing_stylesheet(
            out_dir=out_dir, loc=loc, theme=theme, writing=writing, notes=notes,
            library_prompt=library_prompt, lib_path=lib_path, brief=brief,
            writing_outline=writing_outline, chunks=chunks,
            temperature=temperature, max_tokens=max_tokens,
        )

    stylesheet = [x for x in (sheet.get("stylesheet") or []) if isinstance(x, dict)]
    text_pl = _generate_text_placement(
        out_dir=out_dir, loc=loc, theme=theme, writing=writing,
        writing_outline=writing_outline, stylesheet=stylesheet, chunks=chunks,
        temperature=temperature, max_tokens=max_tokens,
        overwrite=overwrite or not sheet_cached,
    )
    apply_section_roles_to_sheet(sheet, text_pl.get("section_roles") or [])
    (out_dir / "writing_stylesheet.json").write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    media = _generate_media_placement(
        out_dir=out_dir, loc=loc, theme=theme, writing=writing,
        writing_outline=writing_outline, stylesheet=stylesheet,
        temperature=temperature, max_tokens=max_tokens,
        overwrite=overwrite or not sheet_cached,
    )
    placements = [p for p in (media.get("placements") or []) if isinstance(p, dict)]
    text_rows = [p for p in (text_pl.get("placements") or []) if isinstance(p, dict)]
    node_index = collect_outline_node_index(writing_outline)
    media_cat = media_catalog_items(stylesheet)
    outline_items: list[dict] = []
    raw_parts: list[str] = []
    ss_raw = out_dir / "raw_stylesheet.txt"
    if ss_raw.is_file():
        raw_parts.append(ss_raw.read_text(encoding="utf-8"))
    tp_raw = out_dir / "raw_text_placement.txt"
    if tp_raw.is_file():
        raw_parts.append(tp_raw.read_text(encoding="utf-8"))
    mp_raw = out_dir / "raw_media_placement.txt"
    if mp_raw.is_file():
        raw_parts.append(mp_raw.read_text(encoding="utf-8"))

    for ch in chunks:
        iid = ch["instance_id"]
        chunk_pl = placements_for_chunk(placements, ch)
        chunk_text = placements_for_chunk(text_rows, ch)
        must_txt = format_chunk_media_must(chunk_pl, node_index, locale=loc)
        text_must_txt = format_chunk_text_must(chunk_text, node_index, locale=loc)
        cached = None
        if not overwrite:
            bp = resolve_step3_block_json(out_dir, iid)
            if bp is not None:
                try:
                    cached = json.loads(bp.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cached = None
            if isinstance(cached, dict) and isinstance(cached.get("blocks"), list):
                miss = missing_required_media(cached["blocks"], chunk_pl)
                text_miss = missing_required_text(cached["blocks"], chunk_text)
                from LongDocFmtEval.pipeline_auto.scripts.errors_clean.plan_body_slots import (  # noqa: PLC0415
                    hard_body_slot_hits,
                )
                slot_hits = hard_body_slot_hits(cached["blocks"], stylesheet)
                if not miss and not text_miss and not slot_hits:
                    ok, reason = _chunk_signature_ok(ch, cached["blocks"], stylesheet)
                    _warn_heading_drift(iid, ok, reason)
                    print(f"[cache] 复用 blocks/{iid}.json")
                    outline_items.append({"instance_id": iid, "blocks": cached["blocks"]})
                    continue
                if slot_hits:
                    print(
                        f"[cache-invalid] blocks/{iid}.json 标题槽位硬规则未过"
                        f"（{len(slot_hits)} 处），重跑 3d"
                    )
        item = _generate_chunk_blocks(
            out_dir=out_dir, loc=loc, theme=theme, writing=writing,
            chunk=ch, stylesheet=stylesheet,
            temperature=temperature, max_tokens=max_tokens,
            media_must=must_txt, text_must=text_must_txt,
            required_placements=chunk_pl, text_placements=chunk_text,
            media_catalog=media_cat,
        )
        outline_items.append(item)
        rp = resolve_step3_block_raw(out_dir, iid)
        if rp is not None:
            raw_parts.append(rp.read_text(encoding="utf-8"))

    plan = _assemble_plan(
        writing_outline=writing_outline, sheet=sheet, chunk_outlines=outline_items,
        media_placement=media, text_placement=text_pl,
    )
    frozen_sig = heading_signature(writing_outline)
    ok, reason = signatures_compatible(frozen_sig, heading_signature_from_plan(plan))
    _warn_heading_drift("plan", ok, reason)
    leftover = [
        r["style_id"] for r in media_cat
        if r["style_id"] not in {
            str(b.get("role_id") or "")
            for item in outline_items
            for b in (item.get("blocks") or [])
        }
    ]
    if leftover:
        print(f"[warn] 拼接后仍未实例化的 Tbl*/Img*: {leftover}")
    raw = "\n\n".join(raw_parts) if raw_parts else json.dumps(plan, ensure_ascii=False)
    (out_dir / "raw.txt").write_text(raw, encoding="utf-8")
    print("[writing-plan] stylesheet + 文本落位 + 媒体落位 + 分章 blocks 已拼接")
    return raw, plan



def _keep_by_layer(selection: dict) -> dict[str, set]:
    tbl = selection.get("table_roles") or []
    return {
        "section": set(selection.get("section_roles") or []),
        "paragraph.body": set(selection.get("paragraph_body_roles") or []),
        "paragraph.table_cell": set(selection.get("paragraph_cell_roles") or []),
        "table": {t.get("role_id") for t in tbl if isinstance(t, dict)} | {t for t in tbl if isinstance(t, str)},
        "image": set(selection.get("image_roles") or []),
        "paragraph_runs": set(selection.get("run_roles") or []),
    }


def _enforce_cover_formal_head_xor(plan: dict) -> list[str]:
    """Drop SecFormalHead when SecCover is also present (hard exclusivity)."""
    notes: list[str] = []
    sections = plan.get("sections") or []
    if not isinstance(sections, list):
        return notes
    cover_ids = {
        s.get("instance_id")
        for s in sections
        if isinstance(s, dict) and s.get("role_id") == "SecCover" and s.get("instance_id")
    }
    head_ids = {
        s.get("instance_id")
        for s in sections
        if isinstance(s, dict) and s.get("role_id") == "SecFormalHead" and s.get("instance_id")
    }
    if not (cover_ids and head_ids):
        return notes

    plan["sections"] = [
        s for s in sections
        if not (isinstance(s, dict) and s.get("role_id") == "SecFormalHead")
    ]
    outline = plan.get("outline") or []
    if isinstance(outline, list):
        plan["outline"] = [
            o for o in outline
            if not (isinstance(o, dict) and o.get("instance_id") in head_ids)
        ]
    selection = plan.setdefault("selection", {})
    if isinstance(selection, dict):
        roles = selection.get("section_roles") or []
        if isinstance(roles, list):
            selection["section_roles"] = [r for r in roles if r != "SecFormalHead"]
        sn = selection.setdefault("notes", [])
        if isinstance(sn, list):
            sn.append("已强制去掉 SecFormalHead（与 SecCover 互斥，禁止重复题名+信息表）")
    stylesheet = plan.get("stylesheet")
    if isinstance(stylesheet, list):
        plan["stylesheet"] = [
            s for s in stylesheet
            if not (isinstance(s, dict) and s.get("style_id") == "SecFormalHead")
        ]
    meta = plan.setdefault("meta", {})
    if isinstance(meta, dict):
        applied = meta.setdefault("notes_applied", [])
        if isinstance(applied, list):
            applied.append("cover_formal_head_xor: kept SecCover, dropped SecFormalHead")
    notes.append(
        f"互斥纠偏：保留 SecCover，删除 SecFormalHead 节 {sorted(head_ids)}"
    )
    return notes


def run_one(genre: str, locale: str, theme: str, writing: str, notes: str,
            library: str | None, temperature: float | None, max_tokens: int,
            *, step1_dir: Path | None = None, overwrite: bool = False,
            theme_key: str | None = None, genre_id: int | None = None,
            skip_brief: bool = False, continue_raw: bool = False,
            out_dir: Path | None = None,
            prior_outline_briefs: str = "",
            structure_brief: dict | None = None,
            complexity: str = "") -> bool:
    temperature = temperature if temperature is not None else 0.3
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    # 目录：{id:02d}_{genre}_{locale}__themeN（与 step1 前缀对齐）
    key = (theme_key or "").strip() or "theme1"
    m = re.search(r"(\d+)$", key)
    theme_n = int(m.group(1)) if m else 1
    slug = outline_dir_name(genre_id, genre, loc, theme_n)
    out_dir = Path(out_dir) if out_dir else (OUTLINES_DIR / slug)
    out_dir = out_dir.resolve()
    slug = out_dir.name
    plan_path = out_dir / "plan.json"
    if prior_outline_briefs:
        extra = (
            "\n\n## 同 theme 下已生成大纲结构摘要（须明显不同构）\n"
            "禁止再生成节序雷同、表图类型集合几乎相同、深度轮廓相同的大纲；"
            "允许题材措辞相近，但分节切分、表图分工、横向节/附录形态须可区分。\n"
            f"{prior_outline_briefs.strip()}\n"
        )
        notes = (notes or "无") + extra

    if plan_path.exists() and not overwrite and not continue_raw:
        print(f"\n=== step3 写作大纲 + stylesheet + 媒体落位 + 分章块: {slug} ===")
        try:
            cached = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cached = {}
        n_sec = len((cached or {}).get("sections") or [])
        n_blk = sum(len(o.get("blocks") or []) for o in ((cached or {}).get("outline") or []))
        n_sty = len((cached or {}).get("stylesheet") or []) if isinstance((cached or {}).get("stylesheet"), list) else 0
        ok_c, reason_c = _outline_plan_ok(cached or {}, n_sections=n_sec, n_blocks=n_blk, n_styles=n_sty)
        if not ok_c:
            print(f"[cache-invalid] 已有 plan 无效（{reason_c}），将重跑（等同缺产物）")
            # fall through to regenerate
        else:
            print(f"[cache] 已存在，跳过：{out_dir}（--overwrite 可强制重跑）")
            tree = out_dir / "outline_tree.md"
            if not tree.exists():
                try:
                    from LongDocFmtEval.pipeline_auto.scripts.viz_outline import write_outline_tree
                    write_outline_tree(cached, tree)
                    print(f"[browse] 补写 {tree.name}")
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 补写 outline_tree.md 失败: {e}")
            metrics = out_dir / "outline_metrics.json"
            if not metrics.is_file():
                try:
                    from LongDocFmtEval.pipeline_auto.seed.outline_metrics import write_outline_metrics
                    write_outline_metrics(cached or {}, out_dir)
                    print("[record] 补写 outline_metrics.json")
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 补写 outline_metrics 失败: {e}")
            return True

    lib_path = resolve_library(genre, loc, library, step1_dir=step1_dir)
    library_prompt, library_data = load_role_library_prompt(lib_path)

    print(f"\n=== step3 写作大纲 + stylesheet + 媒体落位 + 分章块: {slug} ===")
    print(f"[library] {lib_path}  （大纲用简易清单；stylesheet 注入完整 Role 库；blocks 只用冻结目录）")
    print(f"[theme] {theme[:60]}{'…' if len(theme) > 60 else ''}")

    from LongDocFmtEval.common.llm_usage import print_usage_summary, usage_scope, write_step_usage_report  # noqa: PLC0415
    step_label = f"step3:{complexity or key}"

    with usage_scope(step_label) as _usage_rows:
        if continue_raw:
            brief = load_brief(out_dir / "structure_brief.json") or {}
            assembled = _try_assemble_from_parts(out_dir)
            if assembled and _outline_payload_ok(assembled):
                print("[continue-raw] 从 writing_stylesheet.json + writing_media_placement.json + blocks/*.json 拼接 plan")
                plan = assembled
            else:
                raw_path = out_dir / "raw.txt"
                if not raw_path.is_file():
                    print(f"[FAIL] --continue-raw 但缺少 {raw_path} 且无法从分件拼接")
                    return False
                print(f"[continue-raw] 复用已有 raw.txt（{raw_path.stat().st_size} bytes），不重跑大纲 LLM")
                raw = raw_path.read_text(encoding="utf-8")
                try:
                    plan = parse_outline_json(raw)
                except Exception as e:  # noqa: BLE001
                    _write_step3_parse_error(
                        out_dir,
                        error=str(e),
                        extra={"raw_chars": len(raw or ""), "recover_strategies": []},
                    )
                    print(f"[FAIL] 解析失败（--continue-raw 不重生成）: {e}")
                    write_step_usage_report(out_dir, _usage_rows, step=step_label)
                    return False
        else:
            brief = _write_structure_brief(
                out_dir,
                loc=loc,
                theme=theme,
                writing=writing or "",
                notes=notes or "",
                library_prompt=library_prompt,
                lib_path=lib_path,
                genre=genre,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                skip_llm=bool(skip_brief) or bool(structure_brief) or bool(complexity),
                structure_brief=structure_brief,
                complexity=complexity or (theme_key or "").upper(),
            )

            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                cached_tree = load_writing_outline(out_dir / "writing_outline.json")
                if cached_tree:
                    cached_report = check_writing_outline(cached_tree, brief)
                    if cached_report.get("ok"):
                        writing_outline = cached_tree
                        print("[cache] 复用已通过检查的 writing_outline.json（改树请删该文件）")
                    elif _problems_are_only_child(cached_report):
                        print("[cache] 已有大纲仅独生子不合格，局部修补（不整树重写）")
                        cached_report = _repair_outline_only_children(
                            cached_tree, out_dir=out_dir, loc=loc, theme=theme,
                            writing=writing or "", genre=genre, brief=brief,
                            temperature=float(temperature), max_tokens=int(max_tokens),
                        )
                        write_writing_outline_artifacts(cached_tree, out_dir, brief)
                        if cached_report.get("ok"):
                            writing_outline = cached_tree
                            print("[cache] 独生子局部修补通过")
                        else:
                            cached_tree = None
                    else:
                        cached_tree = None
                if not cached_tree:
                    writing_outline = _generate_writing_outline(
                        out_dir=out_dir, loc=loc, theme=theme, writing=writing or "",
                        notes=notes or "", genre=genre, library_data=library_data,
                        brief=brief, temperature=float(temperature), max_tokens=int(max_tokens),
                    )
                _raw, plan = _generate_writing_plan(
                    out_dir=out_dir, loc=loc, theme=theme, writing=writing or "",
                    notes=notes or "", library_prompt=library_prompt, lib_path=lib_path,
                    brief=brief, writing_outline=writing_outline,
                    temperature=float(temperature), max_tokens=int(max_tokens),
                    overwrite=bool(overwrite),
                )
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "写作大纲" in msg:
                    stage = "writing_outline"
                elif "stylesheet" in msg.lower() or "样式" in msg:
                    stage = "writing_stylesheet"
                elif "text" in msg.lower() or "正文" in msg:
                    stage = "writing_text_placement"
                elif "media" in msg.lower() or "落位" in msg:
                    stage = "writing_media_placement"
                else:
                    stage = "writing_blocks"
                err = {
                    "ok": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "stage": stage,
                    "marker": STEP3_FAIL_MARKER,
                }
                (out_dir / "error.json").write_text(
                    json.dumps(err, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
                print(f"[FAIL] {stage} 失败（已写 error.json）: {e}")
                try:
                    write_step_usage_report(out_dir, _usage_rows, step=step_label)
                except Exception:  # noqa: BLE001
                    pass
                return False

        # Record provenance so downstream knows inputs came from Step1
        meta = plan.setdefault("meta", {})
        if isinstance(meta, dict):
            if genre_id is not None:
                meta["genre_id"] = int(genre_id)
            meta.setdefault("genre", genre)
            meta.setdefault("locale", loc)
            meta.setdefault("theme", theme)
            meta["theme_key"] = f"theme{theme_n}"
            meta.setdefault("writing_requirements", writing)
            meta["role_library_path"] = str(lib_path).replace("\\", "/")
            meta["role_library_usage"] = library_data.get("usage", "reference")
            meta["outline_dir"] = slug
            meta["writing_mode"] = "outline_stylesheet_then_blocks"
            wo = load_writing_outline(out_dir / "writing_outline.json")
            if wo:
                meta["writing_outline_headings"] = heading_signature(wo)

        for msg in _enforce_cover_formal_head_xor(plan):
            print(f"[fix] {msg}")

        try:
            from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import prune_empty_sections
            for msg in prune_empty_sections(plan):
                print(f"[fix] {msg}")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] prune_empty_sections 失败: {e}")

        try:
            from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import write_abstract_check
            abs_report = write_abstract_check(plan, out_dir, library_data)
            abs_status = "PASS" if abs_report.get("ok") else "FAIL"
            print(
                f"[abstract-check] {abs_status}  "
                f"notes={abs_report.get('n_notes')}  "
                f"remaining={abs_report.get('remaining') or '—'}"
            )
            for msg in (abs_report.get("notes") or [])[:8]:
                print(f"[abstract-check] {msg}")
            print("[browse] abstract_check.md  ← 剔除 abstract *Base / 节族根，改挂叶子")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] strip_abstract_roles 失败: {e}")
            abs_report = {}

        try:
            from LongDocFmtEval.pipeline_auto.seed.caption_bind import write_caption_check
            cap_report = write_caption_check(plan, out_dir)
            cap_status = "PASS" if cap_report.get("ok") else "FAIL"
            print(
                f"[caption-check] {cap_status}  "
                f"required={cap_report.get('required')}  "
                f"inserted={cap_report.get('n_inserted')}  "
                f"exempt={cap_report.get('n_exempt')}"
            )
            for msg in (cap_report.get("notes") or [])[:8]:
                print(f"[caption-check] {msg}")
            print("[browse] caption_check.md  ← 正文表/图题注绑定（封面 logo 禁止题注）")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] caption_bind 失败: {e}")
            cap_report = {}

        try:
            scope = trim_stylesheet_to_used(plan, library=library_data)
            scope["text_placement_unplaced"] = list(
                (plan.get("meta") or {}).get("text_placement_unplaced") or []
            )
            scope["ok"] = True
            (out_dir / "stylesheet_scope.json").write_text(
                json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            dropped = scope.get("dropped") or []
            if dropped:
                print(f"[scope] 3e 未在 blocks 使用、已从 stylesheet 去掉 {len(dropped)} 项: {dropped}")
            else:
                print("[scope] 3e stylesheet 与 blocks 使用集一致")
            leftover_place = scope.get("text_placement_unplaced") or []
            if leftover_place:
                print(f"[scope] 3c1 重试后仍未落位（只记录）: {leftover_place}")
            print("[browse] stylesheet_scope.json  ← 使用集收口（step4/5/6 认 plan.json 里这份）")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] stylesheet 3e 收口失败: {e}")

        brief_loaded = load_brief(out_dir / "structure_brief.json") or brief
        heading_report = write_heading_check(plan, brief_loaded, out_dir)
        want_d = heading_report.get("recommend_heading_depth")
        have_d = heading_report.get("used_depth")
        status = "PASS" if heading_report.get("ok") else "FAIL"
        print(
            f"[heading-check] {status}  used_depth={have_d}  "
            f"step2建议={want_d}  levels={heading_report.get('levels_present')}"
        )
        for p in heading_report.get("problems") or []:
            print(f"[heading-check] {p}")
        print("[browse] heading_check.md  ← stylesheet heading_level vs step2 深度（不核验 block 数）")
        if isinstance(meta, dict):
            meta["structure_brief"] = {
                "recommend": brief_loaded.get("recommend"),
                "heading_depth_target": brief_loaded.get("heading_depth_target"),
            }
            meta["heading_check"] = {
                "ok": heading_report.get("ok"),
                "used_depth": have_d,
                "recommend_heading_depth": want_d,
                "problems": heading_report.get("problems") or [],
            }
            if abs_report:
                meta["abstract_check"] = {
                    "ok": abs_report.get("ok"),
                    "remaining": abs_report.get("remaining") or [],
                    "n_notes": abs_report.get("n_notes"),
                }
            if cap_report:
                meta["caption_check"] = {
                    "ok": cap_report.get("ok"),
                    "required": cap_report.get("required"),
                    "inserted": cap_report.get("n_inserted"),
                    "exempt": cap_report.get("n_exempt"),
                    "missing": cap_report.get("missing") or [],
                }

        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from LongDocFmtEval.pipeline_auto.outline_brief import write_outline_brief
            write_outline_brief(plan, out_dir, outline_id=slug)
            print(f"[browse] outline_brief.md  ← 结构摘要（供下一 outline 避让）")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 outline_brief 失败: {e}")

        try:
            from LongDocFmtEval.pipeline_auto.seed.outline_metrics import write_outline_metrics
            om = write_outline_metrics(plan, out_dir)
            c = om.get("counts") or {}
            print(
                f"[record] outline_metrics.json  "
                f"blocks={c.get('n_blocks')} body={c.get('n_body')} "
                f"H={c.get('heading_counts')} "
                f"bridge={((om.get('bridge') or {}).get('bridge_rate'))}"
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 outline_metrics 失败: {e}")

        try:
            from LongDocFmtEval.pipeline_auto.scripts.viz_outline import write_outline_tree
            tree_path = write_outline_tree(plan, out_dir / "outline_tree.md")
            print(f"[browse] {tree_path.name}  ← 写作层次树（blocks）")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 outline_tree.md 失败: {e}")

        try:
            from LongDocFmtEval.pipeline_auto.seed.media_style_diff import write_media_style_diff
            diff_path = write_media_style_diff(plan, out_dir / "media_style_diff.md")
            print(f"[browse] {diff_path.name}  ← 表/图 style 区分理由（审阅）")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 media_style_diff.md 失败: {e}")

        stylesheet = plan.get("stylesheet")
        if isinstance(stylesheet, list) and stylesheet:
            (out_dir / "stylesheet.json").write_text(
                json.dumps(stylesheet, ensure_ascii=False, indent=2), encoding="utf-8")

        selection = plan.get("selection") or {}
        keep = _keep_by_layer(selection)
        counts = {k: len(v) for k, v in keep.items()}
        n_sections = len(plan.get("sections") or [])
        n_blocks = sum(len(o.get("blocks") or []) for o in (plan.get("outline") or []))
        n_styles = len(stylesheet) if isinstance(stylesheet, list) else 0

        try:
            pruned = prune(library_data, keep)
            pruned_path = out_dir / "selected_library.yaml"
            pruned_path.write_text(
                yaml.safe_dump(pruned, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(Path(__file__).with_name("viz_role_library.py")),
                 str(pruned_path), str(out_dir / "selected_tree.md")], check=False)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 参考角色树可视化失败: {e}")

        print(f"[out] {out_dir}")
        n_styles = len(stylesheet) if isinstance(stylesheet, list) else 0
        layer_counts = normalize_keep_counts(counts)
        print(
            f"[plan] sections={n_sections} blocks={n_blocks} stylesheet_styles={n_styles} "
            f"| ref_roles={format_ref_roles(layer_counts)}"
        )
        n_tbl = n_img = 0
        obj_by_id = {
            str(s.get("style_id") or ""): str(s.get("object") or "")
            for s in (stylesheet or [])
            if isinstance(s, dict)
        }
        for o in plan.get("outline") or []:
            for b in o.get("blocks") or []:
                if not isinstance(b, dict):
                    continue
                rid = str(b.get("role_id") or "")
                obj = obj_by_id.get(rid) or (
                    "table" if rid.startswith("Tbl") else "image" if rid.startswith("Img") else ""
                )
                if obj == "table":
                    n_tbl += 1
                elif obj == "image":
                    n_img += 1
        print(f"[plan] media_blocks tables={n_tbl} figures={n_img}")
        if n_sections >= 4 and (n_tbl + n_img) < max(3, n_sections - 3):
            print(
                f"[hint] 表/图块偏少（tables={n_tbl} figures={n_img} / sections={n_sections}）；"
                "正文 H1 章宜各有表或图；template 每种 Img* 最多示范 2 张实例（不限制 style 种数）"
            )
        try:
            payload = layers_payload(
                selection=selection,
                stylesheet=stylesheet,
                keep_counts=counts,
            )
            (out_dir / "style_layers.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 style_layers.json 失败: {e}")
        for note in (selection.get("notes") or [])[:6]:
            print(f"   · {note}")

        try:
            report = write_step_usage_report(out_dir, _usage_rows, step=step_label)
            print_usage_summary(report["summary"], prefix="[step3 tokens]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 llm_usage.json 失败: {e}")

        ok, reason = _outline_plan_ok(plan, n_sections=n_sections, n_blocks=n_blocks, n_styles=n_styles)
        if not ok:
            err = {
                "step": 3,
                "ok": False,
                "reason": reason,
                "sections": n_sections,
                "blocks": n_blocks,
                "stylesheet_styles": n_styles,
                "ref_roles": layer_counts,
                "outline_dir": str(out_dir).replace("\\", "/"),
            }
            (out_dir / "error.json").write_text(
                json.dumps(err, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            print(f"[FAIL] outline 无效，已写入 error.json：{reason}")
            print("      不进入后续 template/document；请检查 raw.txt / 网关空响应后 --overwrite 重跑。")
            return False
        err_path = out_dir / "error.json"
        if err_path.is_file():
            try:
                err_path.unlink()
            except OSError:
                pass
        if not heading_report.get("ok") and HEADING_DEPTH_CHECK_FATAL:
            print("[FAIL] heading_level 深度未跟上 step2 建议（HEADING_DEPTH_CHECK_FATAL=True）")
            return False
        return True


def _outline_plan_ok(
    plan: dict,
    *,
    n_sections: int,
    n_blocks: int,
    n_styles: int,
) -> tuple[bool, str]:
    """Reject empty or stub outline+stylesheet plans."""
    if not isinstance(plan, dict) or not plan:
        return False, "plan 为空或非对象"
    if n_sections < 1:
        return False, f"sections={n_sections}（至少 1）"
    if n_blocks < 1:
        return False, f"blocks={n_blocks}（至少 1）"
    if n_styles < 1:
        # stylesheet may live only inside plan; still require some styles
        return False, f"stylesheet_styles={n_styles}（至少 1）"
    return True, ""


def _resolve_from_step1(path: Path) -> tuple[Path, Path | None]:
    """Accept a Step1 draft dir or a themes.xlsx path → (themes.xlsx, step1_dir|None)."""
    if path.is_dir():
        xlsx = path / "themes.xlsx"
        if not xlsx.exists():
            raise FileNotFoundError(f"--from-step1 目录内缺少 themes.xlsx: {path}")
        return xlsx, path
    if path.is_file() and path.suffix.lower() in (".xlsx", ".xlsm"):
        parent = path.parent
        step1_dir = parent if (parent / "draft.yaml").exists() else None
        return path, step1_dir
    raise FileNotFoundError(f"无法识别 Step1 输入（需要草稿目录或 themes.xlsx）: {path}")


def _filter_theme_rows(rows: list[dict], *, theme_ids: list[int] | None,
                       index: int | None, limit: int, run_all: bool) -> tuple[list[dict], str]:
    total = len(rows)
    if theme_ids:
        by_id = {int(r["theme_id"]): r for r in rows}
        missing = [i for i in theme_ids if i not in by_id]
        if missing:
            raise ValueError(f"themes.xlsx 中找不到 theme_id={missing}（enabled 共 {total}）")
        selected = [by_id[i] for i in theme_ids]
        return selected, f"[themes] 指定 theme_id={theme_ids}（enabled 共 {total}）"
    if index is not None:
        if index < 1:
            raise ValueError("--index 须 ≥ 1（1-based）")
        if index > total:
            raise ValueError(f"--index {index} 超出 enabled 行数 {total}")
        by_id = {int(r["theme_id"]): r for r in rows}
        if index in by_id:
            return [by_id[index]], f"[index] theme_id={index}/{total}"
        return [rows[index - 1]], f"[index] 第 {index}/{total} 条 enabled"
    if run_all:
        return rows, f"[all] 跑全部 {total} 条 enabled 主题"
    if limit < 1:
        raise ValueError("--limit 须 ≥ 1（或改用 --all / --theme）")
    return rows[:limit], f"[limit] 只跑前 {limit}/{total} 条（默认 1；--all 跑全部；推荐 --theme）"


def _run_theme_batch(
    *,
    themes_path: Path,
    step1_dir: Path | None,
    rows: list[dict],
    batch_note: str,
    library: str | None,
    temperature: float | None,
    max_tokens: int,
    overwrite: bool,
    genre_id: int | None = None,
    skip_brief: bool = False,
    continue_raw: bool = False,
) -> int:
    print(batch_note)
    print(f"[batch] {len(rows)} 个主题")
    print(f"[themes] {themes_path}")
    if step1_dir:
        print(f"[step1-dir] {step1_dir}  （优先用其中 draft.yaml 作为 Role 库）")
    print("[缓存] 默认：plan.json 已存在则跳过；加 --overwrite 强制重跑")
    print(f"[产物目录] {OUTLINES_DIR.resolve()}  （{{id:02d}}_{{genre}}_{{locale}}__themeN/）")
    if not rows:
        print("[warn] 无主题可跑")
        return 0
    ok = 0
    for idx, r in enumerate(rows, 1):
        theme_n = int(r["theme_id"])
        theme_key = f"theme{theme_n}"
        print(f"\n----- ({idx}/{len(rows)}, {theme_key}) {r['genre']}_{r['locale']} / {r['theme'][:24]} -----")
        try:
            if run_one(
                r["genre"], r["locale"], r["theme"], r["writing"], r["notes"],
                library, temperature, max_tokens,
                step1_dir=step1_dir, overwrite=overwrite,
                theme_key=theme_key, genre_id=genre_id,
                skip_brief=skip_brief, continue_raw=continue_raw,
            ):
                ok += 1
        except FileNotFoundError as e:
            print(f"[skip] {e}")
    print(f"\n[done] {ok}/{len(rows)} 成功或命中缓存")
    print(f"[产物目录] {OUTLINES_DIR.resolve()}")
    print("  每主题一子目录：{id:02d}_{genre}_{locale}__themeN/ …")
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="Step3：写作大纲 → stylesheet → 文本落位 → 媒体落位 → 按章 blocks → 使用集收口。"
                    "推荐 --id；主题来自 Step2 themes.xlsx。"
    )
    ap.add_argument("--id", dest="genre_id", type=int, default=None,
                    help="体裁 id（step1 产物编号，如 1 / 26）")
    ap.add_argument("--ids", default="",
                    help="多个体裁 id，如 1,26 或 1-3（每个 id 按 --theme 跑）")
    ap.add_argument("--theme", type=int, default=None,
                    help="主题 id（themes.xlsx 的 theme_id；默认 1）")
    ap.add_argument("--themes", default="",
                    help="多个主题 id，如 1,2 或 1-3（覆盖 --theme）")
    ap.add_argument("--from-step1", help="（兼容）Step1 草稿目录或 themes.xlsx；推荐改用 --id")
    ap.add_argument("--themes-excel", help="（兼容）等价于 --from-step1 指向 themes.xlsx")
    ap.add_argument("--library", default="",
                    help="显式指定 Role 库 YAML（默认：同目录 draft.yaml → config 金标 → drafts）")
    ap.add_argument("--overwrite", action="store_true",
                    help="强制重跑（默认：plan.json 已存在则跳过）")
    ap.add_argument(
        "--skip-brief",
        action="store_true",
        help="跳过 LLM structure_brief，改用规则回退预算（仍会写入 structure_brief.json）",
    )
    ap.add_argument(
        "--continue-raw",
        action="store_true",
        help="不重跑大纲 LLM：对已有 raw.txt 做截断续写并落盘 plan.json（适合本次解析失败）",
    )
    ap.add_argument("--limit", type=int, default=1,
                    help="无 --theme/--themes 时：只跑前 N 条 enabled（默认 1）")
    ap.add_argument("--all", action="store_true", help="跑该体裁 themes.xlsx 全部 enabled 行")
    ap.add_argument("--index", type=int, default=None,
                    help="（兼容）只跑 theme_id=K 或第 K 条；推荐 --theme")
    ap.add_argument("--make-themes", help="生成空的 themes.xlsx 模板（一般不需要，step1 已产出）")
    ap.add_argument("--temperature", type=float, default=None, help="不填默认 0.3")
    ap.add_argument("--max-tokens", type=int, default=65536)
    ap.add_argument("--genre", help=argparse.SUPPRESS)
    ap.add_argument("--locale", default="zh", help=argparse.SUPPRESS)
    ap.add_argument("--theme-text", default="", help=argparse.SUPPRESS)
    ap.add_argument("--writing", default="", help=argparse.SUPPRESS)
    ap.add_argument("--notes", default="无", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.make_themes:
        make_themes_template(Path(args.make_themes))
        return

    theme_ids = parse_ids_arg(args.themes) if args.themes else ([] if args.theme is None else [args.theme])
    genre_ids = parse_ids_arg(args.ids) if args.ids else ([] if args.genre_id is None else [args.genre_id])
    if genre_ids and not theme_ids and args.index is None and not args.all:
        theme_ids = [1]

    jobs: list[tuple[Path, Path | None, int | None]] = []
    if genre_ids:
        for gid in genre_ids:
            try:
                d = resolve_draft_dir(DRAFTS_DIR, gid)
            except FileNotFoundError as e:
                ap.error(str(e))
            jobs.append((d / "themes.xlsx", d, gid))
            print(f"[id] {gid} → {d.name}")
    elif args.from_step1:
        themes_path, step1_dir = _resolve_from_step1(Path(args.from_step1))
        gid = None
        if step1_dir is not None:
            from LongDocFmtEval.common.draft_resolve import draft_meta
            gid = draft_meta(step1_dir).get("id")
            try:
                gid = int(gid) if gid is not None else None
            except (TypeError, ValueError):
                gid = None
        jobs.append((themes_path, step1_dir, gid))
    elif args.themes_excel:
        themes_path, step1_dir = _resolve_from_step1(Path(args.themes_excel))
        jobs.append((themes_path, step1_dir, None))

    if jobs:
        total_ok = 0
        total_n = 0
        for themes_path, step1_dir, gid in jobs:
            if not themes_path.exists():
                ap.error(f"themes Excel 不存在: {themes_path}")
            rows = read_themes(themes_path)
            try:
                selected, note = _filter_theme_rows(
                    rows,
                    theme_ids=theme_ids or None,
                    index=args.index,
                    limit=args.limit,
                    run_all=args.all,
                )
            except ValueError as e:
                ap.error(str(e))
            total_n += len(selected)
            total_ok += _run_theme_batch(
                themes_path=themes_path,
                step1_dir=step1_dir,
                rows=selected,
                batch_note=note,
                library=args.library or None,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                overwrite=args.overwrite,
                genre_id=gid,
                skip_brief=args.skip_brief,
                continue_raw=args.continue_raw,
            )
        if len(jobs) > 1:
            print(f"\n[all-ids] {total_ok}/{total_n} 成功或命中缓存")
        return

    if args.genre and args.theme_text:
        print("[warn] 手动 --genre/--theme-text 仅为调试；正常请用 --id --theme")
        run_one(
            args.genre, args.locale, args.theme_text, args.writing, args.notes,
            args.library or None, args.temperature, args.max_tokens,
            overwrite=args.overwrite,
            theme_key="theme1",
            skip_brief=args.skip_brief,
            continue_raw=args.continue_raw,
        )
        return

    ap.error("请指定 --id <体裁id> [--theme <主题id>]，或兼容入口 --from-step1")


if __name__ == "__main__":
    main()
