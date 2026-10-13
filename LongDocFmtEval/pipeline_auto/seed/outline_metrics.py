"""Soft outline metrics for later distribution / filtering (no pass/fail gates)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index
from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level


def _is_body_hint(hint: str) -> bool:
    return hint in {"medium", "long"}


def _is_caption_like(rid: str, dn: str) -> bool:
    s = f"{rid} {dn}".lower()
    return "caption" in s or "题注" in dn or "表题" in dn or "图题" in dn


def _is_toc_or_cover_title(rid: str, dn: str, rec: dict) -> bool:
    if rec.get("title_kind"):
        return True
    s = f"{rid} {dn}".lower()
    return any(k in s for k in ("toc", "目录", "cover", "封面"))


def measure_outline(plan: dict[str, Any]) -> dict[str, Any]:
    """Compute structural density / branching / bridge stats from a plan.json."""
    name_by: dict[str, str] = {}
    obj_by: dict[str, str] = {}
    for item in plan.get("stylesheet") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if not sid:
            continue
        if item.get("display_name"):
            name_by[sid] = str(item["display_name"])
        if item.get("object"):
            obj_by[sid] = str(item["object"])

    idx = heading_index(plan)
    blocks: list[dict[str, Any]] = []
    for o in plan.get("outline") or []:
        if isinstance(o, dict):
            blocks.extend(b for b in (o.get("blocks") or []) if isinstance(b, dict))

    lh = Counter(str(b.get("length_hint") or "") for b in blocks)
    heading_counts: Counter[str] = Counter()
    seq: list[tuple[str, int | None, str]] = []

    for b in blocks:
        rid = str(b.get("role_id") or "")
        dn = name_by.get(rid) or rid
        rec = idx.get(rid) or {}
        hl = rec.get("heading_level")
        if hl is None and not rec.get("title_kind"):
            hl = heading_level(rid, dn)
        hint = str(b.get("length_hint") or "")
        obj = obj_by.get(rid) or ""
        if hl:
            heading_counts[f"H{int(hl)}"] += 1
            seq.append(("H", int(hl), rid))
        elif _is_body_hint(hint) and not _is_caption_like(rid, dn) and not _is_toc_or_cover_title(rid, dn, rec):
            seq.append(("B", None, rid))
        elif obj == "table" or rid.startswith("Tbl") or hint == "table":
            seq.append(("T", None, rid))
        elif obj == "image" or rid.startswith("Img") or hint == "image":
            seq.append(("I", None, rid))
        else:
            seq.append(("O", None, rid))

    # Leaf body counts: deepest headings with no deeper child before next same/shallower
    leaf_bodies: list[int] = []
    for i, item in enumerate(seq):
        if item[0] != "H" or item[1] is None:
            continue
        lvl = int(item[1])
        has_child = False
        n_body = 0
        for j in range(i + 1, len(seq)):
            if seq[j][0] == "H" and seq[j][1] is not None:
                if int(seq[j][1]) > lvl:
                    has_child = True
                    break
                break
            if seq[j][0] == "B":
                n_body += 1
        if not has_child:
            leaf_bodies.append(n_body)

    # Parent → first child: bridge body between them?
    parents_with_children = 0
    with_bridge = 0
    without_bridge = 0
    single_child_parents = 0
    multi_child_parents = 0
    sibling_counts: list[int] = []

    for i, item in enumerate(seq):
        if item[0] != "H" or item[1] is None:
            continue
        lvl = int(item[1])
        child_idxs: list[int] = []
        for j in range(i + 1, len(seq)):
            if seq[j][0] == "H" and seq[j][1] is not None:
                cj = int(seq[j][1])
                if cj <= lvl:
                    break
                if cj == lvl + 1:
                    child_idxs.append(j)
        if not child_idxs:
            continue
        parents_with_children += 1
        n_sib = len(child_idxs)
        sibling_counts.append(n_sib)
        if n_sib == 1:
            single_child_parents += 1
        else:
            multi_child_parents += 1
        first = child_idxs[0]
        if any(seq[t][0] == "B" for t in range(i + 1, first)):
            with_bridge += 1
        else:
            without_bridge += 1

    n_body = int(lh.get("medium", 0) + lh.get("long", 0))
    # Rough mid-band estimate for filtering later (not a gate): zh≈350/625, en≈1000/2000 chars
    locale = str((plan.get("meta") or {}).get("locale") or "zh").lower()
    if locale.startswith("en"):
        est = int(lh.get("medium", 0)) * 1000 + int(lh.get("long", 0)) * 2000
    else:
        est = int(lh.get("medium", 0)) * 350 + int(lh.get("long", 0)) * 625

    depth = 0
    for k, n in heading_counts.items():
        if n and k.startswith("H"):
            try:
                depth = max(depth, int(k[1:]))
            except ValueError:
                pass

    return {
        "ok": True,
        "fatal": False,
        "note": "soft record only — for distribution / filtering; not a pass/fail gate",
        "meta": {
            "genre": (plan.get("meta") or {}).get("genre"),
            "locale": (plan.get("meta") or {}).get("locale"),
            "theme": (plan.get("meta") or {}).get("theme"),
            "complexity": (plan.get("meta") or {}).get("complexity")
            or ((plan.get("meta") or {}).get("structure_brief") or {}).get("complexity"),
        },
        "counts": {
            "n_sections": len(plan.get("sections") or []),
            "n_blocks": len(blocks),
            "n_body_medium": int(lh.get("medium", 0)),
            "n_body_long": int(lh.get("long", 0)),
            "n_body": n_body,
            "n_table": int(lh.get("table", 0)),
            "n_image": int(lh.get("image", 0)),
            "length_hint": dict(lh),
            "heading_counts": dict(heading_counts),
            "heading_depth_used": depth or None,
        },
        "leaf": {
            "n_leaves": len(leaf_bodies),
            "body_per_leaf": leaf_bodies,
            "avg_body_per_leaf": (sum(leaf_bodies) / len(leaf_bodies)) if leaf_bodies else 0.0,
            "n_leaves_eq1": sum(1 for x in leaf_bodies if x == 1),
            "n_leaves_ge2": sum(1 for x in leaf_bodies if x >= 2),
            "n_leaves_eq0": sum(1 for x in leaf_bodies if x == 0),
        },
        "bridge": {
            "parents_with_children": parents_with_children,
            "with_bridge_before_first_child": with_bridge,
            "without_bridge_before_first_child": without_bridge,
            "bridge_rate": (with_bridge / parents_with_children) if parents_with_children else None,
        },
        "branching": {
            "parents_with_children": parents_with_children,
            "single_child_parents": single_child_parents,
            "multi_child_parents": multi_child_parents,
            "sibling_counts": sibling_counts,
            "avg_siblings_when_branched": (
                (sum(sibling_counts) / len(sibling_counts)) if sibling_counts else None
            ),
        },
        "estimates": {
            "body_chars_midband": est,
        },
    }


def write_outline_metrics(plan: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Write ``outline_metrics.json`` (+ short ``outline_metrics.md``). Never raises gates."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = measure_outline(plan)
    (out_dir / "outline_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    c = report.get("counts") or {}
    leaf = report.get("leaf") or {}
    br = report.get("bridge") or {}
    branch = report.get("branching") or {}
    lines = [
        "# Outline metrics · record only",
        "",
        "Soft structural fingerprint for later distribution / filtering. **Not** a pass/fail gate.",
        "",
        f"- sections = **{c.get('n_sections')}** · blocks = **{c.get('n_blocks')}**",
        f"- body medium/long = **{c.get('n_body_medium')}** / **{c.get('n_body_long')}** "
        f"(total body **{c.get('n_body')}**)",
        f"- tables/images = **{c.get('n_table')}** / **{c.get('n_image')}**",
        f"- headings = `{c.get('heading_counts')}` · depth = **{c.get('heading_depth_used')}**",
        f"- leaves = **{leaf.get('n_leaves')}** · avg body/leaf = "
        f"**{float(leaf.get('avg_body_per_leaf') or 0):.2f}** "
        f"· eq1 = **{leaf.get('n_leaves_eq1')}** · ge2 = **{leaf.get('n_leaves_ge2')}**",
        f"- bridge rate = **{br.get('bridge_rate')}** "
        f"({br.get('with_bridge_before_first_child')}/{br.get('parents_with_children')})",
        f"- branching: multi-child parents = **{branch.get('multi_child_parents')}** · "
        f"single-child = **{branch.get('single_child_parents')}**",
        f"- est. body mid-band chars ≈ **{(report.get('estimates') or {}).get('body_chars_midband')}**",
        "",
    ]
    (out_dir / "outline_metrics.md").write_text("\n".join(lines), encoding="utf-8")
    return report
