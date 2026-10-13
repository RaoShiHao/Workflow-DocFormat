"""Merge extracted ``style_tree.json`` props back into ``role_params.json``.

After human edit + ``extract_style_tree``, ``style_tree.json`` is the visual source of
truth. Downstream render/gold reads format from ``role_params.json`` only.
``template.json`` keeps names/examples (no props copy).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json  # noqa: E402

_MERGE_KEYS = ("props", "header", "footer", "header_first", "cells", "table_format")


def _merge_entry(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in _MERGE_KEYS:
        if key not in src:
            continue
        val = src[key]
        if val is None:
            continue
        if key == "props" and isinstance(val, dict):
            dst["props"] = dict(val)
        elif key in ("header", "footer", "header_first") and isinstance(val, dict):
            dst[key] = dict(val)
        elif key == "cells":
            dst[key] = val
        elif key == "table_format" and isinstance(val, dict):
            dst[key] = dict(val)
    if src.get("object"):
        dst.setdefault("object", src["object"])


def sync_asset_sizes_from_roles(dir_path: Path, *, assets_name: str = "assets.json") -> int:
    """Align assets*.json width/height with Img* role props. Returns change count."""
    assets_path = dir_path / assets_name
    if not assets_path.is_file():
        return 0
    try:
        from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415
        assets = load_json(assets_path)
        roles = load_merged_roles(dir_path)
    except Exception:  # noqa: BLE001
        return 0
    changed = 0
    for _fid, info in (assets or {}).items():
        if not isinstance(info, dict):
            continue
        rid = str(info.get("role_id") or "")
        props = ((roles.get(rid) or {}).get("props") or {}) if rid else {}
        for k in ("width", "height"):
            if props.get(k) not in (None, "", "none") and info.get(k) != props.get(k):
                info[k] = props[k]
                changed += 1
        if props.get("hAlign") and info.get("align") != props.get("hAlign"):
            info["align"] = props["hAlign"]
            changed += 1
    if changed:
        dump_json(assets_path, assets)
    return changed


def apply_style_tree_to_tstar(template_dir: Path) -> dict[str, int]:
    """Write format keys into role_params.json; keep template.json as a name catalog."""
    template_dir = template_dir.resolve()
    from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import ROLE_FORMAT_KEYS  # noqa: PLC0415

    tree_path = resolve_work_file(template_dir, "style_tree.json")
    if not tree_path.is_file():
        tree_path = template_dir / "style_tree.json"
    rp_path = resolve_work_file(template_dir, "role_params.json")
    if not rp_path.is_file():
        rp_path = template_dir / "role_params.json"
    tpl_path = resolve_work_file(template_dir, "template.json")
    if not tree_path.exists():
        raise FileNotFoundError(tree_path)
    if not rp_path.exists():
        raise FileNotFoundError(rp_path)

    tree = load_json(tree_path)
    roles_src = tree.get("roles") or {}
    role_params = load_json(rp_path)
    updated_rp = 0
    for rid, entry in roles_src.items():
        if not isinstance(entry, dict):
            continue
        if rid not in role_params or not isinstance(role_params[rid], dict):
            role_params[rid] = {
                "object": entry.get("object") or "paragraph",
                "props": {},
                "example": entry.get("example") or "",
                "display_name": entry.get("display_name") or rid,
            }
        _merge_entry(role_params[rid], entry)
        updated_rp += 1
    dump_json(rp_path, role_params)

    updated_tpl = 0
    if tpl_path.is_file():
        template = load_json(tpl_path)
        tpl_roles = template.setdefault("roles", {})
        if isinstance(tpl_roles, dict):
            for rid, entry in roles_src.items():
                if not isinstance(entry, dict):
                    continue
                slot = tpl_roles.setdefault(rid, {})
                if not isinstance(slot, dict):
                    continue
                for k in ROLE_FORMAT_KEYS:
                    slot.pop(k, None)
                if entry.get("example") and not slot.get("example"):
                    slot["example"] = entry["example"]
                if entry.get("display_name"):
                    slot["display_name"] = slot.get("display_name") or entry["display_name"]
                if entry.get("object"):
                    slot.setdefault("object", entry["object"])
                updated_tpl += 1
        dump_json(tpl_path, template)

    print(
        f"[apply] style_tree → role_params ({updated_rp} roles)"
        + (f", template.json 目录 {updated_tpl} roles（不含 props）" if updated_tpl else "")
    )
    return {"role_params": updated_rp, "template": updated_tpl}


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge style_tree.json into role_params.json")
    ap.add_argument("--template-dir", required=True, help="step3_templates/<slug> or seed dir")
    args = ap.parse_args()
    apply_style_tree_to_tstar(Path(args.template_dir))


if __name__ == "__main__":
    main()
