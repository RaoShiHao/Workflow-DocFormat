"""Rebuild a compact id list from preview.json (no re-parse, no python -c).

Use only when cat of preview.ids.json was truncated. Writes a file; do not
print the JSON through a pager.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.inventory import compact_preview, load_inventory, slice_preview  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Write a compact mapping id list (truncation recovery).")
    p.add_argument("--preview", default="", help="Existing preview.json from parse_docx.py")
    p.add_argument("--inventory", default="", help="Existing inventory.json (alternative to --preview)")
    p.add_argument("--out", required=True, help="Output compact JSON path")
    p.add_argument("--layer", default="", help="Optional single layer, e.g. paragraph.body")
    p.add_argument("--from-id", default="", help="Inclusive start location_id")
    p.add_argument("--to-id", default="", help="Inclusive end location_id")
    args = p.parse_args()
    preview_arg = str(args.preview).strip()
    inv_arg = str(args.inventory).strip()
    if not preview_arg and not inv_arg:
        print("need --preview or --inventory", file=sys.stderr)
        return 2
    layer = str(args.layer).strip() or None
    from_id = str(args.from_id).strip() or None
    to_id = str(args.to_id).strip() or None
    if preview_arg:
        src = Path(preview_arg).expanduser().resolve()
        if not src.is_file():
            print(f"not found: {src}", file=sys.stderr)
            return 2
        data = json.loads(src.read_text(encoding="utf-8"))
        payload = slice_preview(data, from_id=from_id, to_id=to_id, layer_filter=layer)
    else:
        src = Path(inv_arg).expanduser().resolve()
        if not src.is_file():
            print(f"not found: {src}", file=sys.stderr)
            return 2
        data = json.loads(src.read_text(encoding="utf-8"))
        payload = compact_preview(
            load_inventory(data),
            from_id=from_id,
            to_id=to_id,
            layer_filter=layer,
        )
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    print(json.dumps({"ok": True, "out": str(out), "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
