"""Serialize inventory DocElements for the shared sample cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from LongDocFormatter.workflow.contracts import LAYER_ORDER, DocElement, Layer

INVENTORY_SCHEMA = 5


def dump_elements(
    by_layer: Dict[str, List[DocElement]],
    *,
    cache_dir: Path | None = None,
    omit_section_props: bool = False,
) -> Dict[str, Any]:
    root = Path(cache_dir).resolve() if cache_dir else None
    out: Dict[str, Any] = {}
    for layer, els in (by_layer or {}).items():
        rows = []
        for el in els:
            row = el.to_dict()
            if omit_section_props and str(layer) == "section":
                row.pop("props", None)
            img = row.get("image_path")
            if img and root:
                p = Path(img)
                try:
                    row["image_path"] = str(p.resolve().relative_to(root))
                except ValueError:
                    row["image_path"] = str(p)
            rows.append(row)
        out[layer] = rows
    return out


def load_elements(data: Dict[str, Any] | None, *, cache_dir: Path | None = None) -> Dict[Layer, List[DocElement]]:
    root = Path(cache_dir).resolve() if cache_dir else None
    out: Dict[Layer, List[DocElement]] = {}
    for layer, items in (data or {}).items():
        if str(layer).startswith("_") or str(layer) not in LAYER_ORDER:
            continue
        els: List[DocElement] = []
        for item in items or []:
            el = DocElement.from_dict(item)
            if el.image_path and root and not Path(el.image_path).is_absolute():
                el.image_path = str(root / el.image_path)
            els.append(el)
        out[layer] = els  # type: ignore[assignment]
    return out


def save_elements_json(
    path: Path,
    by_layer: Dict[str, List[DocElement]],
    *,
    cache_dir: Path | None = None,
    omit_section_props: bool = False,
    profile: str = "full",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dump_elements(
        by_layer,
        cache_dir=cache_dir,
        omit_section_props=omit_section_props,
    )
    payload["_schema"] = INVENTORY_SCHEMA
    payload["_profile"] = str(profile or "full")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory_profile_of(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("_profile") or "").strip().lower()


def inventory_schema_of(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("_schema") or 0)
    except (TypeError, ValueError):
        return 0


def load_elements_json(path: Path, *, cache_dir: Path | None = None) -> Dict[Layer, List[DocElement]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return load_elements(data if isinstance(data, dict) else {}, cache_dir=cache_dir)
