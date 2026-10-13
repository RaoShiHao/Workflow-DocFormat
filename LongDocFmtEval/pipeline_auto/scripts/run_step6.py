"""Step 6 · 按 outline 分块撰写长文.

参考 LongWriter / AgentWrite：默认按 Sec* 一节一写；超大节再按 block 簇拆分。
每批写入 ``chunks/{instance}_c{nn}.json``；全部可写块齐套后才拼 ``content.json``。

用法：
  python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_auto.py --id 1 --until-step 6

默认产物：datasets/{id}_{lang}_{topic_name}/C*/step6_document/
兼容扁平：work/step5_documents/<slug>/

改渲染后只重出 document.docx（不重跑 LLM）：
  python LongDocFmtEval/pipeline_auto/scripts/run_step6.py --template-dir <step5_template> --out-dir <step6_document> --rerender
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import (  # noqa: E402
    parse_ids_arg,
    resolve_draft_dir,
    resolve_outline_dir,
)
from LongDocFmtEval.common.llm import (  # noqa: E402
    dump_json,
    load_json,
    load_prompt,
    render_template,
)
from LongDocFmtEval.common.prompt_locale import (  # noqa: E402
    format_bibliography_context,
    format_theme_context,
)
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text  # noqa: E402
from LongDocFmtEval.pipeline_auto.auto_paths import (  # noqa: E402
    CONFIG_DIR,
    DOCUMENTS_DIR,
    OUTLINES_DIR,
    ROLE_LIBRARY_DRAFTS_DIR,
    TEMPLATES_DIR,
)
from LongDocFmtEval.pipeline_auto.style_taxonomy import (  # noqa: E402
    cell_paragraphs_for_table,
    count_from_selection,
    format_ref_roles,
    layers_payload,
)
from LongDocFmtEval.common.role_library import (  # noqa: E402
    _iter_json_dicts,
    lenient_json_load,
)

# 一节可写块超过该数则拆成多批，避免 JSON 截断
DEFAULT_CHUNK_MAX_BLOCKS = 12
DEFAULT_RETRIES = 5
DEFAULT_FILL_ROUNDS = 2  # 首轮写 + 一轮补跳过的缺失块
DEFAULT_RETRY_WAIT = 60.0

_POS_HINT = {
    "header": "表头", "corner_header": "表角", "column_header": "列标题",
    "group_header": "分组表头", "sub_header": "次级表头",
    "stub": "行标签", "body_label": "标签列", "body_code": "编码列",
    "body_value": "数值列", "data_value": "数值列", "code_cell": "编码列",
    "summary_cell": "摘要列", "body_note": "备注列", "table_note": "表注",
    "total_value": "合计", "total": "合计",
    "label": "标签", "value": "内容",
    "question_no": "题号", "prompt": "题干", "option": "选项", "response_box": "作答栏",
}


def plan_has_bibliography(plan: dict) -> bool:
    """True when this outline actually selected bibliography / references roles.

    Used to gate in-text superscript citation guidance (avoid polluting genres
    without a reference list).
    """
    needles = (
        "bibliograph", "references", "referenceentry",
        "参考文献", "引用文献", "参考资料",
    )

    def _hit(text: str) -> bool:
        low = str(text or "").lower()
        return any(n in low for n in needles)

    for sec in plan.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if _hit(sec.get("role_id") or "") or _hit(sec.get("title") or ""):
            return True
    for entry in plan.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        for b in entry.get("blocks") or []:
            if isinstance(b, dict) and _hit(b.get("role_id") or ""):
                return True
    sel = plan.get("selection") or {}
    if isinstance(sel, dict):
        for key in ("section_roles", "paragraph_body_roles"):
            for rid in sel.get(key) or []:
                if _hit(rid):
                    return True
    for s in plan.get("stylesheet") or []:
        if isinstance(s, dict) and _hit(s.get("style_id") or ""):
            return True
    return False


def _prompt_path(locale: str, name: str, *fallbacks: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name, fallbacks=fallbacks)


def _is_section_block(b: dict, roles: dict) -> bool:
    from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import is_section_style_block  # noqa: PLC0415

    return is_section_style_block(b, roles)


def _is_image_block(b: dict, roles: dict) -> bool:
    r = b.get("role_id")
    return (roles.get(r) or {}).get("object") == "image"


def _is_table_block(b: dict, roles: dict) -> bool:
    if b.get("table_ref"):
        return True
    rid = b.get("role_id") or ""
    if (roles.get(rid) or {}).get("object") == "table":
        return True
    return str(rid).startswith("Tbl")


def _table_columns(role_id: str, plan: dict, lib: dict) -> list[str]:
    kept = None
    for t in (plan.get("selection", {}).get("table_roles") or []):
        if isinstance(t, dict) and t.get("role_id") == role_id:
            kept = t.get("position_map_kept")
    positions: list[str] = []
    for t in (lib.get("roles", {}).get("table") or []):
        if t.get("role_id") == role_id:
            pm = (t.get("table_layout") or {}).get("position_map") or {}
            positions = [p for p in pm if (kept is None or p in kept)]
    labels = [_POS_HINT.get(p, p) for p in positions]
    seen, out = set(), []
    for x in labels:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _rname(roles: dict, rid: str) -> str:
    return (roles.get(rid) or {}).get("display_name") or rid


def _rdesc(roles: dict, rid: str) -> str:
    spec = roles.get(rid) or {}
    return spec.get("description") or spec.get("example") or ""


def _run_role_ids(expected_runs) -> list[str]:
    out: list[str] = []
    for item in (expected_runs or []):
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            rid = str(item.get("role") or item.get("role_id") or item.get("span_role") or "").strip()
            if rid:
                out.append(rid)
    return out


def _run_hints(expected_runs, roles: dict) -> str:
    parts: list[str] = []
    for item in (expected_runs or []):
        if isinstance(item, str) and item.strip():
            rid = item.strip()
            parts.append(f"{_rname(roles, rid)}({rid})")
            continue
        if not isinstance(item, dict):
            continue
        rid = str(item.get("role") or item.get("role_id") or "").strip()
        if not rid:
            continue
        bit = f"{_rname(roles, rid)}({rid})"
        frag = str(item.get("text") or "").strip()
        reason = str(item.get("reason") or item.get("criterion") or "").strip()
        if frag:
            bit += f" 子串「{frag}」"
        if reason:
            bit += f" 标准：{reason}"
        parts.append(bit)
    return "、".join(parts)


def build_heading_outline(plan: dict, roles: dict) -> str:
    """Fallback compact tree: section titles + heading blocks only (no body/table dump)."""
    from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level as detect_hl  # noqa: PLC0415

    idx = heading_index(plan)
    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    lines: list[str] = []
    for sec in (plan.get("sections") or []):
        inst = sec.get("instance_id")
        lines.append(f"[节 {inst}] {sec.get('title', '')}")
        for b in outline_map.get(inst, []):
            rid = str(b.get("role_id") or "")
            rec = idx.get(rid) or {}
            hl = rec.get("heading_level")
            if hl is None and not rec.get("title_kind"):
                hl = detect_hl(rid, _rname(roles, rid))
            if not hl and not rec.get("title_kind"):
                continue
            tag = f"H{int(hl)}" if hl else str(rec.get("title_kind") or "title")
            lines.append(f"    · [{tag}] {b.get('block_id')} {(b.get('main_point') or '')[:60]}")
    return "\n".join(lines)


def build_global_outline(plan: dict, roles: dict) -> str:
    """Kept for debug; step5 uses writing_outline / heading-only compact tree."""
    return build_heading_outline(plan, roles)


def build_roles_ref(blocks: list, plan: dict, lib: dict, roles: dict) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for b in blocks:
        for rid in [b.get("role_id"), *_run_role_ids(b.get("expected_runs"))]:
            if not rid or rid in seen:
                continue
            seen.add(rid)
            obj = (roles.get(rid) or {}).get("object", "")
            tag = "[run]" if obj == "run" else ("[表格]" if obj == "table" else "")
            lines.append(f"- {_rname(roles, rid)} ({rid}){tag}：{_rdesc(roles, rid)}")
    return "\n".join(lines) or "（无）"


def build_section_blocks(blocks: list, plan: dict, lib: dict, roles: dict) -> str:
    lines: list[str] = []
    for b in blocks:
        if _is_image_block(b, roles) or _is_section_block(b, roles):
            continue
        rid = b.get("role_id")
        head = f"- block_id: {b['block_id']} | 角色: {_rname(roles, rid)}({rid}) | 长度: {b.get('length_hint', '')}"
        if _is_table_block(b, roles):
            ref = b.get("table_ref") or rid
            cols = _table_columns(ref, plan, lib)
            cells = cell_paragraphs_for_table(str(ref), plan, lib)
            head += " | 表格(Tbl*结构)" + (f" | 建议列: {'/'.join(cols)}" if cols else "")
            if cells:
                head += " | 格内文字角色: " + "/".join(cells)
        if b.get("expected_runs"):
            hint = _run_hints(b["expected_runs"], roles)
            head += " | 需带 run: " + (hint or "（见大纲）")
        lines.append(head)
        lines.append(f"    要点: {b.get('main_point', '')}")
        if b.get("organization_note"):
            lines.append(f"    行文提示: {b['organization_note']}")
    return "\n".join(lines)


def split_writable_chunks(writable: list[dict], roles: dict, *, max_blocks: int) -> list[list[dict]]:
    """超大节拆批：优先在表题+表前切开；单批不超过 max_blocks。"""
    if len(writable) <= max_blocks:
        return [writable]
    chunks: list[list[dict]] = []
    cur: list[dict] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            chunks.append(cur)
            cur = []

    for b in writable:
        # 新表开始且当前已有一定篇幅 → 切开，让表与表题同批
        starts_table = _is_table_block(b, roles)
        prev_is_title = False
        if cur:
            last = cur[-1]
            last_rid = str(last.get("role_id") or "")
            prev_is_title = "TableTitle" in last_rid or "表题" in last_rid
        if starts_table and cur and not prev_is_title and len(cur) >= max(4, max_blocks // 3):
            flush()
        # 表题：尽量和下一个表块同批
        rid = str(b.get("role_id") or "")
        is_table_title = "TableTitle" in rid
        if is_table_title and cur and len(cur) >= max_blocks - 2:
            flush()
        cur.append(b)
        if len(cur) >= max_blocks:
            flush()
    flush()
    return chunks or [writable]


def _check_chunk_write(writable: list, produced: dict, roles: dict) -> list[str]:
    warns: list[str] = []
    got = set((produced or {}).keys())
    need = {b["block_id"] for b in writable if b.get("block_id")}
    missing = sorted(need - got)
    extra = sorted(got - need)
    if missing:
        warns.append(f"缺块 {missing}")
    if extra:
        warns.append(f"多余块 {extra}")
    for b in writable:
        bid = b.get("block_id")
        val = (produced or {}).get(bid) or {}
        if not isinstance(val, dict):
            continue
        if bid not in got:
            continue
        if val.get("table"):
            rows = (val["table"] or {}).get("rows") or []
            if len(rows) < 3:
                warns.append(f"{bid} 表格行偏少({len(rows)}，建议≥4)")
            continue
        text = str(val.get("text") or "")
        hint = str(b.get("length_hint") or "").lower()
        nchars = len(re.sub(r"\s+", "", text))
        if hint == "medium" and nchars < 280:
            warns.append(f"{bid} medium 偏短({nchars}字，目标≥400)")
        elif hint == "long" and nchars < 500:
            warns.append(f"{bid} long 偏短({nchars}字，目标≥800)")
        want_runs = _run_role_ids(b.get("expected_runs"))
        if not want_runs:
            continue
        have = val.get("runs") or []
        for er in have:
            if not isinstance(er, dict):
                continue
            sub = str(er.get("text") or "")
            if sub and sub not in text:
                warns.append(f"{bid} run子串未出现在正文：「{sub[:20]}」")
        have_roles: set[str] = set()
        for er in have:
            have_roles.update(_run_role_ids([er] if not isinstance(er, str) else er))
        for rid in want_runs:
            if rid not in have_roles:
                warns.append(f"{bid} 缺 expected_run {rid}")
    return warns


def _has_image_roles(roles: dict) -> bool:
    return any((v or {}).get("object") == "image" for v in roles.values())


def _write_placeholder_assets(
    out_dir: Path,
    template: dict,
    plan: dict,
    lib: dict,
    blocks: dict,
) -> Path | None:
    """Ensure assets.json + PNG exist（build_figures 未产出时的兜底，保证 document 能插图）。"""
    try:
        from LongDocFmtEval.pipeline_auto.scripts.build_figures import (  # noqa: PLC0415
            collect_figures,
            render_one,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    [warn] 占位图依赖不可用: {exc}")
        return None

    figs = collect_figures(template, plan, lib, blocks)
    if not figs:
        return None
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    if (out_dir / "assets.json").is_file():
        try:
            manifest = dict(load_json(out_dir / "assets.json") or {})
        except Exception:  # noqa: BLE001
            manifest = {}

    figure_specs: dict = {}
    for specs_name in ("figures.json", "figures_preview.json"):
        specs_path = out_dir / "figures" / specs_name
        if specs_path.is_file():
            try:
                figure_specs = load_json(specs_path) or {}
                break
            except Exception:  # noqa: BLE001
                figure_specs = {}

    from LongDocFmtEval.pipeline_auto.auto_paths import (  # noqa: PLC0415
        rel_from,
        resolve_work_file,
        sibling_step_dir,
    )

    step3 = sibling_step_dir(out_dir, "template") or (TEMPLATES_DIR / out_dir.name)
    src_meta_path = out_dir / "source_step6.json"
    if not src_meta_path.is_file():
        src_meta_path = out_dir / "source_step5.json"
    if src_meta_path.is_file():
        try:
            td = Path((load_json(src_meta_path) or {}).get("template_dir") or "")
            if td.is_dir():
                step3 = td
        except Exception:  # noqa: BLE001
            pass
    preview_manifest: dict = {}
    pm_path = resolve_work_file(out_dir, "assets_preview.json")
    if pm_path.is_file():
        try:
            preview_manifest = load_json(pm_path) or {}
        except Exception:  # noqa: BLE001
            preview_manifest = {}

    for i, f in enumerate(figs):
        fid = str(f["figure_id"])
        asset = assets_dir / f"{_safe_fig(fid)}.png"
        used = "placeholder"
        asset_rel = f"assets/{asset.name}"
        if asset.exists():
            used = str((manifest.get(fid) or {}).get("gen_method") or "cache")
        else:
            prev = preview_manifest.get(fid) or {}
            prev_rel = prev.get("asset")
            prev_file = (step3 / str(prev_rel)) if prev_rel else None
            if prev_file is not None and prev_file.is_file():
                used = "from_preview"
                asset_rel = rel_from(prev_file, out_dir)
            else:
                spec = figure_specs.get(fid) or {**f, "figure_id": fid, "gen_method": "placeholder"}
                used = render_one(spec, asset, seed=1234 + i)
        manifest[fid] = {
            "role_id": f.get("role_id"),
            "gen_method": used,
            "title": f.get("display_name") or fid,
            "alt": f.get("alt_t3") or f.get("main_point") or fid,
            "asset": asset_rel,
            "width": f"{float(f.get('width_cm') or 12):g}cm",
            "height": f"{float(f.get('height_cm') or 8):g}cm",
            "align": f.get("align") or "center",
        }
    out = out_dir / "assets.json"
    dump_json(out, manifest)
    print(f"    [figures] 图集就绪 → {out.name}（{len(manifest)} 张）")
    return out


def _safe_fig(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(name)).strip("_") or "fig"


def _build_figures(out_dir: Path, *, overwrite: bool) -> bool:
    try:
        from LongDocFmtEval.pipeline_auto.scripts import build_figures  # noqa: PLC0415
        return bool(
            build_figures.build_one(
                out_dir, variant="document", overwrite=overwrite,
                temperature=0.5, max_tokens=20480, seed=1234, no_render=True,
            )
        )
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] 图构建失败：{e}")
        return False


def _render_gold(out_dir: Path, content: dict) -> None:
    try:
        from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import render  # noqa: PLC0415
        out = render(
            out_dir, content=content.get("blocks"), annotated=False,
            out_name="document.docx",
            assets_name="assets.json",
        )
        print(f"    [gold] {out}")
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] gold 文档渲染失败：{e}")


_SUMMARY_KEYS = frozenset({"chunk_summary", "section_summary"})
_BLOCK_ID_RE = re.compile(r"^I\d+_B\d+", re.I)


def _looks_like_block_body(val: Any) -> bool:
    return isinstance(val, dict) and any(k in val for k in ("text", "table", "runs"))


def _hoist_nested_block_ids(blocks: dict) -> dict[str, Any]:
    """Lift ``Ixx_Byy`` keys nested inside another block (usually a missing ``}`` after table/text)."""
    if not isinstance(blocks, dict):
        return {}
    nested: dict[str, Any] = {}

    def walk(obj: dict, parent_id: str) -> dict:
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            kid = str(k)
            if kid != parent_id and _BLOCK_ID_RE.match(kid) and isinstance(v, dict):
                nested[kid] = walk(v, kid)
                continue
            cleaned[k] = v
        return cleaned

    out: dict[str, Any] = {}
    for bid, val in blocks.items():
        key = str(bid)
        if isinstance(val, dict):
            out[key] = walk(val, key)
        else:
            out[key] = val
    n_lift = 0
    for bid, val in nested.items():
        if bid not in out:
            out[bid] = val
            n_lift += 1
            continue
        if not _looks_like_block_body(out[bid]) and _looks_like_block_body(val):
            out[bid] = val
            n_lift += 1
    if n_lift:
        print(f"    [parse] 提升 {n_lift} 个嵌套 block_id 到顶层")
    return out


def _coerce_chunk_payload(data: Any) -> dict | None:
    """Accept root ``{blocks:…}`` or the inner blocks map (when the root ``}`` is missing)."""
    if not isinstance(data, dict):
        return None

    def _take_blocks(src: dict) -> tuple[dict, str, str]:
        csum = src.get("chunk_summary") if isinstance(src.get("chunk_summary"), str) else ""
        ssum = src.get("section_summary") if isinstance(src.get("section_summary"), str) else ""
        out: dict[str, Any] = {}
        for k, v in src.items():
            if k in _SUMMARY_KEYS:
                continue
            if isinstance(v, dict) and (_looks_like_block_body(v) or _BLOCK_ID_RE.match(str(k))):
                out[str(k)] = v
        return out, str(csum or ""), str(ssum or "")

    blocks = data.get("blocks")
    if isinstance(blocks, dict) and blocks:
        cleaned, extra_c, extra_s = _take_blocks(blocks)
        if not cleaned:
            return None
        out = dict(data)
        out["blocks"] = cleaned
        if extra_c and not str(out.get("chunk_summary") or "").strip():
            out["chunk_summary"] = extra_c
        if extra_s and not str(out.get("section_summary") or "").strip():
            out["section_summary"] = extra_s
        out["blocks"] = _hoist_nested_block_ids(out["blocks"])
        return out

    inner, csum, ssum = _take_blocks(data)
    if not inner:
        return None
    return {
        "blocks": _hoist_nested_block_ids(inner),
        "chunk_summary": csum,
        "section_summary": ssum,
    }


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _loads_with_close(text: str) -> tuple[dict | None, str]:
    """Parse JSON; if the model omitted trailing ``}``/``]``, append them."""
    s = text.rstrip()
    s = re.sub(r",\s*$", "", s)
    try:
        obj = json.loads(s)
        return (obj, "ok") if isinstance(obj, dict) else (None, "")
    except json.JSONDecodeError:
        pass
    for n_brace in range(1, 6):
        for n_brack in range(0, 3):
            try:
                obj = json.loads(s + ("]" * n_brack) + ("}" * n_brace))
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                how = f"+{n_brace}}}"
                if n_brack:
                    how = f"+{n_brack}]{n_brace}}}"
                return obj, how
    return None, ""


def _parse_chunk_payload(raw: str) -> dict:
    """Parse a step6 chunk. Recover missing root ``}`` and inner-map decoys.

    ``lenient_json_load`` ranks Role-library ``roles`` objects. For an unclosed
    ``{"blocks":{…}}`` it often returns the inner map (no ``blocks`` key), which
    used to look like truncation. Local repair first; do not bump max_tokens.
    """
    text = _strip_json_fence(raw)
    if not text:
        raise ValueError("输出为空")

    obj, how = _loads_with_close(text)
    if obj is not None:
        coerced = _coerce_chunk_payload(obj)
        if coerced:
            if how != "ok":
                print(f"    [parse] 补闭合括号后解析成功（{how}）")
            return coerced

    for cand in _iter_json_dicts(text):
        coerced = _coerce_chunk_payload(cand)
        if coerced:
            print("    [parse] 从内层 blocks 映射还原")
            return coerced

    try:
        coerced = _coerce_chunk_payload(lenient_json_load(text))
        if coerced:
            print("    [parse] 从内层 blocks 映射还原")
            return coerced
    except Exception:  # noqa: BLE001
        pass
    raise ValueError("缺非空 blocks 对象（多为根 JSON 少写一层 }，已尝试本地补全仍失败）")


def _is_upstream_capacity_error(exc: BaseException) -> bool:
    """True when the API/gateway is out of capacity — wait before retry, don't hammer."""
    msg = str(exc).lower()
    needles = (
        "503",
        "429",
        "model_not_found",
        "no available channel",
        "rate limit",
        "ratelimit",
        "overloaded",
        "capacity",
        "too many requests",
        "server_error",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection reset",
        "connection error",
        "service unavailable",
    )
    return any(n in msg for n in needles)


def _write_chunk_with_retry(
    *,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    retries: int,
    dump_dir: Path | None,
    tag: str,
    retry_wait: float = 60.0,
    need_ids: list[str] | None = None,
) -> dict | None:
    """Retry failed chunks. Capacity/503 → sleep then retry; JSON parse → prompt nudge sooner."""
    last_err: Exception | None = None
    last_was_capacity = False
    last_was_missing = False
    best: dict | None = None
    need = [str(x) for x in (need_ids or []) if x]
    for attempt in range(1, max(1, retries) + 1):
        attempt_user = user
        attempt_tokens = max_tokens
        if attempt > 1:
            if last_was_capacity:
                wait_s = min(float(retry_wait) * (2 ** (attempt - 2)), 600.0)
                print(
                    f"    [wait] 上游资源不足，暂停 {wait_s:.0f}s 后再试"
                    f"（第 {attempt}/{retries} 次）…"
                )
                time.sleep(wait_s)
                print(f"    [retry] 第 {attempt}/{retries} 次（max_tokens={attempt_tokens}）…")
            else:
                if not last_was_missing:
                    attempt_tokens = min(int(max_tokens * (1.0 + 0.25 * (attempt - 1))), 32768)
                if last_was_missing:
                    extra = (
                        f"上次 JSON 能解析但缺块：{last_err}。"
                        "每个待写 block_id 必须是 blocks 的**顶层键**，"
                        "不要写进上一块的 table/text 对象内部。"
                    )
                else:
                    extra = (
                        "上次输出不是合法闭合 JSON（常见：根对象少一层 }）。"
                        "请重新输出**完整** JSON：根键含 blocks、chunk_summary、section_summary；"
                        "blocks 必须覆盖全部待写 block_id；花括号必须配对闭合。"
                    )
                attempt_user = (
                    user
                    + "\n\n## 重试说明（第 "
                    + str(attempt)
                    + " 次）\n"
                    + extra
                )
                print(f"    [retry] 第 {attempt}/{retries} 次（max_tokens={attempt_tokens}）…")
        raw = ""
        try:
            raw = stream_llm_text(
                system=system, user=attempt_user,
                temperature=temperature, max_tokens=attempt_tokens,
            )
            data = _parse_chunk_payload(raw)
            blocks = _hoist_nested_block_ids(data.get("blocks") or {})
            data = {**data, "blocks": blocks}
            if need:
                got = set(blocks.keys())
                miss = [i for i in need if i not in got]
                if miss:
                    best = data
                    raise ValueError(
                        f"缺块 {miss}（已有 {sorted(got)}，{len(got)}/{len(need)}）"
                    )
            return data
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            last_was_capacity = _is_upstream_capacity_error(e)
            last_was_missing = (not last_was_capacity) and str(e).startswith("缺块")
            kind = "上游资源" if last_was_capacity else ("缺块" if last_was_missing else "生成/解析")
            print(f"    [warn] 本批{kind}失败（第 {attempt}/{retries} 次）：{e}")
            if dump_dir is not None:
                try:
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    (dump_dir / f"{tag}.attempt{attempt}.error.txt").write_text(
                        str(e), encoding="utf-8")
                    if raw:
                        (dump_dir / f"{tag}.attempt{attempt}.raw.txt").write_text(
                            raw, encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
    if best and (best.get("blocks") or {}):
        n = len(best.get("blocks") or {})
        print(f"    [warn] 本批在 {retries} 次尝试后仍缺块，保留已有 {n} 块")
        return best
    print(f"    [warn] 本批在 {retries} 次尝试后仍失败，跳过")
    return None


def _chunk_token_budget(base: int, n_blocks: int) -> int:
    # 长文充实：每块预留更多输出
    need = 2500 + n_blocks * 1200
    return min(max(base, need), 32768)


def _section_complete(writable: list[dict], blocks: dict) -> bool:
    need = {b["block_id"] for b in writable if b.get("block_id")}
    return bool(need) and need.issubset(set((blocks or {}).keys()))


def _missing_writable(writable: list[dict], blocks: dict) -> list[dict]:
    have = set((blocks or {}).keys())
    return [b for b in writable if b.get("block_id") and b["block_id"] not in have]


def _count_missing(plan: dict, outline_map: dict, roles: dict, blocks: dict) -> int:
    n = 0
    for sec in (plan.get("sections") or []):
        inst = sec.get("instance_id")
        writable = [b for b in outline_map.get(inst, []) if not _is_image_block(b, roles) and not _is_section_block(b, roles)]
        n += len(_missing_writable(writable, blocks))
    return n


def _persist_content(path: Path, content: dict) -> None:
    dump_json(path, content)


def _chunks_dir(out_dir: Path) -> Path:
    return Path(out_dir) / "chunks"


def _chunk_filename(inst: str, ci: int) -> str:
    return f"{inst}_c{ci:02d}.json"


def _load_chunks(chunks_dir: Path) -> tuple[dict[str, Any], dict[str, str], dict[str, list[str]]]:
    """Merge ``chunks/*.json`` into blocks + summaries. Incomplete files still contribute."""
    all_blocks: dict[str, Any] = {}
    summaries: dict[str, str] = {}
    chunk_notes: dict[str, list[str]] = {}
    if not chunks_dir.is_dir():
        return all_blocks, summaries, chunk_notes
    for p in sorted(chunks_dir.glob("*_c*.json")):
        try:
            data = load_json(p)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        inst = str(data.get("instance_id") or "")
        file_blocks: dict[str, Any] = {}
        for bid, val in (data.get("blocks") or {}).items():
            if isinstance(val, dict) and bid not in ("section_summary", "chunk_summary"):
                file_blocks[str(bid)] = val
        for bid, val in _hoist_nested_block_ids(file_blocks).items():
            all_blocks[bid] = val
        csum = str(data.get("chunk_summary") or "").strip()
        if inst and csum:
            notes = chunk_notes.setdefault(inst, [])
            if csum not in notes:
                notes.append(csum)
        ssum = str(data.get("section_summary") or "").strip()
        if inst and ssum:
            summaries[inst] = ssum
    return all_blocks, summaries, chunk_notes


def _merge_legacy_content(
    existing: dict | None,
    all_blocks: dict[str, Any],
    summaries: dict[str, str],
    chunk_notes: dict[str, list[str]],
) -> None:
    """Fill holes from a leftover content.json; chunk files win on conflict."""
    if not existing:
        return
    for bid, val in (existing.get("blocks") or {}).items():
        if isinstance(val, dict):
            all_blocks.setdefault(str(bid), val)
    for inst, ssum in (existing.get("section_summaries") or {}).items():
        if ssum and str(inst) not in summaries:
            summaries[str(inst)] = str(ssum)
    for inst, notes in (existing.get("chunk_summaries") or {}).items():
        if not isinstance(notes, list):
            continue
        dest = chunk_notes.setdefault(str(inst), [])
        for n in notes:
            if n and n not in dest:
                dest.append(n)


def _write_chunk_file(
    chunks_dir: Path,
    *,
    inst: str,
    ci: int,
    chunk_total: int,
    block_ids: list[str],
    blocks: dict[str, Any],
    chunk_summary: str,
    section_summary: str,
) -> Path:
    chunks_dir.mkdir(parents=True, exist_ok=True)
    path = chunks_dir / _chunk_filename(inst, ci)
    dump_json(path, {
        "instance_id": inst,
        "chunk_index": ci,
        "chunk_total": chunk_total,
        "block_ids": block_ids,
        "blocks": blocks,
        "chunk_summary": chunk_summary,
        "section_summary": section_summary,
    })
    print(f"    [chunk] {path.name}（{len(blocks)}/{len(block_ids)} 块）")
    return path


def _merge_chunk_blocks(
    chunks_dir: Path,
    inst: str,
    ci: int,
    orig_ids: list[str],
    produced: dict[str, Any],
    all_blocks: dict[str, Any],
) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    path = chunks_dir / _chunk_filename(inst, ci)
    if path.is_file():
        try:
            old = load_json(path)
            for bid, val in (old.get("blocks") or {}).items():
                if isinstance(val, dict):
                    saved[str(bid)] = val
        except Exception:  # noqa: BLE001
            pass
    for bid in orig_ids:
        if bid in produced:
            saved[bid] = produced[bid]
        elif bid in all_blocks:
            saved[bid] = all_blocks[bid]
    return {k: saved[k] for k in orig_ids if k in saved}


def _backfill_complete_chunk_files(
    chunks_dir: Path,
    plan: dict,
    outline_map: dict,
    roles: dict,
    all_blocks: dict[str, Any],
    summaries: dict[str, str],
    *,
    chunk_max: int,
) -> int:
    """Write missing chunk files for already-complete original batches (legacy content.json)."""
    n = 0
    for sec in (plan.get("sections") or []):
        inst_raw = sec.get("instance_id")
        inst = str(inst_raw or "")
        writable = [
            b for b in (outline_map.get(inst_raw) or outline_map.get(inst) or [])
            if not _is_image_block(b, roles) and not _is_section_block(b, roles)
        ]
        if not writable:
            continue
        orig_chunks = split_writable_chunks(writable, roles, max_blocks=chunk_max)
        chunk_total = len(orig_chunks)
        for ci, orig_chunk in enumerate(orig_chunks, start=1):
            orig_ids = [str(b["block_id"]) for b in orig_chunk if b.get("block_id")]
            if not orig_ids or any(bid not in all_blocks for bid in orig_ids):
                continue
            path = chunks_dir / _chunk_filename(inst, ci)
            if path.is_file():
                continue
            _write_chunk_file(
                chunks_dir,
                inst=inst,
                ci=ci,
                chunk_total=chunk_total,
                block_ids=orig_ids,
                blocks={bid: all_blocks[bid] for bid in orig_ids},
                chunk_summary="",
                section_summary=summaries.get(inst, "") if ci == chunk_total else "",
            )
            n += 1
    return n


def _content_payload(
    template: dict,
    slug: str,
    all_blocks: dict,
    summaries: dict,
    chunk_notes: dict,
) -> dict:
    return {
        "meta": {
            **(template.get("meta") or {}),
            "slug": slug,
            "step": 6,
            "writing_mode": "writing_outline_then_plan",
        },
        "blocks": all_blocks,
        "section_summaries": summaries,
        "chunk_summaries": chunk_notes,
    }


def prepare_step6_out_dir(template_dir: Path, out_dir: Path) -> Path:
    """Prepare step6 dir: pointer to T* only — do not copy plan/template IR."""
    from LongDocFmtEval.pipeline_auto.auto_paths import drop_redundant_copies  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.auto_paths import sibling_step_dir  # noqa: PLC0415

    template_dir = template_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    styles = sibling_step_dir(template_dir, "styles")
    src_meta = {
        "step": 6,
        "slug": template_dir.name,
        "template_dir": str(template_dir).replace("\\", "/"),
        "styles_dir": str(styles).replace("\\", "/") if styles else "",
        "out_dir": str(out_dir).replace("\\", "/"),
    }
    dump_json(out_dir / "source_step6.json", src_meta)
    dropped = drop_redundant_copies(out_dir)
    if dropped:
        print(f"  [dedupe] 去掉与 step3/4 重复的 {', '.join(dropped)}")
    # 兼容：若旧版把 content 写在 step3，首次迁到 step5
    legacy = template_dir / "content.json"
    target = out_dir / "content.json"
    if legacy.is_file() and not target.is_file():
        shutil.copy2(legacy, target)
        print(f"  [migrate] step3 content.json → {out_dir.name}/content.json")
    _sync_asset_sizes_from_roles(out_dir)
    return out_dir


def _sync_asset_sizes_from_roles(out_dir: Path) -> None:
    """Keep assets.json width/height aligned with Img* role props (人改回写后)。"""
    try:
        from LongDocFmtEval.pipeline_auto.seed.apply_style_tree_to_tstar import (
            sync_asset_sizes_from_roles,
        )

        n = sync_asset_sizes_from_roles(out_dir, assets_name="assets.json")
        if n:
            print(f"  [sync] assets.json 尺寸 ← Img* props（{n} 处）")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] assets 尺寸同步跳过: {exc}")


def resolve_out_dir(template_dir: Path, out_dir: str = "") -> Path:
    if out_dir:
        p = Path(out_dir)
        if not p.is_absolute():
            alt = Path("LongDocFmtEval") / out_dir
            if alt.exists() or not p.exists():
                pass
        return p.resolve()
    from LongDocFmtEval.pipeline_auto.auto_paths import sibling_step_dir  # noqa: PLC0415

    sib = sibling_step_dir(template_dir, "document")
    if sib is not None:
        return sib.resolve()
    return (DOCUMENTS_DIR / template_dir.name).resolve()


def _write_content(
    out_dir: Path,
    template: dict,
    plan: dict,
    lib: dict,
    roles: dict,
    locale: str,
    temperature: float,
    max_tokens: int,
    *,
    retries: int = DEFAULT_RETRIES,
    chunk_max: int = DEFAULT_CHUNK_MAX_BLOCKS,
    retry_wait: float = DEFAULT_RETRY_WAIT,
    resume: bool = False,
    existing: dict | None = None,
    fill_rounds: int = DEFAULT_FILL_ROUNDS,
    chapter_workers: int = 1,
) -> dict:
    import threading  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

    meta = plan.get("meta", {})
    context = format_theme_context(
        locale,
        genre=str(template.get("meta", {}).get("genre", meta.get("genre", "")) or ""),
        theme=str(meta.get("theme", "") or ""),
        writing_requirements=str(meta.get("writing_requirements", "") or ""),
        note=(
            "Write the final draft from the outline; the template may show fewer "
            "tables/figures, but this step must cover every writable block."
            if str(locale or "").lower().startswith("en")
            else "本步按大纲写正式成稿；template 可少示范表/图，本步必须覆盖全部可写块。"
        ),
    )
    context += format_bibliography_context(
        locale, has_bibliography=plan_has_bibliography(plan),
    )
    global_outline = build_heading_outline(plan, roles)
    try:
        from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415
        from LongDocFmtEval.pipeline_auto.seed.writing_outline import (  # noqa: PLC0415
            format_writing_outline_compact,
            load_writing_outline,
        )

        wo = load_writing_outline(resolve_work_file(out_dir, "writing_outline.json"))
        if wo:
            global_outline = format_writing_outline_compact(wo)
            print("    [context] 写作大纲短树（非全量 blocks）")
    except Exception:  # noqa: BLE001
        pass
    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    prompt = load_prompt(_prompt_path(locale, "step6_write_content.yaml", "step5_write_content.yaml"))
    system = prompt["system"]
    try:
        layers = layers_payload(
            selection=plan.get("selection"),
            stylesheet=plan.get("stylesheet"),
        )
        dump_json(out_dir / "style_layers.json", layers)
        print(f"[style-layers] {format_ref_roles(count_from_selection(plan.get('selection') or {}))}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] style_layers: {e}")

    chunks_dir = _chunks_dir(out_dir)
    all_blocks, summaries, chunk_notes = _load_chunks(chunks_dir)
    _merge_legacy_content(existing, all_blocks, summaries, chunk_notes)
    n_bf = _backfill_complete_chunk_files(
        chunks_dir, plan, outline_map, roles, all_blocks, summaries,
        chunk_max=chunk_max,
    )
    if n_bf:
        print(f"    [chunks] 从已有块补齐 {n_bf} 个批文件")

    slug = out_dir.name
    dump_dir = out_dir / "_step6_failures"
    content_path = out_dir / "content.json"
    if content_path.exists() and _count_missing(plan, outline_map, roles, all_blocks):
        content_path.unlink()
        print("    [content] 去掉未齐套 content.json（齐套后再拼）")
    rounds = max(1, int(fill_rounds))
    workers = max(1, int(chapter_workers or 1))
    have_existing = bool(all_blocks)
    if resume or have_existing:
        rounds = max(rounds, 1)
    state_lock = threading.Lock()

    def _write_one_section(sec: dict, round_i: int) -> list[str]:
        """Write missing chunks for one Word section. Returns skipped tags."""
        nonlocal have_existing
        skipped: list[str] = []
        inst_raw = sec.get("instance_id")
        inst = str(inst_raw or "")
        blocks = outline_map.get(inst_raw) or outline_map.get(inst) or []
        writable = [
            b for b in blocks
            if not _is_image_block(b, roles) and not _is_section_block(b, roles)
        ]
        if not writable:
            return skipped

        with state_lock:
            if _section_complete(writable, all_blocks):
                print(f"  [skip] 已完成节 {inst}·{sec.get('title', '')}")
                return skipped
            n_miss_sec = len(_missing_writable(writable, all_blocks))

        orig_chunks = split_writable_chunks(writable, roles, max_blocks=chunk_max)
        chunk_total = len(orig_chunks)
        print(
            f"  节 {inst}·{sec.get('title', '')}"
            f"（{len(writable)} 块 / {chunk_total} 批"
            + (f"，补 {n_miss_sec}" if n_miss_sec < len(writable) else "")
            + "）"
        )

        for ci, orig_chunk in enumerate(orig_chunks, start=1):
            orig_ids = [str(b["block_id"]) for b in orig_chunk if b.get("block_id")]
            with state_lock:
                missing = [
                    b for b in orig_chunk
                    if b.get("block_id") and str(b["block_id"]) not in all_blocks
                ]
            chunk_name = _chunk_filename(inst, ci)
            if not missing:
                print(f"    [skip] 批 {chunk_name} 已齐套")
                continue

            is_last = ci == chunk_total
            user = render_template(
                prompt["user_template"],
                context=context,
                global_outline=global_outline,
                roles_ref=build_roles_ref(missing, plan, lib, roles),
                section_title=sec.get("title", ""),
                section_purpose=sec.get("purpose", ""),
                section_blocks=build_section_blocks(missing, plan, lib, roles),
                chunk_label=f"{inst}_c{ci:02d}",
                chunk_index=str(ci),
                chunk_total=str(chunk_total),
                is_last_chunk="true" if is_last else "false",
            )
            tok = _chunk_token_budget(max_tokens, len(missing))
            n_had = len(orig_ids) - len(missing)
            extra = f"，补 {len(missing)}/{len(orig_ids)}" if n_had else ""
            print(f"    批 {chunk_name}（{ci}/{chunk_total}，{len(missing)} 块{extra}，max_tokens≈{tok}）")
            tag = f"{inst}_c{ci:02d}_r{round_i}"
            data = _write_chunk_with_retry(
                system=system, user=user, temperature=temperature, max_tokens=tok,
                retries=retries, dump_dir=dump_dir, tag=tag,
                retry_wait=retry_wait,
                need_ids=[str(b["block_id"]) for b in missing if b.get("block_id")],
            )
            if not data:
                skipped.append(tag)
                continue
            produced: dict[str, dict] = {}
            with state_lock:
                for bid, val in (data.get("blocks") or {}).items():
                    if bid in ("section_summary", "chunk_summary") or not isinstance(val, dict):
                        continue
                    all_blocks[str(bid)] = val
                    produced[str(bid)] = val
                for w in _check_chunk_write(missing, produced, roles)[:10]:
                    print(f"      · {w}")

                csum = (data.get("chunk_summary") or "").strip()
                if csum:
                    notes = chunk_notes.setdefault(inst, [])
                    if csum not in notes:
                        notes.append(csum)
                ssum = ""
                if is_last:
                    ssum = (data.get("section_summary") or "").strip() or csum
                    if ssum:
                        summaries[inst] = ssum

                merged = _merge_chunk_blocks(
                    chunks_dir, inst, ci, orig_ids, produced, all_blocks,
                )
                old_csum = ""
                old_ssum = ""
                old_path = chunks_dir / chunk_name
                if old_path.is_file():
                    try:
                        old = load_json(old_path)
                        old_csum = str(old.get("chunk_summary") or "").strip()
                        old_ssum = str(old.get("section_summary") or "").strip()
                    except Exception:  # noqa: BLE001
                        pass
                _write_chunk_file(
                    chunks_dir,
                    inst=inst,
                    ci=ci,
                    chunk_total=chunk_total,
                    block_ids=orig_ids,
                    blocks=merged,
                    chunk_summary=csum or old_csum,
                    section_summary=ssum or old_ssum or summaries.get(inst, ""),
                )
                have_existing = True
        return skipped

    for round_i in range(1, rounds + 1):
        fill_only = resume or have_existing or round_i > 1
        miss0 = _count_missing(plan, outline_map, roles, all_blocks)
        if miss0 == 0:
            if round_i == 1:
                print(f"\n=== step6: {slug} ===  已齐套，无需补写")
            break
        if round_i == 1 and not fill_only:
            print(
                f"\n=== step6: {slug} ===  "
                f"({len(plan.get('sections') or [])} 节，chunk_max={chunk_max}，"
                f"retries={retries}，fill_rounds={rounds}，chapter_workers={workers})"
            )
        elif round_i == 1:
            print(
                f"\n=== step6: {slug} 续写 ===  "
                f"已有 {len(all_blocks)} 块，缺 {miss0}；从 chunks/ 缺失批继续"
                f"（chapter_workers={workers}）"
            )
        else:
            print(
                f"\n=== step6 第 {round_i} 轮补写 ===  "
                f"缺失 {miss0} 块（每批最多重试 {retries} 次，失败则跳过）"
            )

        skipped_tags: list[str] = []
        sections = [s for s in (plan.get("sections") or []) if isinstance(s, dict)]
        if workers <= 1 or len(sections) <= 1:
            for sec in sections:
                skipped_tags.extend(_write_one_section(sec, round_i))
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(sections))) as pool:
                futs = [pool.submit(_write_one_section, sec, round_i) for sec in sections]
                for fut in as_completed(futs):
                    skipped_tags.extend(fut.result())

        miss1 = _count_missing(plan, outline_map, roles, all_blocks)
        if skipped_tags:
            print(f"  [round {round_i}] 本轮跳过 {len(skipped_tags)} 批：{', '.join(skipped_tags[:12])}"
                  + ("…" if len(skipped_tags) > 12 else ""))
        if miss1 == 0:
            print(f"  [round {round_i}] 全部可写块已齐套")
            break
        if round_i >= rounds:
            print(
                f"  [round {round_i}] 仍缺 {miss1} 块。"
                f"已落盘 chunks/；下次从缺失批续写（不必 --overwrite）"
            )

    payload = _content_payload(template, slug, all_blocks, summaries, chunk_notes)
    miss_final = _count_missing(plan, outline_map, roles, all_blocks)
    if miss_final == 0:
        _persist_content(content_path, payload)
        print(f"    [content] 齐套 → {content_path.name}（{len(all_blocks)} 块）")
    else:
        print(
            f"    [content] 未齐套（缺 {miss_final} 块），未写 content.json；"
            f"已落盘 {chunks_dir.as_posix()}/"
        )
    return payload


def resolve_template_dir(genre_id: int | None, theme: int | None, template_dir: str) -> Path:
    if template_dir:
        p = Path(template_dir)
        if not p.is_dir():
            alt = Path("LongDocFmtEval") / template_dir
            p = alt if alt.exists() else p
        if not p.is_dir():
            raise FileNotFoundError(f"--template-dir 不存在: {template_dir}")
        return p.resolve()
    if genre_id is None:
        raise ValueError("请指定 --id 或 --template-dir")
    tid = int(theme or 1)
    # Prefer topic-tree step4 (legacy step3)
    try:
        from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: PLC0415
            resolve_topic_dir,
            template_dir as topic_tpl,
        )
        topic = resolve_topic_dir(int(genre_id))
        cand = topic_tpl(topic, tid, 1)
        from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file as _rwf  # noqa: PLC0415
        if _rwf(cand, "template.json").is_file() or (cand / "template.docx").is_file():
            return cand.resolve()
    except Exception:  # noqa: BLE001
        pass
    draft = resolve_draft_dir(ROLE_LIBRARY_DRAFTS_DIR, int(genre_id))
    outline = resolve_outline_dir(OUTLINES_DIR, draft, tid, genre_id=int(genre_id))
    out = TEMPLATES_DIR / outline.name
    if not out.is_dir():
        raise FileNotFoundError(f"找不到 step4 模板目录: {out}")
    return out.resolve()


def _document_docx_ready(out_dir: Path) -> bool:
    """True when step6 already has a real document.docx (not an empty create_docx shell)."""
    docx = Path(out_dir) / "document.docx"
    if not docx.is_file():
        return False
    return docx.stat().st_size > 10_000


def run_one(
    template_dir: Path,
    *,
    out_dir: Path | None = None,
    overwrite: bool,
    resume: bool,
    temperature: float,
    max_tokens: int,
    no_render: bool,
    figures: bool = True,
    retries: int = DEFAULT_RETRIES,
    chunk_max: int = DEFAULT_CHUNK_MAX_BLOCKS,
    retry_wait: float = DEFAULT_RETRY_WAIT,
    fill_rounds: int = DEFAULT_FILL_ROUNDS,
    rerender: bool = False,
    chapter_workers: int = 1,
) -> bool:
    template_dir = template_dir.resolve()
    from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415
    tpl_path = resolve_work_file(template_dir, "template.json")
    template = load_json(tpl_path) if tpl_path.is_file() else {}
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415
    roles = load_merged_roles(template_dir, template)
    if not roles:
        print(f"[skip] 缺 role_params.json / template.json: {template_dir}")
        return False
    slug = template_dir.name
    plan_path = resolve_work_file(template_dir, "plan.json")
    lib_path = resolve_work_file(template_dir, "selected_library.yaml")
    if not plan_path.exists():
        print(f"[skip] 缺 plan.json: {plan_path}")
        return False
    plan = load_json(plan_path)
    lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) if lib_path.exists() else {}
    meta = plan.get("meta", {})
    locale = meta.get("locale", "zh")

    dest = prepare_step6_out_dir(template_dir, out_dir or resolve_out_dir(template_dir))
    print(f"  [out] step6 → {dest}")

    content_path = dest / "content.json"
    chunks_dir = _chunks_dir(dest)
    docx_path = dest / "document.docx"

    if rerender:
        if not content_path.is_file():
            print(f"[skip] --rerender 需要已有 content.json：{content_path}")
            return False
        content = load_json(content_path)
        print(f"[rerender] 保留 content.json，只重渲 document.docx：{dest}")
        if figures and _has_image_roles(roles):
            _build_figures(dest, overwrite=False)
        if _has_image_roles(roles):
            _write_placeholder_assets(
                dest, template, plan, lib, content.get("blocks") or {},
            )
        _render_gold(dest, content)
        return True

    if not overwrite and _document_docx_ready(dest):
        print(f"[cache] step6 已有 document.docx，跳过：{dest}")
        print("         （改渲染请加 --rerender；强制重写请加 --overwrite）")
        return True

    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    content_fresh = False
    existing = None

    if overwrite:
        if chunks_dir.is_dir():
            shutil.rmtree(chunks_dir)
            print(f"  [overwrite] 清空 {chunks_dir.name}/")
        if content_path.exists():
            content_path.unlink()
            print("  [overwrite] 删除 content.json")
        if docx_path.exists():
            docx_path.unlink()
            print("  [overwrite] 删除 document.docx")

    def _blocks_complete(blocks: dict) -> bool:
        return _count_missing(plan, outline_map, roles, blocks or {}) == 0

    skip_write = False
    content: dict[str, Any] = {}
    if content_path.exists() and not overwrite:
        content = load_json(content_path)
        if _blocks_complete(content.get("blocks") or {}):
            print(f"[cache] 已有齐套 content.json，跳过写作：{dest}（--overwrite 重写）")
            skip_write = True
        else:
            n = _count_missing(plan, outline_map, roles, content.get("blocks") or {})
            print(f"[resume] content.json 未齐套（缺 {n} 块），从 chunks/ 续写")
            existing = content

    if not skip_write:
        loaded_b, _, _ = _load_chunks(chunks_dir)
        if loaded_b and not overwrite:
            n = _count_missing(plan, outline_map, roles, loaded_b)
            print(
                f"[resume] 载入 chunks/（{len(loaded_b)} 块已落盘，缺 {n}）；"
                f"只生成缺失批"
            )
        from LongDocFmtEval.common.llm_usage import (  # noqa: PLC0415
            print_usage_summary,
            usage_scope,
            write_step_usage_report,
        )

        with usage_scope("step6") as _usage_rows:
            content = _write_content(
                dest, template, plan, lib, roles, locale,
                temperature, max_tokens, retries=retries, chunk_max=chunk_max,
                retry_wait=retry_wait, fill_rounds=fill_rounds,
                resume=(resume or bool(loaded_b) or bool(existing)) and not overwrite,
                existing=existing,
                chapter_workers=chapter_workers,
            )
            try:
                ureport = write_step_usage_report(dest, _usage_rows, step="step6")
                print_usage_summary(ureport["summary"], prefix="[step6 tokens]")
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 写出 usage/llm_usage.json 失败: {e}")
        content_fresh = _blocks_complete(content.get("blocks") or {})
        if content_fresh:
            print(f"    内容：{content_path}（{len(content.get('blocks', {}))} 块）")

    if not no_render:
        if _document_docx_ready(dest) and not overwrite and not content_fresh:
            print(f"[cache] 已有 document.docx，跳过渲染：{dest}")
            return True
        if not _blocks_complete(content.get("blocks") or {}):
            print("  [skip] 可写块未齐套，暂不渲 document.docx（续写完成后再渲）")
            return True
        # 出图（可失败）→ 补齐占位/preview 图 → 统一渲 gold（无 {Para*} 标记）
        if figures and _has_image_roles(roles):
            _build_figures(dest, overwrite=content_fresh or overwrite)
        if _has_image_roles(roles):
            _write_placeholder_assets(
                dest, template, plan, lib, content.get("blocks") or {},
            )
        _render_gold(dest, content)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Step6：按 outline 写长文 → C*/step6_document/"
    )
    ap.add_argument("--id", dest="genre_id", type=int, default=None, help="体裁 id")
    ap.add_argument("--ids", default="", help="多个体裁 id")
    ap.add_argument("--theme", type=int, default=None, help="主题 id（默认 1）")
    ap.add_argument("--themes", default="", help="多个主题 id")
    ap.add_argument("--template-dir", default="", help="显式 step3 目录（读 T*）")
    ap.add_argument(
        "--out-dir", default="",
        help="step6 输出目录（默认 C*/step6_document/）",
    )
    ap.add_argument("--all", action="store_true", help="处理全部 step3_templates")
    ap.add_argument(
        "--overwrite", action="store_true",
        help="清空 chunks/、content.json 与 document.docx 后重写",
    )
    ap.add_argument(
        "--rerender", action="store_true",
        help="不重跑 LLM：保留已有 content.json，只重渲 document.docx（改渲染算法后用这个）",
    )
    ap.add_argument(
        "--resume", action="store_true",
        help="续写/补洞：只生成 chunks/ 中缺失的批（content.json 未齐套时默认也会自动续写）",
    )
    ap.add_argument("--no-render", action="store_true", help="只写 content.json，不渲 document.docx")
    ap.add_argument("--no-figures", action="store_true", help="不出/不重出 document 配图")
    ap.add_argument("--temperature", type=float, default=0.55)
    ap.add_argument("--max-tokens", type=int, default=24576)
    ap.add_argument(
        "--retries", type=int, default=DEFAULT_RETRIES,
        help=f"单批失败最多重试次数（默认 {DEFAULT_RETRIES}）；仍失败则跳过，留给下一轮补",
    )
    ap.add_argument(
        "--retry-wait", type=float, default=DEFAULT_RETRY_WAIT,
        help="上游资源不足（503/无通道等）时，首次等待秒数；之后按 2× 指数退避（上限 600s）",
    )
    ap.add_argument(
        "--fill-rounds", type=int, default=DEFAULT_FILL_ROUNDS,
        help=f"写作轮数：第1轮写完全文，第2轮起只补跳过/缺失块（默认 {DEFAULT_FILL_ROUNDS}）",
    )
    ap.add_argument(
        "--chunk-max-blocks", type=int, default=DEFAULT_CHUNK_MAX_BLOCKS,
        help=f"单批最多可写块数，超过则拆批（默认 {DEFAULT_CHUNK_MAX_BLOCKS}）",
    )
    ap.add_argument(
        "--chapter-workers", type=int, default=None,
        help="分节写作 LLM 并发（默认见 pipeline DEFAULT_CHAPTER_WORKERS=2）",
    )
    args = ap.parse_args()

    from LongDocFmtEval.pipeline_auto.auto_paths import DEFAULT_CHAPTER_WORKERS  # noqa: PLC0415
    chapter_workers = max(1, int(
        args.chapter_workers if args.chapter_workers is not None else DEFAULT_CHAPTER_WORKERS
    ))

    if args.overwrite and args.rerender:
        print("[warn] 同时指定了 --overwrite 与 --rerender：本次只按 --rerender（不重跑 LLM）")

    dirs: list[Path] = []
    if args.all:
        dirs = sorted(p for p in TEMPLATES_DIR.iterdir() if (p / "template.json").exists())
    elif args.template_dir:
        dirs = [resolve_template_dir(None, None, args.template_dir)]
    else:
        ids = parse_ids_arg(args.ids) if args.ids else ([args.genre_id] if args.genre_id else [])
        if not ids:
            ap.error("请指定 --id / --ids / --template-dir / --all")
        themes = parse_ids_arg(args.themes) if args.themes else [int(args.theme or 1)]
        for gid in ids:
            for tid in themes:
                dirs.append(resolve_template_dir(int(gid), int(tid), ""))

    ok = 0
    for d in dirs:
        # --out-dir 仅在单目录时覆盖；--all 仍按 slug 落到 step6_document/<slug>/
        out = resolve_out_dir(d, args.out_dir if (args.out_dir and len(dirs) == 1) else "")
        if run_one(
            d,
            out_dir=out,
            overwrite=args.overwrite,
            resume=args.resume,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            no_render=args.no_render,
            figures=not args.no_figures,
            retries=max(1, args.retries),
            chunk_max=max(4, args.chunk_max_blocks),
            retry_wait=max(1.0, float(args.retry_wait)),
            fill_rounds=max(1, int(args.fill_rounds)),
            rerender=bool(args.rerender),
            chapter_workers=chapter_workers,
        ):
            ok += 1
    print(f"\n完成 {ok}/{len(dirs)} 个；产物根目录 {DOCUMENTS_DIR.resolve()}")


if __name__ == "__main__":
    main()