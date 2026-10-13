"""Flatten a .docx into the LongDocFmtEval eval key space via officecli.

Matches ``LongDocFmt.workflow.eval_acc.flatten_doc`` on inventory
``profile=eval`` (whitelist props, real ``tc`` cells, delta runs). When
``LongDocFmt`` is importable, delegates to ``snapshot`` so keys match
the Windows eval cache exactly.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

_PAGE_NUM_TEXT_RE = re.compile(r"^(\d+|[ivxlcdm]+|[IVXLCDM]+)$")
_RUN_TAIL_RE = re.compile(r"/(?:w:)?(?:r|run)\[\d+\](?:/.*)?$", re.I)
_HEADING_OUTLINE = {
    "heading1": 0,
    "paraheading1": 0,
    "heading 1": 0,
    "heading2": 1,
    "paraheading2": 1,
    "heading 2": 1,
    "heading3": 2,
    "paraheading3": 2,
    "heading 3": 2,
    "heading4": 3,
    "paraheading4": 3,
    "heading 4": 3,
    "heading5": 4,
    "heading6": 5,
}

_SKIP_READBACK = {
    "path", "type", "text", "childCount", "children", "id", "name", "customStyle",
    "basedOn", "basedOn.path", "next", "linked", "uiPriority", "qFormat", "semiHidden",
    "unhideWhenUsed", "personal", "personalCompose", "personalReply", "aliases",
    "paraId", "style", "styleId", "styleName", "lineRule",
    "headerRef", "headerRef.default", "headerRef.first", "headerRef.even",
    "footerRef", "footerRef.default", "footerRef.first", "footerRef.even",
    "sectionType", "relId", "contentType", "fileSize", "alt",
}
_EFFECTIVE_TO_PROP = {
    "effective.size": "size",
    "effective.bold": "bold",
    "effective.italic": "italic",
    "effective.alignment": "align",
    "effective.spaceBefore": "spaceBefore",
    "effective.spaceAfter": "spaceAfter",
    "effective.lineSpacing": "lineSpacing",
    "effective.firstLineIndent": "firstLineIndent",
    "effective.hangingIndent": "hangingIndent",
    "effective.leftIndent": "indent",
    "effective.rightIndent": "rightIndent",
    "effective.outlineLvl": "outlineLvl",
    "effective.color": "color",
    "effective.underline": "underline",
    "effective.keepNext": "keepNext",
    "effective.pageBreakBefore": "pageBreakBefore",
}
PARAGRAPH_KEYS = (
    "font.latin", "font.ea", "size", "bold", "italic", "color", "align",
    "lineSpacing", "spaceBefore", "spaceAfter", "firstLineIndent", "hangingIndent",
    "indent", "rightIndent", "outlineLvl", "keepNext", "pageBreakBefore",
    "listStyle", "numLevel",
)
SECTION_KEYS = (
    "marginTop", "marginBottom", "marginLeft", "marginRight",
    "marginHeader", "marginFooter", "orientation", "columns", "pgBorders",
    "type", "pageNumFmt", "pageStart", "titlePage",
)
TABLE_FORMAT_KEYS = (
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
    "align", "width", "layout", "repeat_header",
)
CELL_KEYS = (
    "valign", "fill", "shading",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
)
IMAGE_KEYS = ("width", "height", "hAlign")
RUN_KEYS = (
    "bold", "italic", "superscript", "subscript", "caps", "smallcaps",
    "color", "underline", "font.ea", "font.latin", "size",
)
_NORMAL_STYLE_IDS = {"a", "normal"}
_DOC_DEFAULT_SRC = "/docdefaults"


class OfficeCLI:
    def __init__(self, binary: str = "officecli") -> None:
        self.binary = binary
        self._opened: str | None = None

    def _run(self, args: list[str]) -> Any:
        proc = subprocess.run(
            [self.binary, *args, "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        text = (proc.stdout or "").strip()
        if not text:
            raise RuntimeError(
                f"officecli {' '.join(args)} failed rc={proc.returncode}: {(proc.stderr or '')[-400:]}"
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"officecli returned non-JSON: {text[:400]}") from exc
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise RuntimeError(f"officecli {' '.join(args)}: {parsed}")
        return parsed

    def open(self, path: Path) -> None:
        self._run(["open", str(path)])
        self._opened = str(path)

    def close(self) -> None:
        if not self._opened:
            return
        try:
            self._run(["close", self._opened])
        finally:
            self._opened = None

    def query(self, selector: str) -> list[dict[str, Any]]:
        assert self._opened
        raw = self._run(["query", self._opened, selector])
        data = raw.get("data") if isinstance(raw, dict) else raw
        results = data.get("results") if isinstance(data, dict) else data
        return [x for x in (results or []) if isinstance(x, dict)]

    def get(self, path: str, depth: int = 1) -> dict[str, Any] | None:
        assert self._opened
        raw = self._run(["get", self._opened, path, "--depth", str(depth)])
        data = raw.get("data") if isinstance(raw, dict) else raw
        results = data.get("results") if isinstance(data, dict) else data
        if isinstance(results, list) and results:
            node = results[0]
            return node if isinstance(node, dict) else None
        return None


def _fmt(node: dict[str, Any] | None) -> dict[str, Any]:
    if not node:
        return {}
    raw = node.get("format")
    return raw if isinstance(raw, dict) else {}


def _text_of(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    t = node.get("text") or _fmt(node).get("text") or ""
    return str(t).strip()


def _src_is_doc_defaults(fmt: dict[str, Any], key: str) -> bool:
    return str(fmt.get(f"{key}.src") or "").strip().lower() == _DOC_DEFAULT_SRC


def clean_readback(fmt: dict[str, Any] | None) -> dict[str, Any]:
    fmt = fmt or {}
    out: dict[str, Any] = {}
    for k, v in fmt.items():
        if k in _SKIP_READBACK or v in (None, "", "none"):
            continue
        if k.endswith(".path") or k.endswith(".xml") or str(k).startswith("markRPr"):
            continue
        if str(k).startswith("effective."):
            continue
        if str(k).startswith("headerRef") or str(k).startswith("footerRef"):
            continue
        out[k] = v
    if "font.latin" not in out:
        latin = out.get("font.ascii") or out.get("font.hAnsi")
        if latin:
            out["font.latin"] = latin
    ea = out.get("font.eastAsia") or out.get("font.ea")
    if ea:
        out["font.ea"] = ea
    return out


def props_from_effective(fmt: dict[str, Any] | None, *, skip_doc_defaults: bool = False) -> dict[str, Any]:
    fmt = fmt or {}
    out: dict[str, Any] = {}
    for ek, pk in _EFFECTIVE_TO_PROP.items():
        v = fmt.get(ek)
        if v in (None, "", "none"):
            continue
        if skip_doc_defaults and _src_is_doc_defaults(fmt, ek):
            continue
        out[pk] = v
    ea = fmt.get("effective.font.eastAsia") or fmt.get("effective.font.ea")
    if ea and not (
        skip_doc_defaults
        and (
            _src_is_doc_defaults(fmt, "effective.font.eastAsia")
            or _src_is_doc_defaults(fmt, "effective.font.ea")
        )
    ):
        out["font.ea"] = ea
    latin = fmt.get("effective.font.ascii") or fmt.get("effective.font.hAnsi")
    if latin and not (
        skip_doc_defaults
        and (
            _src_is_doc_defaults(fmt, "effective.font.ascii")
            or _src_is_doc_defaults(fmt, "effective.font.hAnsi")
        )
    ):
        out["font.latin"] = latin
    return out


def merge_element_props(fmt: dict[str, Any] | None) -> dict[str, Any]:
    direct = clean_readback(fmt)
    effective = props_from_effective(fmt)
    merged = dict(effective)
    merged.update(direct)
    return merged


def merge_paragraph_props(
    para_fmt: dict[str, Any] | None,
    style_fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = merge_element_props(style_fmt)
    para_direct = clean_readback(para_fmt)
    para_eff = props_from_effective(para_fmt, skip_doc_defaults=True)
    for k, v in para_eff.items():
        if k not in merged:
            merged[k] = v
    merged.update(para_direct)
    return merged


def filter_props(props: dict[str, Any] | None, keys: tuple[str, ...] | list[str]) -> dict[str, Any]:
    allowed = set(keys)
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if k in allowed and v not in (None, "", "none"):
            out[k] = v
    return out


def _norm_body_path(path: str) -> str:
    p = str(path or "").replace("\\", "/").lower()
    p = p.replace("/w:tbl[", "/table[")
    p = p.replace("/tbl[", "/table[")
    p = p.replace("/w:p[", "/p[")
    return p


def _path_is_table_cell_para(path: str) -> bool:
    p = path.replace("\\", "/")
    return "/tc[" in p or "/tbl[" in p or "/table[" in p


def _parent_para_path(run_path: str) -> str:
    return _RUN_TAIL_RE.sub("", str(run_path or "").replace("\\", "/"))


def _tbl_cell_key(path: str) -> str | None:
    p = str(path or "").replace("\\", "/")
    tbl = re.search(r"/(?:tbl|table)\[(\d+)\]", p, re.I)
    tr = re.search(r"/tr\[(\d+)\]", p)
    tc = re.search(r"/tc\[(\d+)\]", p)
    if not (tbl and tr and tc):
        return None
    return f"{tbl.group(1)}:{tr.group(1)}:{tc.group(1)}"


def _is_table_cell_element(path: str, typ: str) -> bool:
    t = str(typ or "").lower()
    if t in {"cell", "tc"}:
        return True
    p = str(path or "").replace("\\", "/")
    return bool(re.search(r"/tc\[\d+\]$", p))


def _index_query_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("path") or "")
        if not path:
            continue
        out.setdefault(path, row)
        out.setdefault(_norm_body_path(path), row)
        cell = _tbl_cell_key(path)
        if cell:
            out.setdefault("cell:" + cell, row)
    return out


def _row_for_path(index: dict[str, dict[str, Any]], path: str) -> dict[str, Any] | None:
    if not path:
        return None
    hit = index.get(path) or index.get(_norm_body_path(path))
    if hit:
        return hit
    cell = _tbl_cell_key(path)
    if cell:
        return index.get("cell:" + cell)
    return None


def _linked_style_id(row: dict[str, Any] | None, fmt: dict[str, Any] | None) -> str | None:
    for bag in (row or {}, fmt or {}):
        for key in ("style", "styleId"):
            val = bag.get(key)
            if val and str(val).strip().lower() not in _NORMAL_STYLE_IDS:
                return str(val)
    return None


def _outline_from_style_name(fmt: dict[str, Any]) -> int | None:
    for key in ("style", "styleId", "styleName"):
        name = str(fmt.get(key) or "").strip().lower().replace(" ", "")
        if name in _HEADING_OUTLINE:
            return _HEADING_OUTLINE[name]
    return None


def _write(flat: dict[str, Any], prefix: str, props: dict[str, Any]) -> None:
    for k, v in props.items():
        if isinstance(v, dict):
            continue
        flat[f"{prefix}.{k}"] = v


class _StyleCache:
    def __init__(self, cli: OfficeCLI) -> None:
        self.cli = cli
        self._by_id: dict[str, dict[str, Any]] = {}

    def fmt(self, style_id: str | None) -> dict[str, Any]:
        key = str(style_id or "").strip()
        if not key:
            return {}
        if key in self._by_id:
            return self._by_id[key]
        self._by_id[key] = {}
        try:
            node = self.cli.get(f"/styles/{key}", depth=0)
        except RuntimeError:
            node = None
        child = _fmt(node)
        parent_id = child.get("basedOn")
        merged = dict(self.fmt(str(parent_id))) if parent_id else {}
        merged.update({k: v for k, v in child.items() if v not in (None, "")})
        self._by_id[key] = merged
        return merged


def _walk_nodes(node: dict[str, Any] | None):
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from _walk_nodes(child)


def _paragraph_props(
    row: dict[str, Any],
    styles: _StyleCache,
    keys: tuple[str, ...] = PARAGRAPH_KEYS,
) -> dict[str, Any]:
    fmt = dict(row.get("format") or {})
    sid = _linked_style_id(row, fmt)
    style_fmt = styles.fmt(sid) if sid else {}
    props = filter_props(merge_paragraph_props(fmt, style_fmt), keys)
    if props.get("outlineLvl") in (None, "", "none"):
        lvl = _outline_from_style_name(fmt) or _outline_from_style_name(style_fmt)
        if lvl is not None:
            props["outlineLvl"] = lvl
    return props


def flatten_docx(path: Path | str, *, binary: str = "officecli") -> dict[str, Any]:
    """Return the same dotted-key mapping as dataset eval/*.flat.json."""
    doc = Path(path)
    if not doc.is_file():
        raise FileNotFoundError(doc)
    if str(binary or "officecli") == "officecli":
        try:
            from LongDocFmt.workflow.eval_acc import snapshot

            return snapshot(doc)
        except ImportError:
            pass
    return flatten_docx_officecli(doc, binary=binary)


def flatten_docx_officecli(path: Path | str, *, binary: str = "officecli") -> dict[str, Any]:
    """Standalone officecli flatten (Linux runner without LongDocFmt)."""
    doc = Path(path)
    if not doc.is_file():
        raise FileNotFoundError(doc)
    cli = OfficeCLI(binary)
    flat: dict[str, Any] = {}
    cli.open(doc)
    try:
        styles = _StyleCache(cli)
        para_rows = cli.query("paragraph")
        para_index = _index_query_rows(para_rows)
        body_paras = _body_paragraphs(para_rows, styles)
        _flatten_sections(cli, flat)
        _write_paragraphs(flat, body_paras)
        _flatten_images(cli, flat, para_index)
        _flatten_tables(cli, flat, styles, para_index)
        _flatten_runs(cli, flat, body_paras)
    finally:
        cli.close()
    return flat


def _body_paragraphs(
    para_rows: list[dict[str, Any]],
    styles: _StyleCache,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    loc = 0
    for row in para_rows:
        path = str(row.get("path") or "")
        if not path.startswith("/body"):
            continue
        if _path_is_table_cell_para(path):
            continue
        loc += 1
        out.append({"location_id": loc, "path": path, "props": _paragraph_props(row, styles), "row": row})
    return out


def _write_paragraphs(flat: dict[str, Any], paras: list[dict[str, Any]]) -> None:
    for el in paras:
        _write(flat, f"paragraphs.{el['location_id']}", el["props"])


def _flatten_sections(cli: OfficeCLI, flat: dict[str, Any]) -> None:
    sections = cli.query("section")
    for i, node in enumerate(sections, start=1):
        fmt = _fmt(node)
        props = filter_props(merge_element_props(fmt), SECTION_KEYS)
        _write(flat, f"section.{i}", props)
        mapping = {
            "header": fmt.get("headerRef.default") or fmt.get("headerRef"),
            "footer": fmt.get("footerRef.default") or fmt.get("footerRef"),
            "header_first": fmt.get("headerRef.first"),
        }
        for part, ref in mapping.items():
            if not ref:
                continue
            hf_node = cli.get(str(ref), depth=2)
            nfmt = merge_element_props(_fmt(hf_node))
            bag = {
                "text": _text_of(hf_node),
                "font.ea": nfmt.get("font.ea"),
                "font.latin": nfmt.get("font.latin"),
                "size": nfmt.get("size"),
                "align": nfmt.get("align"),
            }
            if not any(bag.get(x) for x in bag):
                continue
            for k, v in bag.items():
                if v in (None, ""):
                    continue
                if part == "footer" and k == "text" and _PAGE_NUM_TEXT_RE.match(str(v).strip()):
                    continue
                flat[f"section.{i}.{part}.{k}"] = v


def _flatten_images(
    cli: OfficeCLI,
    flat: dict[str, Any],
    para_index: dict[str, dict[str, Any]],
) -> None:
    pics = cli.query("picture") or cli.query("image")
    for i, node in enumerate(pics, start=1):
        path = str(node.get("path") or "")
        props = filter_props(merge_element_props(_fmt(node)), IMAGE_KEYS)
        para_path = path.rsplit("/r[", 1)[0] if "/r[" in path else path
        if "hAlign" not in props:
            hit = _row_for_path(para_index, para_path)
            pfmt = merge_element_props(_fmt(hit))
            align = pfmt.get("align")
            if align not in (None, ""):
                props["hAlign"] = align
        _write(flat, f"images.{i}", props)


def _flatten_tables(
    cli: OfficeCLI,
    flat: dict[str, Any],
    styles: _StyleCache,
    para_index: dict[str, dict[str, Any]],
) -> None:
    tables = cli.query("table")
    for t_i, node in enumerate(tables, start=1):
        path = str(node.get("path") or f"/body/tbl[{t_i}]")
        tree = cli.get(path, depth=4) or node
        tf = filter_props(merge_element_props(_fmt(tree) or _fmt(node)), TABLE_FORMAT_KEYS)
        _write(flat, f"tables.{t_i}.table_format", tf)
        for child in _walk_nodes(tree):
            cpath = str(child.get("path") or "")
            typ = str(child.get("type") or "").lower()
            if not _is_table_cell_element(cpath, typ):
                continue
            cfmt = merge_element_props(_fmt(child))
            chrome = {k: cfmt[k] for k in CELL_KEYS if k in cfmt}
            tr = re.search(r"/tr\[(\d+)\]", cpath)
            tc = re.search(r"/tc\[(\d+)\]", cpath)
            row_i = int(tr.group(1)) if tr else 0
            col_i = int(tc.group(1)) if tc else 0
            if chrome:
                _write(flat, f"tables.{t_i}.cells.r{row_i}_c{col_i}", chrome)
            para_path = cpath + "/p[1]" if "/p[" not in cpath else cpath
            hit = _row_for_path(para_index, para_path)
            row = dict(hit or {})
            row["path"] = para_path
            if hit and hit.get("format"):
                row["format"] = hit["format"]
            props = _paragraph_props(row, styles)
            _write(flat, f"paragraph_cells.{t_i}:{row_i}:{col_i}", props)


def _flatten_runs(
    cli: OfficeCLI,
    flat: dict[str, Any],
    body_paras: list[dict[str, Any]],
) -> None:
    run_rows = cli.query("run")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        path = str(row.get("path") or "")
        if not path or _path_is_table_cell_para(path):
            continue
        parent = _norm_body_path(_parent_para_path(path))
        grouped.setdefault(parent, []).append(row)
    loc = 0
    for para in body_paras:
        host = filter_props(para["props"], RUN_KEYS)
        for child in grouped.get(_norm_body_path(para["path"])) or []:
            text = str(child.get("text") or _fmt(child).get("text") or "")
            if not text:
                continue
            fmt = filter_props(merge_element_props(_fmt(child)), RUN_KEYS)
            delta = {k: v for k, v in fmt.items() if host.get(k) != v}
            if not delta:
                continue
            loc += 1
            _write(flat, f"runs.{loc}", delta)
