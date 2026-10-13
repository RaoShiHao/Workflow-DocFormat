"""Strip leading ``{RoleId}`` markers by copying a docx and surgically editing XML.

Do **not** re-serialize ``document.xml`` with ElementTree — that rewrites namespaces /
declaration and Word often refuses to open the result. Instead:

  1. ``shutil``-style zip copy preserving each member's ZipInfo / compress_type
  2. Edit only ``<w:t>…</w:t>`` text inside ``word/document.xml`` as raw UTF-8 text

Convention:
  - ``template.docx``       — keep **with** markers
  - ``template.clean.docx`` — deliverable **without** markers
"""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

# Leading {PascalCaseRole}
_MARKER_RE = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}")
# Paragraph blocks in document.xml (non-greedy; nested tables still use flat w:p)
_PARA_RE = re.compile(r"<w:p(?:\s[^>]*)?>[\s\S]*?</w:p>")
# w:t with optional attributes (xml:space="preserve" etc.)
_WT_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)([\s\S]*?)(</w:t>)")

CLEAN_DOCX_NAME = "template.clean.docx"


def _strip_one_paragraph_xml(para: str) -> tuple[str, bool]:
    """Remove leading {RoleId} from concatenated w:t texts; preserve surrounding XML."""
    matches = list(_WT_RE.finditer(para))
    if not matches:
        return para, False
    plain = "".join(m.group(2) for m in matches)
    m = _MARKER_RE.match(plain)
    if not m:
        return para, False
    remaining = m.end()
    new_texts: list[str] = []
    for tm in matches:
        text = tm.group(2)
        if remaining <= 0:
            new_texts.append(text)
        elif len(text) <= remaining:
            new_texts.append("")
            remaining -= len(text)
        else:
            new_texts.append(text[remaining:])
            remaining = 0
    # Rebuild from the end so offsets stay valid
    out = para
    for tm, new_t in zip(reversed(matches), reversed(new_texts)):
        out = out[: tm.start()] + tm.group(1) + new_t + tm.group(3) + out[tm.end() :]
    return out, True


def strip_markers_in_document_xml(xml_bytes: bytes) -> tuple[bytes, int]:
    """Return new document.xml bytes and number of paragraphs changed."""
    # Preserve BOM / encoding; OOXML is UTF-8
    text = xml_bytes.decode("utf-8")
    parts: list[str] = []
    last = 0
    changed = 0
    for pm in _PARA_RE.finditer(text):
        parts.append(text[last : pm.start()])
        new_para, did = _strip_one_paragraph_xml(pm.group(0))
        if did:
            changed += 1
        parts.append(new_para)
        last = pm.end()
    parts.append(text[last:])
    return "".join(parts).encode("utf-8"), changed


def write_clean_docx(src: Path, dst: Path | None = None) -> tuple[Path, int]:
    """Copy ``src`` → ``dst`` (default sibling ``template.clean.docx``), strip markers.

    Leaves ``src`` untouched. Preserves zip entry metadata so Word can open the file.
    """
    src = src.resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    dst = (dst or src.with_name(CLEAN_DOCX_NAME)).resolve()

    tmp = dst.with_suffix(dst.suffix + ".tmp")
    changed = 0
    try:
        with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/document.xml":
                    data, changed = strip_markers_in_document_xml(data)
                # Preserve compression + timestamps (critical for Word)
                new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                new_info.create_system = info.create_system
                new_info.flag_bits = info.flag_bits
                zout.writestr(new_info, data)
        if dst.exists():
            dst.unlink()
        tmp.replace(dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dst, changed


def strip_para_markers_docx(docx: Path, *, inplace: bool = False) -> int:
    if inplace:
        _, n = write_clean_docx(docx, dst=docx)
        return n
    _, n = write_clean_docx(docx)
    return n


def write_clean_in_dir(seed_dir: Path, *, src_name: str = "template.docx") -> Path | None:
    src = seed_dir / src_name
    if not src.is_file():
        print(f"[strip-markers] skip: missing {src}")
        return None
    dst, n = write_clean_docx(src, seed_dir / CLEAN_DOCX_NAME)
    print(
        f"[strip-markers] {src.name} → {dst.name} "
        f"(removed {{Role}} from {n} paragraphs; source kept with markers)"
    )
    return dst


def strip_in_seed_dir(seed_dir: Path, *, name: str = "template.docx") -> int:
    """Back-compat: write clean sibling; do not mutate ``name``."""
    src = seed_dir / name
    if not src.is_file():
        print(f"[strip-markers] skip: missing {src}")
        return 0
    _, n = write_clean_docx(src, seed_dir / CLEAN_DOCX_NAME)
    print(f"[strip-markers] → {CLEAN_DOCX_NAME} ({n} paragraphs); {name} kept with markers")
    return n


def prefer_clean_docx(seed_dir: Path) -> Path | None:
    clean = seed_dir / CLEAN_DOCX_NAME
    return clean if clean.is_file() else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Copy docx and strip {RoleId} markers → template.clean.docx (Word-safe)"
    )
    ap.add_argument("--docx", default="", help="source .docx (kept with markers)")
    ap.add_argument("--out", default="", help=f"output path (default: sibling {CLEAN_DOCX_NAME})")
    ap.add_argument("--seed-dir", default="", help="dir with template.docx")
    ap.add_argument("--inplace", action="store_true", help="overwrite source (not recommended)")
    args = ap.parse_args()
    if args.seed_dir:
        write_clean_in_dir(Path(args.seed_dir))
    elif args.docx:
        src = Path(args.docx)
        if args.inplace:
            n = strip_para_markers_docx(src, inplace=True)
            print(f"[strip-markers] inplace {n} paragraphs ← {src}")
        else:
            out, n = write_clean_docx(src, Path(args.out) if args.out else None)
            print(f"[strip-markers] {src.name} → {out} ({n} paragraphs)")
    else:
        ap.error("need --docx or --seed-dir")


if __name__ == "__main__":
    main()
