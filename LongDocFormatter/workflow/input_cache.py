"""Lazy shared cache: fill missing inventory / image pieces in place."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from LongDocFormatter.workflow.element_io import (
    INVENTORY_SCHEMA,
    inventory_schema_of,
    load_elements_json,
    save_elements_json,
)
from LongDocFormatter.workflow.image_extract import extract_embedded_images
from LongDocFormatter.workflow.officecli_doc import (
    close_docx,
    fill_missing_outline_levels,
    inventory,
    open_docx,
)
from LongDocFormatter.workflow.officecli_lock import officecli_exclusive


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relativize_manifest(manifest: list[dict], cache_dir: Path) -> list[dict]:
    root = cache_dir.resolve()
    out: list[dict] = []
    for row in manifest:
        item = dict(row)
        f = item.get("file")
        if f:
            p = Path(str(f))
            try:
                item["file"] = str(p.resolve().relative_to(root))
            except ValueError:
                item["file"] = str(p)
        out.append(item)
    return out


def absolutize_manifest(manifest: list[dict], cache_dir: Path) -> list[dict]:
    out: list[dict] = []
    for row in manifest:
        item = dict(row)
        f = item.get("file")
        if f and not Path(str(f)).is_absolute():
            item["file"] = str(cache_dir / f)
        out.append(item)
    return out


def _manifest_files_ok(rows: list[dict]) -> bool:
    for row in rows:
        f = row.get("file")
        if f and not Path(str(f)).is_file():
            return False
    return True


def load_image_manifest(cache_dir: Path, filename: str) -> list[dict] | None:
    path = cache_dir / filename
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return None
    rows = absolutize_manifest(data, cache_dir)
    if not _manifest_files_ok(rows):
        return None
    return rows


def ensure_embedded_images(
    doc: Path,
    cache_dir: Path,
    *,
    stem: str,
    max_edge: int = 1024,
) -> list[dict]:
    """Load ``{stem}_image_manifest.json`` or extract into ``{stem}_images/``."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{stem}_image_manifest.json"
    cached = load_image_manifest(cache_dir, filename)
    if cached is not None:
        return cached
    rows = extract_embedded_images(Path(doc), cache_dir / f"{stem}_images", max_edge=max_edge)
    _save_json(cache_dir / filename, relativize_manifest(rows, cache_dir))
    return rows


def _live_inventory(doc: Path, *, profile: str = "full"):
    with officecli_exclusive():
        open_docx(doc)
        try:
            return inventory(doc, profile=profile)
        finally:
            close_docx(doc)


def inventory_profile_for_stem(stem: str) -> str:
    """source/init → assign (T/A/M); template → full (cluster + eval)."""
    return "assign" if str(stem) == "source" else "full"


def inventory_live(doc: Path, *, stem: str):
    """Uncached inventory with the same profile as ``ensure_inventory(..., stem=)``."""
    return _live_inventory(Path(doc), profile=inventory_profile_for_stem(stem))


def _section_previews_collapsed(elements) -> bool:
    secs = elements.get("section") or []
    texts = [str(getattr(el, "content", "") or "").strip() for el in secs]
    texts = [t for t in texts if t]
    return len(texts) >= 3 and len(set(texts)) == 1


def _section_previews_unusable(elements) -> bool:
    """True when assignment would see empty or identical section previews."""
    secs = elements.get("section") or []
    if not secs:
        return False
    texts = [str(getattr(el, "content", "") or "").strip() for el in secs]
    nonempty = [t for t in texts if t]
    if not nonempty:
        return True
    return _section_previews_collapsed(elements)


def ensure_inventory(doc: Path, cache_dir: Path, *, stem: str, profile: str | None = None):
    """Load ``inventory_{stem}.json`` or inventory the docx and write the cache.

    ``stem=="source"`` uses the assign profile (init T/A/M: no section props / HF /
    cell-para layer; section leading-text preview and nonempty table/image
    neighbors are still filled). Template clustering
    uses ``profile="full"``. Scoring snapshots use ``profile="eval"`` in
    ``eval_acc.snapshot_document`` (not this cache).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"inventory_{stem}.json"
    mode = profile or inventory_profile_for_stem(stem)
    if path.is_file() and inventory_schema_of(path) >= INVENTORY_SCHEMA:
        elements = load_elements_json(path, cache_dir=cache_dir)
        if not _section_previews_unusable(elements):
            if fill_missing_outline_levels(Path(doc), elements):
                save_elements_json(path, elements, cache_dir=cache_dir, profile=mode)
            return elements
    elements = _live_inventory(Path(doc), profile=mode)
    save_elements_json(path, elements, cache_dir=cache_dir, profile=mode)
    return elements
