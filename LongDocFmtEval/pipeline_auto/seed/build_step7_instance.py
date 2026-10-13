"""Step 7 · factor-driven training instance (one sample per topic×Cx).

Outputs under ``C*/step7_pairs/<input_mode>/``:
  golden.docx / input.docx / template.docx / text_input.md / sample_info.json
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from LongDocFmtEval.pipeline_auto.auto_paths import (
    TEMPLATES_DIR,
    drop_redundant_copies,
    rel_from,
    resolve_work_file,
)
from LongDocFmtEval.pipeline_auto.seed.build_step6_pairs import (
    _genre_from_dirs,
    apply_partial_headings,
    apply_plain_transform,
    resolve_role_params,
    resolve_selected_library,
    resolve_stylesheet,
)
from LongDocFmtEval.pipeline_auto.seed.heading_contract import load_heading_index
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json
from LongDocFmtEval.pipeline_auto.seed.step7_docx_numbering import apply_document_numbering
from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import (
    build_need_role_params,
    heading_style_ids,
)
from LongDocFmtEval.pipeline_auto.seed.step7_postprocess import (
    materialize_golden_docx,
    materialize_template_docx,
)
from LongDocFmtEval.pipeline_auto.seed.step7_text_input import build_text_input_md
from LongDocFmtEval.pipeline_auto.seed.template_perturb import (
    apply_perturb_plan,
    derive_hybrid_text,
    sample_perturb_plan,
)
from LongDocFmtEval.pipeline_auto.seed.training_factors import (
    FROZEN_SEED,
    init_structure_name,
    load_pools,
    numbering_enabled,
    resolve_runtime_factors,
    scheme_id,
    text_block,
    uses_template_docx,
    uses_text_input,
)

_LEGACY_STEP7_FILES = (
    "input_plain.docx",
    "input_partial.docx",
    "t_text.md",
    "t_text_from_partial.md",
    "t_text_meta.json",
    "t_text_from_partial_meta.json",
    "t_style_propose.md",
    "t_template_style.docx",
    "t_template_direct.docx",
    "t_template_styled.docx",
    "document.docx",
    "sample.json",
    "source_step7.json",
    "manifest.json",
    "channels.json",
)


def _build_input_docx(
    step6_src: Path,
    out_path: Path,
    *,
    init_structure: str,
    init_text_numbered: bool,
    scheme_id: str | None,
    step5_dir: Path,
    slug: str,
    seed: int,
    role_params: dict,
    map_json: dict,
) -> dict[str, Any]:
    """Build init from **unnumbered** step6 document (not from golden)."""
    init_structure = str(init_structure or "plain").strip().lower()
    plain_tmp = out_path.parent / "_plain_base.docx"
    plain_meta = apply_plain_transform(step6_src, plain_tmp, seed=seed)
    meta: dict[str, Any] = {
        "init_structure": init_structure,
        "init_text_numbered": bool(init_text_numbered),
        "plain": plain_meta,
    }

    if init_structure == "partial":
        ss_idx = load_heading_index(
            resolve_work_file(step5_dir, "plan.json"),
            resolve_work_file(step5_dir, "stylesheet.json"),
            step5_dir.parent / "step3_outlines" / "plan.json",
            step5_dir.parent / "step3_outlines" / "stylesheet.json",
            TEMPLATES_DIR / slug / "plan.json",
        )
        partial_meta = apply_partial_headings(
            plain_tmp,
            out_path,
            golden=step6_src,
            map_json=map_json,
            role_params=role_params,
            heading_map=ss_idx or None,
        )
        meta["partial"] = partial_meta
        if plain_tmp.is_file():
            plain_tmp.unlink()
    else:
        shutil.copy2(plain_tmp, out_path)
        if plain_tmp.is_file():
            plain_tmp.unlink()

    if init_text_numbered and scheme_id:
        # Plan from unnumbered step6 (keeps outlineLvl); apply to init which may be plain.
        rep = apply_document_numbering(
            out_path,
            scheme_id=str(scheme_id),
            outline_source=step6_src,
        )
        meta["numbering"] = rep
        meta["numbered"] = True
    else:
        meta["numbered"] = False
    return meta


def _build_template_docx(
    tpl_src: Path,
    out_path: Path,
    *,
    role_params: dict,
    map_json: dict,
    styles_json: dict,
) -> dict[str, Any]:
    """Keep paragraph styles; only strip markers (no style vs direct distinction)."""
    del role_params, map_json, styles_json
    from LongDocFmtEval.pipeline_auto.seed.strip_para_markers import write_clean_docx  # noqa: PLC0415
    inplace = tpl_src.resolve() == out_path.resolve()
    if inplace:
        tmp = out_path.parent / "_tpl_strip_tmp.docx"
        _, n_stripped = write_clean_docx(out_path, tmp)
        shutil.copy2(tmp, out_path)
        tmp.unlink(missing_ok=True)
    else:
        _, n_stripped = write_clean_docx(tpl_src, out_path)
    return {
        "template_mode": None,
        "kind": "style_keep",
        "stripped_markers": n_stripped,
    }


def _purge_legacy(out_dir: Path) -> list[str]:
    removed: list[str] = []
    for name in _LEGACY_STEP7_FILES:
        p = out_dir / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    return removed


def build_step7_instance(
    step6_dir: Path,
    out_dir: Path,
    factors: dict[str, Any],
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Build one training instance from step6 golden + training factors."""
    step6_dir = step6_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    golden_src = step6_dir / "document.docx"
    if not golden_src.is_file():
        raise FileNotFoundError(f"missing golden document: {golden_src}")

    step5_dir = step6_dir.parent / "step5_template"
    cx_dir = step6_dir.parent
    topic_dir = cx_dir.parent
    slug = topic_dir.name

    dropped = drop_redundant_copies(out_dir)
    dropped.extend(_purge_legacy(out_dir))

    role_params = resolve_role_params(slug, step5_dir)
    lib = resolve_selected_library(slug, step5_dir)
    stylesheet = resolve_stylesheet(slug, step5_dir)
    genre = _genre_from_dirs(slug, step5_dir)
    locale_primary = str(factors.get("locale") or (lib or {}).get("locale") or "zh")
    if not locale_primary:
        locale_primary = "en" if "_en_" in slug else "zh"

    map_path = resolve_work_file(step5_dir, "map.json")
    styles_path = resolve_work_file(step5_dir, "styles.json")
    map_json = load_json(map_path) if map_path.is_file() else {}
    styles_json = load_json(styles_path) if styles_path.is_file() else {}
    head_ids = heading_style_ids(stylesheet, role_params)

    # Normalize nested init / text factors
    factors = resolve_runtime_factors(
        factors, master_seed=FROZEN_SEED, pools=load_pools(),
    )

    hybrid: dict[str, Any] = {}
    perturb_plan: dict[str, Any] = {}
    if str(factors.get("input_mode") or "") == "template_w_text":
        sample_key = str(
            factors.get("sample_key")
            or f"{cx_dir.parent.name}_{cx_dir.name}_template_w_text"
        )
        hybrid = factors.get("hybrid_text") if isinstance(factors.get("hybrid_text"), dict) else {}
        if not hybrid:
            hybrid = derive_hybrid_text(sample_key, master_seed=FROZEN_SEED)
        factors = {**factors, "hybrid_text": hybrid}

    golden_path = out_dir / "golden.docx"
    golden_meta = materialize_golden_docx(
        step6_dir=step6_dir,
        cx_dir=cx_dir,
        out_dir=out_dir,
        factors=factors,
    )

    # init from unnumbered step6, then optional heading styles + optional prefixes
    input_path = out_dir / "input.docx"
    input_meta = _build_input_docx(
        golden_src,
        input_path,
        init_structure=init_structure_name(factors),
        init_text_numbered=numbering_enabled(factors),
        scheme_id=scheme_id(factors),
        step5_dir=step5_dir,
        slug=slug,
        seed=seed,
        role_params=role_params,
        map_json=map_json,
    )

    need_main, diff_meta = build_need_role_params(
        role_params,
        golden=golden_path,
        init=input_path,
        styles=styles_json or {},
        maps=map_json or {},
        locale=locale_primary,
        stylesheet=stylesheet,
    )

    template_path = out_dir / "template.docx"
    tpl_meta: dict[str, Any] = {}
    if uses_template_docx(factors):
        tpl_meta = materialize_template_docx(
            step5_dir=step5_dir,
            cx_dir=cx_dir,
            out_dir=out_dir,
            factors=factors,
            build_fn=lambda src, dst: _build_template_docx(
                src,
                dst,
                role_params=role_params,
                map_json=map_json,
                styles_json=styles_json,
            ),
        )
        if hybrid.get("include_delta") and template_path.is_file():
            sample_key = str(factors.get("sample_key") or "")
            perturb_plan = sample_perturb_plan(
                sample_key=sample_key,
                stylesheet=stylesheet,
                role_params=role_params,
                styles_json=styles_json,
                map_json=map_json,
                locale=locale_primary,
                master_seed=FROZEN_SEED,
            )
            if perturb_plan.get("ok"):
                # template.docx stays the reference; golden.docx gets the target props.
                apply_meta = apply_perturb_plan(
                    golden_path,
                    perturb_plan,
                    styles_json=styles_json,
                )
                delta_record = {
                    "sample_key": factors.get("sample_key"),
                    "hybrid_text": hybrid,
                    "apply_target": "golden.docx",
                    "plan": perturb_plan,
                    "apply": apply_meta,
                }
                golden_meta["delta_perturb"] = delta_record
                tpl_meta["template_perturb"] = {
                    "plan": perturb_plan,
                    "apply_target": "golden.docx",
                    "apply": apply_meta,
                }
                dump_json(out_dir / "delta_plan.json", delta_record)
            else:
                hybrid = {
                    "combo": "catalog_only",
                    "include_catalog": True,
                    "include_delta": False,
                    "fallback_from_delta_fail": True,
                    "perturb_warning": perturb_plan.get("warning"),
                }
                factors = {**factors, "hybrid_text": hybrid}
                tpl_meta["template_perturb"] = perturb_plan
    elif template_path.is_file():
        template_path.unlink()

    text_input_path = out_dir / "text_input.md"
    if uses_text_input(factors):
        text_input_path.write_text(
            build_text_input_md(
                role_params=role_params,
                genre=genre,
                lib=lib,
                stylesheet=stylesheet,
                heading_ids=head_ids,
                init_docx=input_path,
                golden=golden_path,
                diff_meta=diff_meta,
                factors=factors,
                locale_primary=locale_primary,
                map_json=map_json,
                styles_json=styles_json,
                perturb_plan=perturb_plan if hybrid.get("include_delta") else None,
            ),
            encoding="utf-8",
        )
    elif text_input_path.is_file():
        text_input_path.unlink()

    tpl_src = resolve_work_file(step5_dir, "template.docx") if uses_template_docx(factors) else None
    init_blk = factors.get("init") if isinstance(factors.get("init"), dict) else {}
    text_blk = text_block(factors) if uses_text_input(factors) else None
    has_delta_plan = bool(
        hybrid.get("include_delta")
        and isinstance(tpl_meta.get("template_perturb"), dict)
        and (tpl_meta.get("template_perturb") or {}).get("plan", {}).get("ok")
    )
    sample_info: dict[str, Any] = {
        "version": 6,
        "step": 7,
        "sample_key": factors.get("sample_key"),
        "content_key": factors.get("content_key"),
        "topic_id": factors.get("topic_id"),
        "complexity": factors.get("complexity"),
        "input_mode": factors.get("input_mode"),
        "locale": locale_primary,
        "domain_id": factors.get("domain_id"),
        "fine_domain_id": factors.get("fine_domain_id"),
        "genre_display_name": factors.get("genre_display_name"),
        "init": init_blk,
        "text": text_blk,
        "hybrid_text": hybrid or None,
        "template_perturb": (tpl_meta.get("template_perturb") if tpl_meta else None),
        "files": {
            "golden": "golden.docx",
            "input": "input.docx",
            "template": "template.docx" if template_path.is_file() else None,
            "text_input": "text_input.md" if uses_text_input(factors) else None,
            "delta_plan": "delta_plan.json" if has_delta_plan else None,
        },
        "sources": {
            "golden": rel_from(golden_src, out_dir),
            "template": rel_from(tpl_src, out_dir) if tpl_src and tpl_src.is_file() else None,
        },
        "build": {
            "golden": golden_meta,
            "input": input_meta,
            "template": tpl_meta,
        },
    }
    dump_json(out_dir / "sample_info.json", sample_info)
    return sample_info


def parse_sample_key_from_step6(step6_dir: Path) -> tuple[int, str]:
    cx_dir = step6_dir.resolve().parent
    cx = cx_dir.name.upper()
    if not cx.startswith("C"):
        raise ValueError(f"expected C*/step6_document, got {step6_dir}")
    topic_dir = cx_dir.parent
    tid = int(str(topic_dir.name).split("_", 1)[0])
    return tid, cx


def load_factors_for_step6(
    step6_dir: Path,
    *,
    global_factors_path: Path | None = None,
    input_mode: str = "",
) -> dict[str, Any]:
    from LongDocFmtEval.pipeline_auto.seed.training_factors import (  # noqa: PLC0415
        DEFAULT_OUTPUT,
        assignment_for,
        assignments_for_content,
    )

    tid, cx = parse_sample_key_from_step6(step6_dir)
    path = global_factors_path or DEFAULT_OUTPUT
    if not path.is_file():
        raise FileNotFoundError(f"missing datasets_factors.json: {path}")
    data = load_json(path)
    im = str(input_mode or "").strip().lower()
    if im:
        row = assignment_for(data, tid, cx, input_mode=im)
        if not row:
            raise KeyError(f"no factors for {tid}_{cx}_{im} in {path}")
        return row
    rows = assignments_for_content(data, tid, cx)
    if not rows:
        raise KeyError(f"no factors for {tid}_{cx} in {path}")
    return rows[0]
