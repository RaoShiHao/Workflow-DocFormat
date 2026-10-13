"""C2 delta for ``template_w_text`` samples.

Sample 2–5 stylesheet roles, change 1–4 props each from locale-specific pools.
``template.docx`` is **never** modified — it stays the format reference.
Delta text describes adjustments relative to template; only ``golden.docx``
receives the target (``to``) props via officecli ``set``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from LongDocFmtEval.common.officecli_runner import close_docx, officecli_batch_session, set_props
from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR
from LongDocFmtEval.pipeline_auto.seed.extract_style_tree import _para_index_for_role
from LongDocFmtEval.pipeline_auto.seed.step6_t_text import (
    describe_prop_change,
    public_display_name,
)
from LongDocFmtEval.pipeline_auto.seed.training_factors import FROZEN_SEED, rng_for
from LongDocFmtEval.pipeline_auto.style_taxonomy import is_table_paragraph_object

DEFAULT_PERTURB_POOLS = CONFIG_DIR / "template_perturb_pools.yaml"
HYBRID_COMBOS = ("catalog_only", "delta_only", "both")
MAX_PERTURB_RETRIES = 8


def load_perturb_pools(
    path: Path | None = None,
    *,
    locale: str = "zh",
) -> dict[str, dict[str, list[Any]]]:
    p = path or DEFAULT_PERTURB_POOLS
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    if not isinstance(raw, dict):
        return {}
    loc = "en" if str(locale or "").lower().startswith("en") else "zh"
    para = raw.get(f"paragraph_{loc}") or raw.get("paragraph") or {}
    section = raw.get(f"section_{loc}") or raw.get("section") or {}
    return {
        "paragraph": {
            str(k): list(v) for k, v in para.items() if isinstance(v, list) and v
        },
        "section": {
            str(k): list(v) for k, v in section.items() if isinstance(v, list) and v
        },
    }


def derive_hybrid_text(sample_key: str, *, master_seed: int = FROZEN_SEED) -> dict[str, Any]:
    """Equal thirds: catalog_only / delta_only / both (never neither)."""
    rng = rng_for(f"{sample_key}:hybrid", master_seed)
    combo = rng.choice(list(HYBRID_COMBOS))
    return {
        "combo": combo,
        "include_catalog": combo in ("catalog_only", "both"),
        "include_delta": combo in ("delta_only", "both"),
    }


def _norm_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low
    return s


def _vals_equal(a: Any, b: Any) -> bool:
    """Treat missing / empty / false / zero lengths as equivalent where safe."""
    na, nb = _norm_val(a), _norm_val(b)
    if na == nb:
        return True
    falsy = {"", "false", "0", "none"}
    if na in falsy and nb in falsy:
        return True
    zero_len = {"", "0", "0pt", "0cm", "0mm", "0in", "0em", "none"}
    return na.lower() in zero_len and nb.lower() in zero_len


def _object_bucket(obj: str = "", rid: str = "") -> str:
    o = str(obj or "").strip().lower()
    r = str(rid or "")
    if o.startswith("section") or r.startswith("Sec"):
        return "section"
    if o.startswith("run") or r.startswith("Run"):
        return "run"
    if o.startswith("table") or o in {"tbl"} or r.startswith("Tbl"):
        return "table"
    if o.startswith("image") or r.startswith("Img"):
        return "image"
    if is_table_paragraph_object(o, r):
        return "paragraph"
    if o.startswith("paragraph") or r.startswith("Para"):
        return "paragraph"
    return ""


def _flat_role_props(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    props = spec.get("props") if isinstance(spec.get("props"), dict) else {}
    return {k: v for k, v in props.items() if not isinstance(v, (dict, list))}


def collect_perturbable_roles(
    stylesheet: list | None,
    role_params: dict[str, Any],
    *,
    styles_json: dict[str, Any],
    map_json: dict[str, Any],
    pools: dict[str, dict[str, list[Any]]],
) -> list[dict[str, Any]]:
    """Roles that have a para_index and at least one pool prop."""
    para_pool = pools.get("paragraph") or {}
    if not para_pool:
        return []
    entries: list[dict] = [e for e in (stylesheet or []) if isinstance(e, dict)]
    if not entries:
        for rid, spec in (role_params or {}).items():
            if isinstance(spec, dict):
                entries.append({"style_id": rid, **spec})
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        rid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not rid or rid in seen:
            continue
        bucket = _object_bucket(str(item.get("object") or ""), rid)
        if bucket != "paragraph":
            continue
        idx = _para_index_for_role(styles_json or {}, map_json or {}, rid)
        if idx is None or idx < 1:
            continue
        spec = role_params.get(rid) if isinstance(role_params.get(rid), dict) else item
        current = _flat_role_props(spec if isinstance(spec, dict) else {})
        eligible_props = [
            p for p, cands in para_pool.items()
            if any(not _vals_equal(c, current.get(p)) for c in cands)
        ]
        if not eligible_props:
            continue
        seen.add(rid)
        out.append({
            "style_id": rid,
            "display_name": public_display_name(
                rid,
                item.get("display_name"),
                (spec or {}).get("display_name") if isinstance(spec, dict) else None,
            ),
            "para_index": int(idx),
            "current_props": current,
            "eligible_props": eligible_props,
        })
    return out


def _pick_new_value(rng, current: Any, candidates: list[Any]) -> Any | None:
    alts = [c for c in candidates if not _vals_equal(c, current)]
    if not alts:
        return None
    return rng.choice(alts)


def sample_perturb_plan(
    *,
    sample_key: str,
    stylesheet: list | None,
    role_params: dict[str, Any],
    styles_json: dict[str, Any],
    map_json: dict[str, Any],
    pools: dict[str, dict[str, list[Any]]] | None = None,
    locale: str = "zh",
    master_seed: int = FROZEN_SEED,
    max_retries: int = MAX_PERTURB_RETRIES,
) -> dict[str, Any]:
    """Build a perturb plan; redo until ≥1 style has real changes or give up."""
    pools = pools or load_perturb_pools(locale=locale)
    para_pool = pools.get("paragraph") or {}
    roles = collect_perturbable_roles(
        stylesheet, role_params, styles_json=styles_json, map_json=map_json, pools=pools,
    )
    report: dict[str, Any] = {
        "ok": False,
        "n_pool": len(roles),
        "retry_count": 0,
        "edits": [],
        "n_styles": 0,
    }
    if len(roles) < 1:
        report["warning"] = "no perturbable paragraph roles with map para_index"
        return report

    for attempt in range(max(1, int(max_retries))):
        rng = rng_for(f"{sample_key}:tpl_delta:{attempt}", master_seed)
        want_n = rng.randint(2, 5)
        n_styles = min(want_n, len(roles))
        chosen = rng.sample(roles, k=n_styles)
        edits: list[dict[str, Any]] = []
        for role in chosen:
            eligible = list(role["eligible_props"])
            if not eligible:
                continue
            n_props = rng.randint(1, min(4, len(eligible)))
            props_pick = rng.sample(eligible, k=n_props)
            changes: list[dict[str, Any]] = []
            current = role["current_props"]
            for prop in props_pick:
                cands = para_pool.get(prop) or []
                new_v = _pick_new_value(rng, current.get(prop), cands)
                if new_v is None:
                    continue
                changes.append({
                    "prop": prop,
                    "from": current.get(prop),
                    "to": new_v,
                })
            if not changes:
                continue
            edits.append({
                "style_id": role["style_id"],
                "display_name": role["display_name"] or role["style_id"],
                "para_index": role["para_index"],
                "changes": changes,
            })
        report["retry_count"] = attempt
        report["n_styles"] = len(edits)
        report["edits"] = edits
        if edits:
            report["ok"] = True
            return report

    report["warning"] = f"perturb failed after {max_retries} retries"
    return report


def apply_perturb_plan(
    docx_path: Path,
    plan: dict[str, Any],
    *,
    styles_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply planned prop changes to ``docx_path`` (golden target).

    Paragraph roles: ``set`` on ``/body/p[N]``; also ``/styles/{id}`` **only when that
    style already exists** in the docx (skip phantom ids — they used to poison atomic
    ``officecli batch`` and force sequential replay).
    """
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import style_props_for_paragraph  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.seed.word_style_resolve import (  # noqa: PLC0415
        existing_style_ids,
        read_docx_parts,
        resolve_pipeline_write_style,
    )

    docx_path = Path(docx_path).resolve()
    styles_json = styles_json or {}
    applied = 0
    style_applied = 0
    style_skipped = 0
    errors: list[str] = []
    ocli_mode = "sequential"
    ocli_cmds = 0
    ocli_batches = 0

    existing: set[str] = set()
    try:
        parts = read_docx_parts(docx_path)
        existing = existing_style_ids(parts.get("word/styles.xml") or b"")
    except Exception:  # noqa: BLE001
        existing = set()

    try:
        with officecli_batch_session(docx_path) as sess:
            for edit in plan.get("edits") or []:
                idx = int(edit.get("para_index") or 0)
                changes = edit.get("changes") or []
                if idx < 1 or not changes:
                    continue
                props = {str(c["prop"]): c["to"] for c in changes if c.get("prop")}
                if not props:
                    continue
                sid = str(edit.get("style_id") or "")
                style_loc = (styles_json.get("paragraph") or {}).get(sid) or {}
                preferred = str(
                    style_loc.get("style_id") or style_loc.get("word_style") or sid,
                ).strip()
                try:
                    # Paragraph direct formatting accepts the full perturb pool.
                    set_props(docx_path, f"/body/p[{idx}]", props)
                    applied += 1
                    if preferred and preferred not in ("Normal", "normal", "a"):
                        write_id = resolve_pipeline_write_style(docx_path, preferred)
                        # Style element rejects some keys (notably ``indent``) — filter
                        # or officecli exit 2 poisons atomic batch for the whole chunk.
                        style_props = style_props_for_paragraph(props)
                        if write_id and write_id in existing and style_props:
                            set_props(docx_path, f"/styles/{write_id}", style_props)
                            style_applied += 1
                        else:
                            style_skipped += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{edit.get('style_id')} p[{idx}]: {exc}"[:200])
            ocli_mode = sess.mode
            ocli_cmds = int(sess.n_enqueued)
            ocli_batches = int(sess.n_batches)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"batch/apply: {exc}"[:200])
    try:
        close_docx(docx_path)
    except Exception:  # noqa: BLE001
        pass
    if ocli_cmds:
        print(
            f"  [officecli] step7 delta {ocli_mode}: "
            f"cmds≈{ocli_cmds} batches={ocli_batches} "
            f"para={applied} style_set={style_applied} style_skip={style_skipped}"
        )
    return {
        "applied_styles": applied,
        "style_applied": style_applied,
        "style_skipped": style_skipped,
        "errors": errors,
        "target": str(docx_path),
        "officecli_mode": ocli_mode,
        "officecli_cmds": ocli_cmds,
        "officecli_batches": ocli_batches,
    }


def render_delta_md(
    plan: dict[str, Any],
    *,
    locale: str = "zh",
    as_section: bool = False,
) -> str:
    """Rule-based relative-to-template instructions using display_name."""
    edits = plan.get("edits") or []
    if not edits:
        return ""
    en = str(locale or "").lower().startswith("en")
    lines: list[str] = []
    title = (
        "Adjustments relative to the template"
        if en
        else "相对模板的格式调整说明"
    )
    intro = (
        "Please base formatting on `template.docx`, but apply the following changes "
        "(styles not listed may keep the template formatting)."
        if en
        else "请以 template.docx 为格式基准进行调整，但下列样式需按说明修改"
        "（未列出的部分可沿用模板）。"
    )
    heading = f"## {title}" if as_section else f"# {title}"
    lines.append(heading)
    lines.append("")
    lines.append(intro)
    lines.append("")
    for edit in edits:
        name = str(edit.get("display_name") or "").strip() or str(edit.get("style_id") or "")
        clauses: list[str] = []
        for change in edit.get("changes") or []:
            prop = str(change.get("prop") or "").strip()
            if not prop:
                continue
            clause = describe_prop_change(
                prop,
                change.get("from"),
                change.get("to"),
                locale=locale,
            ).strip()
            if clause:
                clauses.append(clause)
        if not clauses:
            continue
        if en:
            body = ", ".join(clauses)
            if not body.endswith("."):
                body = body + "."
            lines.append(f"- For **{name}**, {body}")
        else:
            body = "；".join(clauses)
            if not body.endswith(("。", "！", "？")):
                body = body + "。"
            lines.append(f"- 对于 **{name}**，{body}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
