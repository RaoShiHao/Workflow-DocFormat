"""Merge one layer mapping into target_loc.json. Prefer JSON layer-loc fragments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.inventory import merge_target_loc  # noqa: E402

_BY_LAYER = frozenset({"section", "paragraph.body", "table", "image"})
_TABLE_CELLS = "table_cells"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _read_text(path_or_dash: str) -> str:
    raw = str(path_or_dash).strip()
    if raw in {"", "-"}:
        return sys.stdin.read()
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def _split_fields(line: str) -> list[str]:
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    if "\t" in line:
        return [p.strip() for p in line.split("\t")]
    if "," in line:
        return [p.strip() for p in line.split(",")]
    return line.split()


def payload_from_tsv(layer: str, text: str) -> dict:
    """Build a one-layer merge payload from TSV/CSV/whitespace lines (legacy)."""
    layer = str(layer).strip()
    if layer == _TABLE_CELLS:
        cells: dict[str, list[dict]] = {}
        for line in text.splitlines():
            parts = _split_fields(line)
            if len(parts) < 4:
                continue
            tid, row_s, col_s, style = parts[0], parts[1], parts[2], parts[3]
            try:
                row, col = int(row_s), int(col_s)
            except ValueError:
                continue
            if not tid or not style:
                continue
            cells.setdefault(str(tid), []).append(
                {"row": row, "col": col, "cell_style": str(style)}
            )
        return {"by_layer": {}, "table_cells": cells}
    if layer not in _BY_LAYER:
        raise SystemExit(f"unsupported --layer {layer!r}; use one of {sorted(_BY_LAYER | {_TABLE_CELLS})}")
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        parts = _split_fields(line)
        if len(parts) < 2:
            continue
        loc_id, style_id = parts[0], parts[1]
        if not loc_id or not style_id:
            continue
        mapping[str(loc_id)] = str(style_id)
    return {"by_layer": {layer: mapping}, "table_cells": {}}


def payload_from_json_map(layer: str, text: str) -> dict:
    """JSON object map: {\"1\":\"ParaCluster0\",...} for a by_layer layer."""
    layer = str(layer).strip()
    if layer == _TABLE_CELLS:
        raise SystemExit("--json-map is for by_layer only; use --layer-loc for table_cells")
    if layer not in _BY_LAYER:
        raise SystemExit(f"unsupported --layer {layer!r}")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("--json-map must be a JSON object")
    mapping = {str(k): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}
    return {"by_layer": {layer: mapping}, "table_cells": {}}


def payload_from_layer_loc(text: str) -> dict:
    """One-layer (or partial) target_loc JSON fragment."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("--layer-loc must be a JSON object")
    return data


def main() -> int:
    p = argparse.ArgumentParser(
        description="Merge one layer into target_loc.json. Prefer --layer-loc (JSON IR fragment)."
    )
    p.add_argument("--loc", required=True, help="target_loc.json (created if missing)")
    p.add_argument(
        "--layer",
        default="",
        help="section | paragraph.body | table | image | table_cells (required with --json-map / --tsv)",
    )
    p.add_argument(
        "--layer-loc",
        dest="layer_loc",
        default="",
        help="JSON fragment with by_layer and/or table_cells (same schema as target_loc). File path or - for stdin.",
    )
    p.add_argument(
        "--json-map",
        default="",
        help="JSON object {id: style_id} for one by_layer. Use - for stdin.",
    )
    p.add_argument(
        "--tsv",
        default="",
        help="Legacy TSV lines. Prefer --layer-loc. Use - for stdin.",
    )
    p.add_argument(
        "--slice",
        dest="legacy_slice",
        default="",
        help=argparse.SUPPRESS,
    )
    args = p.parse_args()
    loc_path = Path(args.loc).expanduser().resolve()
    layer = str(args.layer or "").strip()
    tsv = str(args.tsv or "").strip()
    jmap = str(args.json_map or "").strip()
    legacy = str(args.layer_loc or args.legacy_slice).strip()

    try:
        if legacy:
            payload = payload_from_layer_loc(_read_text(legacy))
        elif jmap:
            if not layer:
                print("need --layer with --json-map", file=sys.stderr)
                return 2
            payload = payload_from_json_map(layer, _read_text(jmap))
        elif tsv:
            if not layer:
                print("need --layer with --tsv", file=sys.stderr)
                return 2
            payload = payload_from_tsv(layer, _read_text(tsv))
        else:
            print("need --layer-loc, or --json-map / --tsv with --layer", file=sys.stderr)
            return 2
    except FileNotFoundError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"bad json: {exc}", file=sys.stderr)
        return 2

    merged = merge_target_loc(_load(loc_path), payload)
    loc_path.parent.mkdir(parents=True, exist_ok=True)
    loc_path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    layers = {k: len(v or {}) for k, v in (merged.get("by_layer") or {}).items() if isinstance(v, dict)}
    print(
        json.dumps(
            {
                "ok": True,
                "loc": str(loc_path),
                "layers": layers,
                "n_table_cells": len(merged.get("table_cells") or {}),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
