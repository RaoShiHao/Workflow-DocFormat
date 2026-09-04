"""Build a role stylesheet from template.docx (cluster + exemplar props)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.cluster import build_stylesheet, roles_view  # noqa: E402
from lib.inventory import load_inventory, read_docx  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Cluster template formatting into a role stylesheet.")
    p.add_argument("--template", required=True)
    p.add_argument("--out", required=True)
    p.add_argument(
        "--roles-out",
        default="",
        help="Compact roles JSON for cat. Default: <out-stem>.roles.json next to --out",
    )
    p.add_argument(
        "--inventory",
        default="",
        help="Reuse a **template** parse_docx.py --out that was built with --profile full",
    )
    args = p.parse_args()
    template = Path(args.template).expanduser().resolve()
    inv_arg = str(args.inventory).strip()
    if inv_arg:
        inv_path = Path(inv_arg).expanduser().resolve()
        if not inv_path.is_file():
            print(f"not found: {inv_path}", file=sys.stderr)
            return 2
        by_layer = load_inventory(json.loads(inv_path.read_text(encoding="utf-8")))
    else:
        if not template.is_file():
            print(f"not found: {template}", file=sys.stderr)
            return 2
        by_layer = read_docx(template, profile="full")
    payload = build_stylesheet(by_layer)
    payload["template"] = str(template)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    roles_path = (
        Path(args.roles_out).expanduser().resolve()
        if str(args.roles_out).strip()
        else out.with_name(out.stem + ".roles.json")
    )
    roles_path.parent.mkdir(parents=True, exist_ok=True)
    roles_path.write_text(json.dumps(roles_view(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_l: dict[str, int] = {}
    for r in payload["roles"]:
        by_l[str(r["object"])] = by_l.get(str(r["object"]), 0) + 1
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "roles": str(roles_path),
                "n_roles": len(payload["roles"]),
                "by_layer": by_l,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
