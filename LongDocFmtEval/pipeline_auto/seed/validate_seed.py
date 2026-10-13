"""Validate a seed directory.

Checks:
  - outline_bind indices contiguous / unique object_id
  - every role_id has a RoleCard in role_registry (if present)
  - bindings.object_indices match outline
  - standard required fields present (human_mark etc.)

Usage:
  python LongDocFmtEval/pipeline_auto/seed/validate_seed.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/seed/examples/field_survey_mini
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_STANDARD = ("label", "display_name", "human_mark", "object_kind", "semantic")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(seed_dir: Path) -> list[str]:
    errors: list[str] = []
    outline_path = seed_dir / "outline_bind.json"
    registry_path = seed_dir / "role_registry.json"
    if not outline_path.exists():
        return [f"missing {outline_path}"]

    outline = _load(outline_path)
    objects = outline.get("objects") or []
    if not objects:
        errors.append("outline_bind.objects is empty")

    seen_idx: set[int] = set()
    seen_oid: set[str] = set()
    for i, obj in enumerate(objects):
        for key in ("index", "object_id", "kind", "role_id"):
            if key not in obj:
                errors.append(f"objects[{i}] missing {key}")
        idx = obj.get("index")
        oid = obj.get("object_id")
        if isinstance(idx, int):
            if idx in seen_idx:
                errors.append(f"duplicate index {idx}")
            seen_idx.add(idx)
        if isinstance(oid, str):
            if oid in seen_oid:
                errors.append(f"duplicate object_id {oid}")
            seen_oid.add(oid)

    if seen_idx and sorted(seen_idx) != list(range(min(seen_idx), max(seen_idx) + 1)):
        errors.append(f"indices not contiguous: {sorted(seen_idx)}")

    if not registry_path.exists():
        errors.append("role_registry.json missing (run build_role_registry.py)")
        return errors

    reg = _load(registry_path)
    roles = reg.get("roles") or {}
    index_map = reg.get("index") or {}

    for obj in objects:
        idx = obj.get("index")
        rid = obj.get("role_id")
        if rid not in roles:
            errors.append(f"role {rid!r} used by index={idx} but missing in role_registry")
            continue
        card = roles[rid]
        std = card.get("standard") or {}
        for f in REQUIRED_STANDARD:
            if not std.get(f):
                errors.append(f"role {rid}: standard.{f} empty")
        bindings = card.get("bindings") or {}
        indices = bindings.get("object_indices") or []
        if idx not in indices:
            errors.append(f"role {rid}: bindings.object_indices missing outline index {idx}")
        if str(idx) in index_map and index_map[str(idx)] != rid:
            errors.append(f"index map mismatch at {idx}: {index_map[str(idx)]} vs {rid}")

        for cell in obj.get("cells") or []:
            cr = cell.get("role_id")
            if cr not in roles:
                errors.append(f"cell role {cr!r} missing in registry (table index={idx})")
                continue
            refs = (roles[cr].get("bindings") or {}).get("cell_refs") or []
            if not any(r.get("table_object_index") == idx and r.get("pos") == cell.get("pos") for r in refs):
                errors.append(f"cell role {cr}: missing cell_ref for table index={idx} pos={cell.get('pos')}")

    # orphan registry roles with empty bindings are allowed (standards-only), but warn via stdout
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True)
    args = ap.parse_args()
    seed_dir = Path(args.seed_dir)
    errs = validate(seed_dir)
    if errs:
        print(f"FAIL {seed_dir} ({len(errs)} issues)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK {seed_dir}")


if __name__ == "__main__":
    main()
