"""Document content integrity (body text / tables / images unchanged)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read.image_schema import is_inline_picture_fmt, parent_paragraph_path
from LongDocFormatter.workflow.officecli_lock import officecli_exclusive

from ._text_utils import has_meaningful_text, normalize_visible_text

_BODY_P_RE = re.compile(r"/body/p(?:\[|$)")
Token = tuple[Any, ...]


@dataclass
class IntegrityReport:
    status: str
    reference_doc: str
    candidate_doc: str
    reference_tokens: int = 0
    candidate_tokens: int = 0
    differences: list[dict[str, Any]] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reference_doc": self.reference_doc,
            "candidate_doc": self.candidate_doc,
            "reference_tokens": self.reference_tokens,
            "candidate_tokens": self.candidate_tokens,
            "differences": self.differences,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "IntegrityReport":
        d = data or {}
        return cls(
            status=str(d.get("status") or "ok"),
            reference_doc=str(d.get("reference_doc") or ""),
            candidate_doc=str(d.get("candidate_doc") or ""),
            reference_tokens=int(d.get("reference_tokens") or 0),
            candidate_tokens=int(d.get("candidate_tokens") or 0),
            differences=list(d.get("differences") or []),
            checked_at=str(d.get("checked_at") or ""),
        )


def content_stream_from_query_rows(
    para_rows: list[dict[str, Any]] | None,
    picture_rows: list[dict[str, Any]] | None,
) -> list[Token]:
    """Body text + inline images from already-fetched ``query paragraph`` / ``query picture`` rows."""
    image_by_host: dict[str, int] = {}
    img_i = 0
    for node in picture_rows or []:
        fmt = dict(node.get("format") or {})
        if not is_inline_picture_fmt(fmt):
            continue
        img_i += 1
        host = parent_paragraph_path(str(node.get("path") or ""))
        if host:
            image_by_host[host] = img_i

    tokens: list[Token] = []
    for para in para_rows or []:
        path = str(para.get("path") or "")
        if not _BODY_P_RE.search(path):
            continue
        text = para.get("text") or para.get("preview") or (para.get("format") or {}).get("text") or ""
        if has_meaningful_text(text):
            tokens.append(("text", path, normalize_visible_text(text)))
        elif path in image_by_host:
            tokens.append(("image", image_by_host[path], path))
    return tokens


def _collect_content_stream_unlocked(doc_path: Path, *, officecli: str) -> list[Token]:
    """Body text + inline images only (same coverage as the old body_only walker)."""
    from LongDocFormatter.officecli.read._cli import query_elements

    pics = query_elements(doc_path, "picture", officecli=officecli)
    paras = query_elements(doc_path, "paragraph", officecli=officecli)
    return content_stream_from_query_rows(paras, pics)


def collect_content_stream(
    doc_path: Path,
    *,
    officecli: str = "officecli",
    already_open: bool = False,
) -> list[Token]:
    """Integrity token stream. Reuses an existing officecli resident when ``already_open``."""
    from LongDocFormatter.officecli.read._cli import close_document, open_document

    doc_path = Path(doc_path).resolve()
    if already_open:
        return _collect_content_stream_unlocked(doc_path, officecli=officecli)
    with officecli_exclusive():
        open_document(doc_path, officecli=officecli)
        try:
            return _collect_content_stream_unlocked(doc_path, officecli=officecli)
        finally:
            close_document(doc_path, officecli=officecli)


def build_content_stream(doc_path: Path, *, officecli: str = "officecli") -> list[Token]:
    return collect_content_stream(doc_path, officecli=officecli, already_open=False)


def tokens_to_json(tokens: list[Token]) -> list[Any]:
    out: list[Any] = []
    for token in tokens:
        row = list(token)
        if row and row[0] == "table" and len(row) > 5:
            row[5] = list(row[5])
        out.append(row)
    return out


def tokens_from_json(raw: Any) -> list[Token]:
    tokens: list[Token] = []
    for row in raw or []:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        kind = row[0]
        if kind == "table":
            cells = row[5] if len(row) > 5 else []
            tokens.append(("table", row[1], row[2], row[3], row[4], tuple(cells)))
        elif kind == "text":
            tokens.append(("text", row[1], row[2]))
        elif kind == "image":
            tokens.append(("image", row[1], row[2]))
    return tokens


def _tokens_equal(ref: Token | None, cand: Token | None) -> bool:
    if ref is None or cand is None:
        return ref is cand
    if ref[0] != cand[0]:
        return False
    if ref[0] == "text":
        return ref[2] == cand[2]
    if ref[0] == "image":
        return ref[1] == cand[1]
    if ref[0] == "table":
        return ref[1] == cand[1] and ref[3] == cand[3] and ref[4] == cand[4] and ref[5] == cand[5]
    return ref == cand


def _token_to_json(token: Token | None) -> Any:
    return None if token is None else list(token)


def report_from_streams(
    reference_doc: Path,
    candidate_doc: Path,
    ref: list[Token],
    cand: list[Token],
) -> IntegrityReport:
    differences: list[dict[str, Any]] = []
    for i in range(max(len(ref), len(cand))):
        a = ref[i] if i < len(ref) else None
        b = cand[i] if i < len(cand) else None
        if _tokens_equal(a, b):
            continue
        if a is None:
            differences.append({"type": "inserted", "index": i, "token": _token_to_json(b)})
        elif b is None:
            differences.append({"type": "deleted", "index": i, "token": _token_to_json(a)})
        elif a[0] == "text" and b[0] == "text":
            differences.append(
                {
                    "type": "text_changed",
                    "index": i,
                    "reference": {"path": a[1], "text": a[2]},
                    "candidate": {"path": b[1], "text": b[2]},
                }
            )
        else:
            differences.append(
                {
                    "type": "token_changed",
                    "index": i,
                    "reference": _token_to_json(a),
                    "candidate": _token_to_json(b),
                }
            )
    return IntegrityReport(
        status="ok" if not differences else "tampered",
        reference_doc=str(Path(reference_doc).resolve()),
        candidate_doc=str(Path(candidate_doc).resolve()),
        reference_tokens=len(ref),
        candidate_tokens=len(cand),
        differences=differences,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def check_document_integrity(
    reference_doc: Path,
    candidate_doc: Path,
    *,
    officecli: str = "officecli",
    reference_stream: list[Token] | None = None,
    candidate_stream: list[Token] | None = None,
) -> IntegrityReport:
    ref = (
        reference_stream
        if reference_stream is not None
        else build_content_stream(reference_doc, officecli=officecli)
    )
    cand = (
        candidate_stream
        if candidate_stream is not None
        else build_content_stream(candidate_doc, officecli=officecli)
    )
    return report_from_streams(reference_doc, candidate_doc, ref, cand)


def save_integrity_report(report: IntegrityReport, path: Path) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
