"""Parse .docx via the same inventory engine as workflow cache / uncached graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.inventory import compact_preview, dump_inventory, preview, read_docx  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse .docx via the skill-local officecli inventory engine."
    )
    p.add_argument("--docx", required=True)
    p.add_argument("--out", required=True, help="Full inventory JSON for apply_format.py")
    p.add_argument("--preview-out", default="", help="Slim JSON (optional; prefer the auto-written ids file)")
    p.add_argument(
        "--ids-out",
        default="",
        help="Compact id list for cat. Default: next to --preview-out as preview.ids.json",
    )
    p.add_argument(
        "--profile",
        choices=("assign", "full"),
        default="assign",
        help="assign = source/init (T/A/M); full = template clustering / eval",
    )
    p.add_argument(
        "--include-runs",
        action="store_true",
        help="Deprecated: delta runs are always inventoried (same as workflow).",
    )
    args = p.parse_args()
    doc = Path(args.docx).expanduser().resolve()
    if not doc.is_file():
        print(f"not found: {doc}", file=sys.stderr)
        return 2
    by_layer = read_docx(doc, profile=str(args.profile))
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = dump_inventory(by_layer)
    payload["_profile"] = str(args.profile)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {k: len(v) for k, v in by_layer.items() if not str(k).startswith("_")}
    print(
        json.dumps(
            {"ok": True, "docx": str(doc), "out": str(out), "profile": args.profile, "layers": counts},
            ensure_ascii=False,
        )
    )
    ids_path: Path | None = None
    if str(args.ids_out).strip():
        ids_path = Path(args.ids_out).expanduser().resolve()
    elif str(args.preview_out).strip():
        prev = Path(args.preview_out).expanduser().resolve()
        ids_path = prev.with_name(prev.stem + ".ids.json")
    else:
        ids_path = out.with_name("preview.ids.json")
    ids_path.parent.mkdir(parents=True, exist_ok=True)
    ids_path.write_text(json.dumps(compact_preview(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ids": str(ids_path)}, ensure_ascii=False))
    if str(args.preview_out).strip():
        prev = Path(args.preview_out).expanduser().resolve()
        prev.parent.mkdir(parents=True, exist_ok=True)
        prev.write_text(json.dumps(preview(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"preview": str(prev)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
