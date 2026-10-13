"""Build role_registry.json from outline_bind.json (+ optional role_standards.json).

For each role:
  - standard: human-facing identification card (label / human_mark / semantic / ...)
  - bindings.object_indices / object_ids / paths: where this role appears
  - bindings.cell_refs: for cell roles inside tables
  - style: optional props stub from theme_tokens + theme_slot

Usage (repo root):
  python LongDocFmtEval/pipeline_auto/seed/build_role_registry.py \\
    --outline LongDocFmtEval/pipeline_auto/seed/examples/field_survey_mini/outline_bind.json \\
    --standards LongDocFmtEval/pipeline_auto/seed/examples/field_survey_mini/role_standards.json \\
    --out LongDocFmtEval/pipeline_auto/seed/examples/field_survey_mini/role_registry.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_THEME_TOKENS = {
    "faces": {
        "title": "方正小标宋",
        "body": "宋体",
        "quote": "楷体_GB2312",
        "latin": "Times New Roman",
    },
    "ladder_pt": {"title": 22, "h1": 16, "h2": 14, "h3": 12, "body": 12},
    "accent": "#1F4E79",
}

_KIND_TO_OBJECT_KIND = {
    "section_break": "section",
    "paragraph": "paragraph",
    "table": "table",
    "image": "image",
    "cell": "cell",
    "run_anchor": "run",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fallback_standard(role_id: str, object_kind: str) -> dict:
    short = role_id.replace("Para", "").replace("Sec", "SEC-").replace("Tbl", "TBL-").replace("Img", "IMG-")
    label = short[:12] if short else role_id
    return {
        "label": label,
        "display_name": role_id,
        "human_mark": f"【{label}·{role_id}】",
        "object_kind": object_kind,
        "semantic": f"（待补）角色 {role_id} 的语义说明。",
        "format_intent": "（待补）版式标准。",
        "preview_example": role_id,
        "theme_slot": "body" if object_kind == "paragraph" else None,
    }


def _props_for_slot(theme_tokens: dict, slot: str | None, object_kind: str) -> dict:
    if object_kind == "section":
        return {
            "type": "nextPage",
            "marginTop": "2.5cm",
            "marginBottom": "2.5cm",
            "marginLeft": "3cm",
            "marginRight": "2.5cm",
        }
    if object_kind in ("table", "image", "cell"):
        return {}
    faces = theme_tokens.get("faces") or {}
    ladder = theme_tokens.get("ladder_pt") or {}
    slot = slot or "body"
    face_key = "title" if slot == "title" else ("body" if slot.startswith("h") is False and slot != "quote" else slot)
    if slot in ("h1", "h2", "h3", "title"):
        ea = faces.get("title") or faces.get("body") or "宋体"
    elif slot == "quote":
        ea = faces.get("quote") or "楷体_GB2312"
    else:
        ea = faces.get("body") or "宋体"
    size = ladder.get(slot) or ladder.get("body") or 12
    props = {
        "font.ea": ea,
        "font.latin": faces.get("latin") or "Times New Roman",
        "size": f"{size}pt",
    }
    if slot == "h1":
        props["outlineLvl"] = 0
        props["bold"] = True
        props["spaceBefore"] = "18pt"
        props["spaceAfter"] = "6pt"
    elif slot == "h2":
        props["outlineLvl"] = 1
        props["bold"] = True
        props["spaceBefore"] = "12pt"
        props["spaceAfter"] = "6pt"
    elif slot == "title":
        props["align"] = "center"
        props["bold"] = True
        props["spaceBefore"] = "24pt"
        props["spaceAfter"] = "12pt"
    else:
        props["spaceBefore"] = "6pt"
        props["spaceAfter"] = "6pt"
        if object_kind == "paragraph" and slot == "body":
            props["firstLineIndent"] = "24pt"
    return props


def build_registry(
    outline: dict,
    standards: dict | None = None,
    theme_tokens: dict | None = None,
    source_outline: str = "",
) -> dict:
    standards = standards or {}
    theme_tokens = theme_tokens or dict(DEFAULT_THEME_TOKENS)
    meta = outline.get("meta") or {}
    objects = outline.get("objects") or []

    buckets: dict[str, dict] = {}
    index_map: dict[str, str] = {}

    def ensure_role(role_id: str, object_kind: str) -> dict:
        if role_id not in buckets:
            std_src = standards.get(role_id) or {}
            base = _fallback_standard(role_id, object_kind)
            base.update({k: v for k, v in std_src.items() if v is not None})
            base["object_kind"] = base.get("object_kind") or object_kind
            if "human_mark" not in base or not base["human_mark"]:
                base["human_mark"] = f"【{base.get('label', role_id)}·{base.get('display_name', role_id)}】"
            slot = base.get("theme_slot")
            buckets[role_id] = {
                "role_id": role_id,
                "standard": {
                    "label": base["label"],
                    "display_name": base["display_name"],
                    "human_mark": base["human_mark"],
                    "object_kind": base["object_kind"],
                    "semantic": base.get("semantic") or "",
                    "format_intent": base.get("format_intent") or "",
                    "preview_example": base.get("preview_example") or "",
                },
                "bindings": {
                    "object_indices": [],
                    "object_ids": [],
                    "paths": [],
                    "cell_refs": [],
                },
                "style": {
                    "theme_slot": slot,
                    "props": _props_for_slot(theme_tokens, slot, base["object_kind"]),
                },
            }
        return buckets[role_id]

    for obj in objects:
        idx = int(obj["index"])
        oid = obj["object_id"]
        kind = obj["kind"]
        role_id = obj["role_id"]
        path = obj.get("path") or f"objects[{idx}]"
        object_kind = _KIND_TO_OBJECT_KIND.get(kind, "paragraph")
        entry = ensure_role(role_id, object_kind)
        entry["bindings"]["object_indices"].append(idx)
        entry["bindings"]["object_ids"].append(oid)
        entry["bindings"]["paths"].append(path)
        index_map[str(idx)] = role_id

        for cell in obj.get("cells") or []:
            cell_role = cell["role_id"]
            pos = cell["pos"]
            cell_entry = ensure_role(cell_role, "cell")
            cell_entry["bindings"]["cell_refs"].append(
                {
                    "table_object_index": idx,
                    "table_object_id": oid,
                    "pos": pos,
                }
            )

    # stable sort indices
    for entry in buckets.values():
        b = entry["bindings"]
        order = sorted(range(len(b["object_indices"])), key=lambda i: b["object_indices"][i])
        b["object_indices"] = [b["object_indices"][i] for i in order]
        b["object_ids"] = [b["object_ids"][i] for i in order]
        b["paths"] = [b["paths"][i] for i in order]

    return {
        "meta": {
            "seed_id": meta.get("seed_id") or "unknown",
            "source_outline": source_outline,
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "theme_tokens": theme_tokens,
        "roles": dict(sorted(buckets.items())),
        "index": dict(sorted(index_map.items(), key=lambda kv: int(kv[0]))),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build role_registry from outline_bind")
    ap.add_argument("--outline", required=True, help="outline_bind.json")
    ap.add_argument("--standards", default="", help="optional role_standards.json")
    ap.add_argument("--theme-tokens", default="", help="optional theme_tokens.json")
    ap.add_argument("--out", required=True, help="role_registry.json output")
    args = ap.parse_args()

    outline_path = Path(args.outline)
    outline = _load_json(outline_path)
    standards = _load_json(Path(args.standards)) if args.standards else {}
    theme_tokens = _load_json(Path(args.theme_tokens)) if args.theme_tokens else None
    reg = build_registry(
        outline,
        standards=standards,
        theme_tokens=theme_tokens,
        source_outline=str(outline_path).replace("\\", "/"),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(reg['roles'])} roles, {len(reg['index'])} objects)")


if __name__ == "__main__":
    main()
