"""Role-library visualizer.

Loads a role library (YAML/JSON) in ANY of three tree encodings and renders it:
  1) nested objects   : node["derives"] = [child node dicts]
  2) flat + child ids : node["derives"] = ["ChildId", ...]
  3) flat + parent    : node["parent"]  = "ParentId" | null

Outputs:
  - ASCII tree per layer to the console
  - a Mermaid diagram (.md) that renders the tree in chat / Markdown / IDE

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/viz_role_library.py <path-to-library.yaml|json> [out.md]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.role_library import TREE_LAYER_PATHS, build_forest, rid as _rid


def _getter(path):
    def g(roles):
        cur = roles
        for p in path[:-1]:
            cur = cur.get(p) or {}
        return cur.get(path[-1])
    return g


TREE_LAYERS = [(name, _getter(path)) for name, path in TREE_LAYER_PATHS]


def ascii_tree(roots: list) -> list[str]:
    lines: list[str] = []

    def walk(node, prefix, is_last):
        conn = "└─ " if is_last else "├─ "
        tag = " (abstract)" if node.get("abstract") else ""
        focus = node.get("format_focus")
        focus_s = f"  focus={focus}" if focus else ""
        lines.append(f"{prefix}{conn}{_rid(node)}{tag}{focus_s}")
        kids = node.get("children", [])
        child_prefix = prefix + ("   " if is_last else "│  ")
        for i, k in enumerate(kids):
            walk(k, child_prefix, i == len(kids) - 1)

    for i, r in enumerate(roots):
        walk(r, "", i == len(roots) - 1)
    return lines


def mermaid(layer_name: str, roots: list) -> str:
    safe = layer_name.replace(".", "_")
    # LR (left->right): levels fan out as columns, leaves stack top-to-bottom
    # on the right instead of crowding into a single width.
    out = [f"```mermaid", f"graph LR"]
    counter = {"i": 0}
    ids: dict[int, str] = {}

    def node_id(node):
        key = id(node)
        if key not in ids:
            ids[key] = f"{safe}_{counter['i']}"
            counter["i"] += 1
        return ids[key]

    def walk(node):
        nid = node_id(node)
        label = _rid(node) + ("<br/><i>abstract</i>" if node.get("abstract") else "")
        shape_l, shape_r = ("([", "])") if node.get("abstract") else ("[", "]")
        out.append(f'  {nid}{shape_l}"{label}"{shape_r}')
        for k in node.get("children", []):
            out.append(f"  {nid} --> {node_id(k)}")
            walk(k)

    for r in roots:
        walk(r)
    out.append("```")
    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        print("usage: python viz_role_library.py <library.yaml|json> [out.md]")
        sys.exit(2)
    path = Path(sys.argv[1])
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) if path.suffix in (".yaml", ".yml") else json.loads(raw)
    roles = data.get("roles", data)

    quiet = len(sys.argv) >= 3  # writing md for pipeline — skip ASCII dump to console
    md_parts = [f"# Role tree: {data.get('display_name', path.stem)}", ""]
    for name, getter in TREE_LAYERS:
        nodes = getter(roles)
        if not nodes:
            continue
        roots = build_forest(nodes)
        if not quiet:
            print(f"\n===== {name} =====")
            for ln in ascii_tree(roots):
                print(ln)
        md_parts.append(f"## {name}")
        md_parts.append(mermaid(name, roots))
        md_parts.append("")

    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else path.with_suffix(".tree.md")
    out_path.write_text("\n".join(md_parts) + "\n", encoding="utf-8")
    if quiet:
        print(f"[browse] {out_path.name}")
    else:
        print(f"\n[mermaid] {out_path}")


if __name__ == "__main__":
    main()
