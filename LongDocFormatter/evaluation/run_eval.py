"""Evaluate one LongDocFormatter sample (whitelist flats + integrity).

Golden/source flats live at ``{sample_dir}/eval/`` (lazy: create if missing, then reuse).
``output.flat.json`` lives next to the model ``output.docx`` the same way: snapshot
once with officecli ``profile=eval``, later evals compare JSON files only.

Cold reads of input / golden / output reuse the same ``query paragraph`` /
``query picture`` rows for flatten and the integrity stream (no second query
pass, no template-only neighbor / section-preview gets).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from LongDocFormatter.workflow.eval_acc import (
    FLAT_SCHEMA,
    flatten_doc,
    same_document,
    snapshot,
    snapshot_document,
    split_need_no_change,
)

from .integrity import (
    IntegrityReport,
    check_document_integrity,
    collect_content_stream,
    save_integrity_report,
    tokens_from_json,
    tokens_to_json,
)
from .metrics import SampleMetrics, metrics_from_flats, save_metrics_report

EVAL_DIR = "eval"
SOURCE_STREAM = "source.stream.json"
OUTPUT_STREAM = "output.stream.json"
FLAT_SCHEMA_FILE = "flat_schema.txt"


def _log(msg: str) -> None:
    print(f"[eval] {msg}", flush=True)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _schema_fresh(dir_path: Path) -> bool:
    marker = dir_path / FLAT_SCHEMA_FILE
    if not marker.is_file():
        return False
    return marker.read_text(encoding="utf-8").strip() == str(FLAT_SCHEMA)


def _write_schema(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / FLAT_SCHEMA_FILE).write_text(f"{FLAT_SCHEMA}\n", encoding="utf-8")


def _cache_fresh(cache: Path, *sources: Path) -> bool:
    if not cache.is_file():
        return False
    stamp = cache.stat().st_mtime
    for src in sources:
        if src.is_file() and src.stat().st_mtime > stamp:
            return False
    return True


def resolve_source_docx(sample_dir: Path) -> Path:
    from LongDocFormatter.experiment.paths import find_source_docx

    return find_source_docx(sample_dir)


def resolve_golden_docx(sample_dir: Path) -> Path:
    from LongDocFormatter.experiment.paths import GOLDEN_NAME, load_sample_info

    named = (load_sample_info(sample_dir).get("files") or {}).get("golden") or GOLDEN_NAME
    path = Path(sample_dir) / str(named)
    if path.is_file():
        return path
    raise FileNotFoundError(f"golden.docx not found in {sample_dir}")


def reference_eval_dir(sample_dir: Path, cache_dir: Path | None = None) -> Path:
    """Dataset-local eval cache: ``{sample_dir}/eval``."""
    del cache_dir
    return Path(sample_dir).resolve() / EVAL_DIR


def output_eval_dir(output_docx: Path) -> Path:
    return output_docx.resolve().parent / EVAL_DIR


def _load_stream(path: Path):
    data = _load_json(path)
    if isinstance(data, dict):
        return tokens_from_json(data.get("tokens"))
    return tokens_from_json(data)


def _save_stream(path: Path, tokens) -> None:
    _save_json(path, {"tokens": tokens_to_json(tokens)})


def _ensure_stream(doc: Path, cache: Path, *, use_cache: bool, tokens=None):
    if tokens is not None:
        _save_stream(cache, tokens)
        return tokens
    if use_cache and _cache_fresh(cache, doc):
        _log(f"reuse {cache}")
        return _load_stream(cache)
    _log(f"integrity stream via officecli → {cache}")
    stream = collect_content_stream(doc)
    _save_stream(cache, stream)
    return stream


def _flatten_cached_source(sample_dir: Path, cache_dir: Path | None) -> dict[str, Any] | None:
    """Reuse a *full/eval* ``inventory_source.json`` so eval does not re-open source.docx.

    Assign-profile source caches omit section props / HF / cell paras and cannot
    supply ``source.flat``.
    """
    from LongDocFormatter.experiment.paths import DEFAULT_CACHE_ROOT
    from LongDocFormatter.workflow.element_io import (
        INVENTORY_SCHEMA,
        inventory_profile_of,
        inventory_schema_of,
        load_elements_json,
    )

    root = Path(cache_dir) if cache_dir else DEFAULT_CACHE_ROOT / Path(sample_dir).name
    path = root / "inventory_source.json"
    if not path.is_file() or inventory_schema_of(path) < INVENTORY_SCHEMA:
        return None
    if inventory_profile_of(path) in ("", "assign"):
        return None
    elements = load_elements_json(path, cache_dir=root)
    secs = elements.get("section") or []
    if secs and not any(dict(getattr(el, "props", None) or {}) for el in secs):
        return None
    return flatten_doc(elements)


def ensure_reference_flats(
    sample_dir: Path,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    tol: float = 0.15,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    sample_dir = sample_dir.resolve()
    source = resolve_source_docx(sample_dir)
    golden = resolve_golden_docx(sample_dir)
    ref = reference_eval_dir(sample_dir, cache_dir=cache_dir)
    src_path = ref / "source.flat.json"
    gold_path = ref / "golden.flat.json"
    need_path = ref / "need_check.json"
    stay_path = ref / "no_change_check.json"
    src_stream_path = ref / SOURCE_STREAM
    cached = (
        use_cache
        and _schema_fresh(ref)
        and _cache_fresh(src_path, source)
        and _cache_fresh(gold_path, golden)
        and need_path.is_file()
        and stay_path.is_file()
    )
    if cached:
        _log(f"reuse {ref}")
        _ensure_stream(source, src_stream_path, use_cache=use_cache)
        return (
            _load_json(src_path),
            _load_json(gold_path),
            _load_json(need_path).get("check_points") or {},
            _load_json(stay_path).get("check_points") or {},
        )
    source_flat = _flatten_cached_source(sample_dir, cache_dir) if use_cache else None
    source_stream = None
    if source_flat is not None:
        _log(f"source.flat from inventory cache → {ref}")
    else:
        _log(f"snapshot source/golden via officecli → {ref}")
        source_flat, source_stream = snapshot_document(source)
    golden_flat = snapshot(golden)
    need, stay = split_need_no_change(golden_flat, source_flat, tol=tol)
    _save_json(src_path, source_flat)
    _save_json(gold_path, golden_flat)
    _save_json(need_path, {"check_points": need})
    _save_json(stay_path, {"check_points": stay})
    _write_schema(ref)
    _ensure_stream(source, src_stream_path, use_cache=False, tokens=source_stream)
    return source_flat, golden_flat, need, stay


def ensure_output_flat(
    output_docx: Path,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    output_docx = Path(output_docx).resolve()
    pred_path = output_eval_dir(output_docx) / "output.flat.json"
    stream_path = output_eval_dir(output_docx) / OUTPUT_STREAM
    if use_cache and _schema_fresh(pred_path.parent) and _cache_fresh(pred_path, output_docx):
        _log(f"reuse {pred_path}")
        _ensure_stream(output_docx, stream_path, use_cache=use_cache)
        return _load_json(pred_path)
    legacy = output_docx.parent / "output.flat.json"
    if use_cache and _schema_fresh(pred_path.parent) and _cache_fresh(legacy, output_docx):
        _log(f"reuse {legacy}")
        pred = _load_json(legacy)
        _save_json(pred_path, pred)
        _ensure_stream(output_docx, stream_path, use_cache=use_cache)
        return pred
    _log(f"snapshot output.docx via officecli → {pred_path}")
    pred, stream = snapshot_document(output_docx)
    _save_json(pred_path, pred)
    _write_schema(pred_path.parent)
    _ensure_stream(output_docx, stream_path, use_cache=False, tokens=stream)
    return pred


def ensure_integrity(
    source: Path,
    output_docx: Path,
    *,
    sample_dir: Path | None = None,
    use_cache: bool = True,
) -> IntegrityReport:
    output_docx = Path(output_docx).resolve()
    source = Path(source).resolve()
    path = output_eval_dir(output_docx) / "integrity.json"
    if use_cache and _cache_fresh(path, source, output_docx):
        _log(f"reuse {path}")
        return IntegrityReport.from_dict(_load_json(path))
    ref_dir = reference_eval_dir(sample_dir) if sample_dir else source.parent / EVAL_DIR
    _log("integrity compare cached streams")
    ref_stream = _ensure_stream(source, ref_dir / SOURCE_STREAM, use_cache=use_cache)
    cand_stream = _ensure_stream(
        output_docx, output_eval_dir(output_docx) / OUTPUT_STREAM, use_cache=use_cache
    )
    report = check_document_integrity(
        source,
        output_docx,
        reference_stream=ref_stream,
        candidate_stream=cand_stream,
    )
    save_integrity_report(report, path)
    return report


def prepare_output_eval_cache(
    sample_dir: Path,
    output_docx: Path,
    *,
    use_cache: bool = True,
    tol: float = 0.15,
) -> None:
    """Officecli read-side eval artifacts for one model output (no scoring).

    Ensures dataset reference flats (``{sample_dir}/eval/``), model
    ``output.flat.json``, and ``integrity.json`` next to ``output.docx``.
    """
    sample_dir = Path(sample_dir).resolve()
    output_docx = Path(output_docx).resolve()
    source = resolve_source_docx(sample_dir)
    ensure_reference_flats(sample_dir, use_cache=use_cache, tol=tol)
    ensure_output_flat(output_docx, use_cache=use_cache)
    ensure_integrity(source, output_docx, sample_dir=sample_dir, use_cache=use_cache)


def evaluate_sample(
    sample_dir: Path,
    output_docx: Path,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    tol: float = 0.15,
    quiet: bool = False,
) -> SampleMetrics:
    sample_dir = Path(sample_dir).resolve()
    output_docx = Path(output_docx).resolve()
    del cache_dir
    source = resolve_source_docx(sample_dir)
    golden = resolve_golden_docx(sample_dir)
    result_dir = output_eval_dir(output_docx)
    result_dir.mkdir(parents=True, exist_ok=True)
    _log(f"sample {sample_dir.name}  output={output_docx}")

    source_flat, golden_flat, _need, _stay = ensure_reference_flats(
        sample_dir, use_cache=use_cache, tol=tol
    )
    # Byte-identical copy of input/golden → reuse reference flats (no re-flatten drift).
    if same_document(output_docx, golden):
        _log("output ≡ golden.docx → reuse golden.flat")
        pred = dict(golden_flat)
        # Still write cache so later runs see a flat next to output.
        pred_path = result_dir / "output.flat.json"
        if not (use_cache and _cache_fresh(pred_path, output_docx) and _schema_fresh(result_dir)):
            _save_json(pred_path, pred)
            _write_schema(result_dir)
    elif same_document(output_docx, source):
        _log("output ≡ input.docx → reuse source.flat")
        pred = dict(source_flat)
        pred_path = result_dir / "output.flat.json"
        if not (use_cache and _cache_fresh(pred_path, output_docx) and _schema_fresh(result_dir)):
            _save_json(pred_path, pred)
            _write_schema(result_dir)
    else:
        pred = ensure_output_flat(output_docx, use_cache=use_cache)

    integrity = ensure_integrity(
        source, output_docx, sample_dir=sample_dir, use_cache=use_cache
    )
    if not quiet:
        n_diff = len(integrity.differences)
        if integrity.status == "ok":
            print("  [INTEGRITY] ok", flush=True)
        else:
            print(
                f"  [INTEGRITY] {integrity.status} (warning, still scoring)  diffs={n_diff}",
                flush=True,
            )

    _log("score JSON flats")
    report = metrics_from_flats(
        pred=pred,
        gold=golden_flat,
        init_flat=source_flat,
        sample_dir=sample_dir,
        integrity_status=integrity.status,
        tol=tol,
    )
    report.metrics["output_docx"] = str(output_docx)
    if integrity.status != "ok":
        report.metrics["integrity_warning"] = integrity.status
        report.metrics["integrity_diff_count"] = len(integrity.differences)
    save_metrics_report(report, result_dir / "accuracy.json")
    if not quiet:
        print(
            f"  [SCORE] accuracy_by_attribution={report.metrics.get('accuracy_by_attribution')} "
            f"accuracy_by_mode_avg={report.metrics.get('accuracy_by_mode_avg')} "
            f"hallucination_by_attribution={report.metrics.get('hallucination_by_attribution')}",
            flush=True,
        )
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate one formatted sample.")
    parser.add_argument("sample_dir", type=Path, help="Directory with golden.docx / source.docx")
    parser.add_argument("output_docx", type=Path, help="Model output.docx")
    args = parser.parse_args()
    evaluate_sample(args.sample_dir, args.output_docx)


if __name__ == "__main__":
    main()
