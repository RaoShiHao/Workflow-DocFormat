"""Step7-only postprocess: copy golden/template into step7_pairs and modify in place.

Step5/step6 stay read-only. Numbering = prefix insert via docx outlineLvl (no rerender).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file
from LongDocFmtEval.pipeline_auto.seed.heading_numbering import targets_from_factors
from LongDocFmtEval.pipeline_auto.seed.step7_docx_numbering import apply_document_numbering
from LongDocFmtEval.pipeline_auto.seed.training_factors import scheme_id as scheme_id_from_factors


def materialize_golden_docx(
    *,
    step6_dir: Path,
    cx_dir: Path,
    out_dir: Path,
    factors: dict[str, Any],
) -> dict[str, Any]:
    """Copy step6 document.docx → golden.docx; insert numbering when required."""
    del cx_dir  # reserved for future cx-level sidecars
    out_path = out_dir / "golden.docx"
    src_doc = step6_dir / "document.docx"
    if not src_doc.is_file():
        raise FileNotFoundError(f"missing step6 document: {src_doc}")

    shutil.copy2(src_doc, out_path)
    meta: dict[str, Any] = {
        "source_step6": str(src_doc).replace("\\", "/"),
        "numbered": False,
        "mode": "copy",
    }

    if not targets_from_factors(factors)["document"]:
        return meta

    sid = scheme_id_from_factors(factors)
    if not sid:
        meta["warning"] = "document_numbered but scheme_id is null"
        return meta

    rep = apply_document_numbering(out_path, scheme_id=sid)
    if int(rep.get("heading_paragraphs") or 0) == 0:
        meta["warning"] = "document_numbered but no outlineLvl headings found in golden.docx"
    meta.update({"numbered": True, "mode": "insert_prefix_outline", "numbering": rep})
    return meta


def materialize_template_docx(
    *,
    step5_dir: Path,
    cx_dir: Path,
    out_dir: Path,
    factors: dict[str, Any],
    build_fn: Callable[[Path, Path], dict[str, Any]],
) -> dict[str, Any]:
    """Copy step5 template.docx → template.docx; transform + numbering in place."""
    del cx_dir
    out_path = out_dir / "template.docx"
    tpl_src = resolve_work_file(step5_dir, "template.docx")
    meta: dict[str, Any] = {
        "source_step5": str(tpl_src).replace("\\", "/") if tpl_src and tpl_src.is_file() else None,
        "numbered": False,
        "mode": "copy",
    }

    if not tpl_src or not tpl_src.is_file():
        meta["warning"] = "no template.docx found; template.docx not written"
        return meta

    shutil.copy2(tpl_src, out_path)
    meta["copied_from"] = str(tpl_src).replace("\\", "/")

    tpl_meta = build_fn(out_path, out_path)
    meta.update(tpl_meta)

    if not targets_from_factors(factors)["template"]:
        return meta

    sid = scheme_id_from_factors(factors)
    if not sid:
        meta["warning"] = "template_numbered but scheme_id is null"
        return meta

    rep = apply_document_numbering(out_path, scheme_id=sid)
    if int(rep.get("heading_paragraphs") or 0) == 0:
        meta["warning"] = "template_numbered but no outlineLvl headings found in template.docx"
    meta.update({"numbered": True, "numbering": rep})
    return meta
