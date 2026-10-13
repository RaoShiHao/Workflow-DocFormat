"""Rebuild map.json paragraph indices from {style-name} markers in docx.

After human insert/delete/reorder of paragraphs, indices in map.paragraph drift.
Markers in body text are the source of truth for paragraph roles.

Looks for ``template.docx`` (seed) first, then ``preview.docx`` (step3).

Usage:
  python LongDocFmtEval/pipeline_auto/seed/sync_map_from_markers.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/work/step4_seeds/field_survey_theme1

  python LongDocFmtEval/pipeline_auto/seed/sync_map_from_markers.py \\
    --seed-dir LongDocFmtEval/pipeline_auto/work/step3_templates/01_five_year_plan_outline_zh__theme1
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: E402
    dump_json, load_json, strip_para_marker,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _para_texts(docx: Path) -> list[str]:
    with zipfile.ZipFile(docx, "r") as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find("w:body", NS)
    if body is None:
        return []
    out: list[str] = []
    for child in list(body):
        if child.tag.split("}")[-1] != "p":
            continue
        parts: list[str] = []
        for t in child.iter(f"{{{W_NS}}}t"):
            if t.text:
                parts.append(t.text)
        out.append("".join(parts))
    return out


def _resolve_docx(seed_dir: Path) -> Path:
    for name in ("template.docx", "preview.docx"):
        cand = seed_dir / name
        if cand.exists():
            return cand
    raise FileNotFoundError(f"neither template.docx nor preview.docx in {seed_dir}")


def sync_map(seed_dir: Path) -> dict:
    seed_dir = seed_dir.resolve()
    doc = _resolve_docx(seed_dir)
    maps = load_json(seed_dir / "map.json") if (seed_dir / "map.json").exists() else {}
    styles = load_json(seed_dir / "styles.json") if (seed_dir / "styles.json").exists() else {}

    paragraph: dict[str, list[int]] = {}
    paragraph_objects: dict[str, dict] = {}
    unmarked = 0
    para_i = 0
    for text in _para_texts(doc):
        para_i += 1
        rid, body = strip_para_marker(text)
        if not rid:
            unmarked += 1
            continue
        paragraph.setdefault(rid, []).append(para_i)
        # synthetic object id by order within role
        oid = f"auto_{rid}_{len(paragraph[rid])}"
        paragraph_objects[oid] = {
            "para_index": para_i,
            "style_id": rid,
            "marker": "{" + rid + "}",
            "text_preview": body[:80],
        }

    maps["paragraph"] = paragraph
    maps["paragraph_objects"] = paragraph_objects
    # refresh paragraph exemplars in styles.json (first occurrence)
    styles.setdefault("paragraph", {})
    for rid, indices in paragraph.items():
        styles["paragraph"][rid] = {"style_id": rid, "para_index": indices[0]}

    dump_json(seed_dir / "map.json", maps)
    dump_json(seed_dir / "styles.json", styles)
    marked = sum(len(v) for v in paragraph.values())
    print(
        f"[sync-map] paragraphs={para_i} marked={marked} unmarked={unmarked} "
        f"roles={len(paragraph)} → {seed_dir / 'map.json'}"
    )
    return maps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True)
    args = ap.parse_args()
    sync_map(Path(args.seed_dir))


if __name__ == "__main__":
    main()
