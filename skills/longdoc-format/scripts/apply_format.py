"""Apply T + A + M onto source.docx using the shared apply engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.apply import DEFAULT_CHUNK, apply_ir, validate  # noqa: E402
from lib.inventory import load_inventory  # noqa: E402


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected object in {path}")
    return data


def _catalog(data: dict) -> list[dict]:
    if isinstance(data.get("entries"), list):
        return [e for e in data["entries"] if isinstance(e, dict)]
    skel = data.get("target_set_skeleton")
    if isinstance(skel, dict) and isinstance(skel.get("entries"), list):
        return [e for e in skel["entries"] if isinstance(e, dict)]
    raise SystemExit("target-set JSON must have entries (or target_set_skeleton.entries)")


def _props(data: dict) -> dict:
    inner = data.get("target_props")
    if isinstance(inner, dict) and inner and all(isinstance(v, dict) for v in inner.values()):
        return inner
    if "entries" not in data and "roles" not in data:
        return data
    if isinstance(inner, dict):
        return inner
    raise SystemExit("target-props JSON must be {style_id: spec} or stylesheet.target_props")


def _loc(data: dict) -> dict:
    if "by_layer" in data:
        return data
    raise SystemExit("target-loc JSON must have by_layer")


def main() -> int:
    p = argparse.ArgumentParser(description="Write formatting from T/A/M JSON onto source.docx")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--target-set", required=True)
    p.add_argument("--target-props", required=True)
    p.add_argument("--target-loc", required=True)
    p.add_argument("--inventory", required=True, help="Full JSON from parse_docx.py --out")
    p.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK,
        help="officecli batch size (default 150). Env LONGDOC_APPLY_BATCH_CHUNK overrides.",
    )
    p.add_argument("--dump-ops", default="", help="Write compiled apply_ops.json (debug)")
    p.add_argument("--budget-seconds", type=float, default=0, help=argparse.SUPPRESS)
    args = p.parse_args()
    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.is_file():
        print(f"not found: {source}", file=sys.stderr)
        return 2
    entries = _catalog(_load(Path(args.target_set).expanduser().resolve()))
    props = _props(_load(Path(args.target_props).expanduser().resolve()))
    loc = _loc(_load(Path(args.target_loc).expanduser().resolve()))
    inventory = load_inventory(_load(Path(args.inventory).expanduser().resolve()))
    errors = validate(entries, props, loc, inventory)
    if errors:
        print(json.dumps({"ok": False, "errors": errors[:40], "n_errors": len(errors)}, ensure_ascii=False, indent=2))
        return 1
    dump = Path(args.dump_ops).expanduser().resolve() if str(args.dump_ops).strip() else None
    stats = apply_ir(
        source=source,
        output=output,
        catalog_entries=entries,
        props=props,
        loc=loc,
        inventory=inventory,
        chunk_size=args.chunk_size,
        dump_ops=dump,
    )
    print(json.dumps({"ok": True, "output": str(output), **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
