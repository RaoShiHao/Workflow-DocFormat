"""Read inline Word pictures via officecli (size + host paragraph)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._cli import get_element, query_elements
from ._ooxml_section import section_index_for_picture_path
from .format_schema import (
    build_alignment,
    build_pagination_control,
)
from .image_schema import (
    build_image_format,
    build_image_metadata,
    is_inline_picture_fmt,
    parent_paragraph_path,
)
from .image_size import content_width_cm_from_page_format


def _build_host_paragraph(
    doc_path: Path,
    picture_path: str,
    *,
    officecli: str,
) -> dict[str, Any] | None:
    """Host paragraph alignment + pagination."""
    para_path = parent_paragraph_path(picture_path)
    if not para_path:
        return None
    node = get_element(doc_path, para_path, officecli=officecli, depth=1)
    if not node:
        return {"path": para_path}
    fmt = dict(node.get("format") or {})
    return {
        "path": para_path,
        "alignment": build_alignment(fmt),
        "pagination_control": build_pagination_control(fmt),
    }


@dataclass
class ImageFormatInfo:
    """
    One **inline** picture in document order.

    ``image_format`` contains ``size`` only. Floating pictures are skipped.
    """

    image_index: int
    path: str
    image_format: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    host_paragraph: dict[str, Any] | None = None
    section_index: int = 1
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_index": self.image_index,
            "path": self.path,
            "preview": self.preview,
            "image_format": self.image_format,
            "metadata": self.metadata,
            "host_paragraph": self.host_paragraph,
            "section_index": self.section_index,
        }


class WordImageReader:
    """
    Read **inline** pictures (``wrap=inline``) via ``officecli query picture``.

    Floating pictures (``anchor=true``) are omitted. Format scope: ``size`` and
    host paragraph ``alignment`` / ``pagination_control``.
    """

    SELECTOR = "picture"

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")
        self._content_width_by_section: dict[int, float] = {}
        self._skipped_floating_count = 0

    @property
    def skipped_floating_count(self) -> int:
        """Floating pictures ignored on the last :meth:`read_all` pass."""
        return self._skipped_floating_count

    def section_index_for_path(self, picture_path: str) -> int:
        return section_index_for_picture_path(self.doc_path, picture_path)

    def content_width_cm(
        self,
        *,
        section_index: int | None = None,
        picture_path: str | None = None,
    ) -> float | None:
        """Text area width for write helper ``size.width_percent``."""
        if section_index is None:
            if picture_path:
                section_index = self.section_index_for_path(picture_path)
            else:
                section_index = 1
        if section_index in self._content_width_by_section:
            return self._content_width_by_section[section_index]
        from .page_reader import WordPageReader

        section = WordPageReader(self.doc_path, officecli=self.officecli).read_section_at(
            section_index
        )
        if section is None:
            return None
        cw = content_width_cm_from_page_format(section.page_format)
        if cw is not None:
            self._content_width_by_section[section_index] = cw
        return cw

    def _node_to_info(
        self,
        node: dict[str, Any],
        *,
        image_index: int,
        include_host_paragraph: bool = True,
    ) -> ImageFormatInfo | None:
        fmt = dict(node.get("format") or {})
        if not is_inline_picture_fmt(fmt):
            return None
        path = node.get("path", "")
        sec_index = self.section_index_for_path(path) if path else 1
        host = None
        if include_host_paragraph and path:
            host = _build_host_paragraph(
                self.doc_path, path, officecli=self.officecli
            )
        metadata = build_image_metadata(fmt)
        metadata["section_index"] = sec_index
        return ImageFormatInfo(
            image_index=image_index,
            path=path,
            image_format=build_image_format(fmt),
            metadata=metadata,
            host_paragraph=host,
            section_index=sec_index,
            preview=(node.get("text") or node.get("preview") or "").strip(),
        )

    def read_all(
        self,
        *,
        include_host_paragraph: bool = True,
    ) -> list[ImageFormatInfo]:
        """All **inline** pictures in document query order (1..n)."""
        nodes = query_elements(
            self.doc_path,
            self.SELECTOR,
            officecli=self.officecli,
        )
        self._skipped_floating_count = 0
        results: list[ImageFormatInfo] = []
        for node in nodes:
            fmt = dict(node.get("format") or {})
            if not is_inline_picture_fmt(fmt):
                self._skipped_floating_count += 1
                continue
            info = self._node_to_info(
                node,
                image_index=len(results) + 1,
                include_host_paragraph=include_host_paragraph,
            )
            if info:
                results.append(info)
        return results

    def read_at(
        self,
        image_index: int = 1,
        *,
        include_host_paragraph: bool = True,
    ) -> ImageFormatInfo | None:
        """One inline picture by 1-based index (inline-only ordering)."""
        items = self.read_all(include_host_paragraph=include_host_paragraph)
        if image_index < 1 or image_index > len(items):
            return None
        return items[image_index - 1]

    def read_at_path(
        self,
        path: str,
        *,
        include_host_paragraph: bool = True,
    ) -> ImageFormatInfo | None:
        node = get_element(self.doc_path, path, officecli=self.officecli)
        if not node or node.get("type") != "picture":
            return None
        fmt = dict(node.get("format") or {})
        if not is_inline_picture_fmt(fmt):
            return None
        inline_paths = [img.path for img in self.read_all(include_host_paragraph=False)]
        try:
            index = inline_paths.index(path) + 1
        except ValueError:
            index = 0
        return self._node_to_info(
            node,
            image_index=index,
            include_host_paragraph=include_host_paragraph,
        )
