"""Extract embedded pictures from a .docx, named by picture id.

Preferred path (fast):
  1. ``officecli query picture --json`` → body-order list with ``id`` / ``relId`` / path
  2. One ZipFile pass over ``word/media`` + document rels, copy bytes to ``{id}{ext}``

Fallback: ``officecli get <picture-path> --save {id}{ext}`` (official binary extract).
EMF/WMF are kept as-is; raster formats may be downscaled for VL.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from LongDocFormatter.officecli.runner import close_docx, run_officecli
from LongDocFormatter.workflow.officecli_doc import list_images, open_docx
from LongDocFormatter.workflow.officecli_lock import officecli_exclusive

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-emf": ".emf",
    "image/emf": ".emf",
    "image/x-wmf": ".wmf",
    "image/wmf": ".wmf",
    "image/svg+xml": ".svg",
}


def _ext_for(content_type: str, media_name: str = "") -> str:
    if content_type in _MIME_EXT:
        return _MIME_EXT[content_type]
    suf = Path(media_name).suffix.lower()
    if suf:
        return suf
    return ".bin"


def _sniff_ext(path: Path) -> str:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return path.suffix.lower() or ".bin"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head.startswith(b"BM"):
        return ".bmp"
    return path.suffix.lower() or ".bin"


def _safe_id(raw: Any, fallback: int) -> str:
    s = str(raw).strip() if raw not in (None, "") else ""
    s = re.sub(r"[^\w.-]+", "_", s)
    return s or str(fallback)


def _rels_map(zf: zipfile.ZipFile) -> Dict[str, str]:
    """rId → zip member path under word/."""
    try:
        xml = zf.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = ET.fromstring(xml)
    out: Dict[str, str] = {}
    for rel in root.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
        rid = rel.get("Id") or ""
        target = (rel.get("Target") or "").replace("\\", "/")
        if not rid or not target:
            continue
        if target.startswith("/"):
            member = target.lstrip("/")
        else:
            member = "word/" + target
        out[rid] = member
    return out


def _blips_in_document_order(zf: zipfile.ZipFile) -> List[Dict[str, Any]]:
    try:
        xml = zf.read("word/document.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    body = root.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body")
    if body is None:
        return []
    out: List[Dict[str, Any]] = []
    for drawing in body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"):
        docpr = None
        for el in drawing.iter():
            if el.tag.endswith("}docPr"):
                docpr = el
                break
        blip = None
        for el in drawing.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
            blip = el
            break
        if blip is None:
            continue
        embed = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        pic_id = docpr.get("id") if docpr is not None else None
        name = docpr.get("name") if docpr is not None else ""
        out.append({"id": pic_id, "relId": embed, "name": name})
    return out


def _maybe_downscale(src: Path, dest: Path, max_edge: int) -> Path:
    if max_edge <= 0:
        if src != dest:
            dest.write_bytes(src.read_bytes())
        return dest
    try:
        from PIL import Image
    except ImportError:
        if src != dest:
            dest.write_bytes(src.read_bytes())
        return dest
    if src.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}:
        if src != dest:
            dest.write_bytes(src.read_bytes())
        return dest
    try:
        with Image.open(src) as im:
            if dest.suffix.lower() in {".jpg", ".jpeg"} and im.mode != "RGB":
                im = im.convert("RGB")
            elif im.mode not in {"RGB", "RGBA", "L"}:
                im = im.convert("RGB")
            w, h = im.size
            if max(w, h) <= max_edge:
                if src != dest:
                    dest.write_bytes(src.read_bytes())
                return dest if src == dest else dest
            scale = max_edge / float(max(w, h))
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            im = im.resize((nw, nh))
            dest.parent.mkdir(parents=True, exist_ok=True)
            save_kw = {"optimize": True}
            if dest.suffix.lower() in {".jpg", ".jpeg"}:
                save_kw["quality"] = 85
            im.save(dest, **save_kw)
            return dest
    except Exception:
        if src != dest:
            dest.write_bytes(src.read_bytes())
        return dest


def _save_via_officecli(doc: Path, picture_path: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = run_officecli(
        ["get", str(doc), picture_path, "--save", str(dest)],
        check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def extract_embedded_images(
    doc: Path | str,
    output_dir: Path | str,
    *,
    max_edge: int = 1024,
    use_officecli_save_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """Write ``{picture_id}{ext}`` under *output_dir*. Returns manifest rows.

    ``picture_id`` prefers OOXML ``wp:docPr/@id`` / officecli ``format.id``;
    collisions get a ``_{n}`` suffix. ``location_id`` is 1-based body order
    (the id used by Style Assignment).
    """
    doc = Path(doc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    queried: List[Dict[str, Any]] = []
    try:
        with officecli_exclusive():
            open_docx(doc)
            try:
                for el in list_images(doc):
                    queried.append(
                        {
                            "location_id": el.location_id,
                            "path": el.path,
                            "id": el.meta.get("officecli_id"),
                            "relId": el.meta.get("relId"),
                            "contentType": el.meta.get("contentType"),
                            "props": el.props,
                        }
                    )
            finally:
                close_docx(doc)
    except Exception:
        queried = []

    rels: Dict[str, str] = {}
    zip_blips: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(doc) as zf:
            rels = _rels_map(zf)
            zip_blips = _blips_in_document_order(zf)
    except Exception:
        rels, zip_blips = {}, []

    zip_by_rel = {str(z.get("relId")): z for z in zip_blips if z.get("relId")}
    zip_by_id = {str(z.get("id")): z for z in zip_blips if z.get("id") not in (None, "")}

    # Same body order as inventory list_images (officecli query). Zip is byte lookup.
    pairs: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
    if queried:
        for q in queried:
            z = zip_by_rel.get(str(q.get("relId") or "")) or zip_by_id.get(str(q.get("id") or "")) or {}
            pairs.append((q, z))
    else:
        pairs = [({}, z) for z in zip_blips]

    used_names: set[str] = set()
    manifest: List[Dict[str, Any]] = []

    for i, (q, z) in enumerate(pairs, start=1):
        raw_id = q.get("id") if q.get("id") not in (None, "") else z.get("id")
        pic_id = _safe_id(raw_id, i)
        name = pic_id
        suffix = 2
        while name in used_names:
            name = f"{pic_id}_{suffix}"
            suffix += 1
        used_names.add(name)

        rel_id = q.get("relId") or z.get("relId")
        member = rels.get(str(rel_id) or "") if rel_id else ""
        content_type = str(q.get("contentType") or "")
        ext = _ext_for(content_type, member)
        dest = output_dir / f"{name}{ext}"
        written = False
        source = "none"

        if member:
            try:
                with zipfile.ZipFile(doc) as zf:
                    data = zf.read(member)
                tmp = output_dir / f".tmp_{name}{Path(member).suffix or ext}"
                tmp.write_bytes(data)
                _maybe_downscale(tmp, dest, max_edge)
                if tmp != dest and tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                written = dest.is_file() and dest.stat().st_size > 0
                source = "zip"
            except Exception:
                written = False

        save_path = str(q.get("path") or "")
        if not save_path and raw_id not in (None, ""):
            save_path = f"/picture[@id={raw_id}]"
        if not written and use_officecli_save_fallback and save_path:
            raw_dest = output_dir / f"{name}{ext}"
            if _save_via_officecli(doc, save_path, raw_dest):
                sniffed = _sniff_ext(raw_dest)
                if sniffed != raw_dest.suffix.lower():
                    renamed = raw_dest.with_suffix(sniffed)
                    try:
                        raw_dest.replace(renamed)
                        raw_dest = renamed
                        dest = output_dir / f"{name}{sniffed}"
                    except OSError:
                        pass
                if max_edge > 0 and raw_dest.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    _maybe_downscale(raw_dest, dest, max_edge)
                written = dest.is_file()
                source = "officecli-save"

        row = {
            "location_id": i,
            "picture_id": name,
            "officecli_id": raw_id,
            "relId": rel_id,
            "path": q.get("path") or save_path,
            "file": str(dest) if written else "",
            "contentType": content_type,
            "source": source if written else "failed",
            "props": q.get("props") or {},
        }
        manifest.append(row)

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def attach_image_files(elements, manifest: List[Dict[str, Any]]) -> None:
    by_loc = {int(r["location_id"]): r for r in manifest if r.get("location_id") is not None}
    for el in elements:
        row = by_loc.get(int(el.location_id))
        if row and row.get("file"):
            el.image_path = row["file"]
            el.meta["picture_id"] = row.get("picture_id")


if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Extract docx pictures named by picture id.")
    p.add_argument("docx")
    p.add_argument("-o", "--out", default="extracted_images")
    p.add_argument("--max-edge", type=int, default=1024)
    args = p.parse_args()
    rows = extract_embedded_images(args.docx, args.out, max_edge=args.max_edge)
    ok = sum(1 for r in rows if r.get("file"))
    print(f"extracted {ok}/{len(rows)} → {args.out}")
    for r in rows:
        print(f"  id={r.get('picture_id')} loc={r.get('location_id')} {r.get('source')} {r.get('file')}")
    sys.exit(0 if ok == len(rows) else 1)

