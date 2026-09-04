"""Write Word picture format via officecli (1:1 with image_reader schema)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read.image_reader import ImageFormatInfo, WordImageReader
from LongDocFormatter.officecli.read.image_schema import (
    image_format_to_officecli_props,
    parent_paragraph_path,
)

from ._format_props import assign_if_changed
from ._cli import OfficeCliError, extract_officecli_warnings, set_properties
from .paragraph_writer import WordParagraphWriter, text_format_to_officecli_props


def _host_paragraph_to_text_format(host: dict[str, Any]) -> dict[str, Any]:
    """Build minimal ``text_format`` for :class:`WordParagraphWriter`."""
    return {
        "alignment": host.get("alignment"),
        "pagination_control": host.get("pagination_control"),
    }


@dataclass
class ImageWriteResult:
    success: bool
    image_index: int
    path: str
    properties_set: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "image_index": self.image_index,
            "path": self.path,
            "properties_set": self.properties_set,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class ImageFullWriteResult:
    image: ImageWriteResult
    host_paragraph: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.image.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "image": self.image.to_dict(),
            "host_paragraph": self.host_paragraph,
        }


class WordImageWriter:
    """
    Apply inline picture format from :class:`~LongDocFormatter.officecli.read.image_reader.WordImageReader`.

    Writable: ``image_format.size`` and ``host_paragraph`` alignment / pagination.
    Floating pictures are not supported.
    """

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def apply_image_format(
        self,
        path: str,
        image_format: dict[str, Any],
        *,
        image_index: int = 0,
        reference_size: dict[str, Any] | None = None,
        content_width_cm: float | None = None,
        section_index: int | None = None,
    ) -> ImageWriteResult:
        current = WordImageReader(self.doc_path, officecli=self.officecli).read_at_path(
            path, include_host_paragraph=False
        )
        if not current:
            return ImageWriteResult(
                success=False,
                image_index=image_index,
                path=path,
                error="Picture not found or not inline (floating pictures are skipped).",
            )

        ref = reference_size
        if ref is None:
            size = current.image_format.get("size") or {}
            ref = {"width": size.get("width"), "height": size.get("height")}

        reader = WordImageReader(self.doc_path, officecli=self.officecli)
        cw = content_width_cm
        if cw is None:
            cw = reader.content_width_cm(
                section_index=section_index,
                picture_path=path,
            )

        props, warnings = image_format_to_officecli_props(
            image_format,
            reference_size=ref,
            content_width_cm=cw,
        )
        source_size = dict((current.image_format or {}).get("size") or {})
        filtered: dict[str, Any] = {}
        for key in ("width", "height"):
            if key not in props:
                continue
            assign_if_changed(filtered, key, props[key], source_size.get(key))
        props = filtered
        if not props:
            return ImageWriteResult(
                success=True,
                image_index=image_index,
                path=path,
                properties_set=[],
                warnings=warnings,
            )
        try:
            payload = set_properties(
                self.doc_path, path, props, officecli=self.officecli
            )
            warnings.extend(extract_officecli_warnings(payload))
            return ImageWriteResult(
                success=True,
                image_index=image_index,
                path=path,
                properties_set=sorted(props.keys()),
                warnings=warnings,
            )
        except OfficeCliError as exc:
            return ImageWriteResult(
                success=False,
                image_index=image_index,
                path=path,
                properties_set=sorted(props.keys()),
                warnings=warnings,
                error=str(exc),
            )

    def apply_host_paragraph(
        self,
        host_paragraph: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply ``host_paragraph.alignment`` / ``pagination_control``."""
        para_path = host_paragraph.get("path")
        if not para_path:
            return {"success": False, "error": "host_paragraph.path is required."}

        text_format = _host_paragraph_to_text_format(host_paragraph)
        source_text_format: dict[str, Any] | None = None
        try:
            from LongDocFormatter.officecli.read import WordTextReader

            source_info = WordTextReader(self.doc_path, officecli=self.officecli).read_at(
                para_path,
                merge_runs=True,
            )
            if source_info is not None:
                source_text_format = dict(source_info.text_format or {})
        except Exception:
            source_text_format = None
        props, warnings = text_format_to_officecli_props(
            text_format,
            source_text_format=source_text_format,
        )
        if not props:
            return {
                "success": True,
                "path": para_path,
                "properties_set": [],
                "warnings": warnings,
            }
        try:
            payload = set_properties(
                self.doc_path, para_path, props, officecli=self.officecli
            )
            warnings.extend(extract_officecli_warnings(payload))
            return {
                "success": True,
                "path": para_path,
                "properties_set": sorted(props.keys()),
                "warnings": warnings,
            }
        except OfficeCliError as exc:
            return {
                "success": False,
                "path": para_path,
                "properties_set": sorted(props.keys()),
                "warnings": warnings,
                "error": str(exc),
            }

    def apply_image(
        self,
        image_index: int,
        source: ImageFormatInfo | dict[str, Any],
        *,
        apply_host_paragraph: bool = True,
    ) -> ImageFullWriteResult:
        if isinstance(source, dict):
            path = source.get("path", "")
            image_format = source.get("image_format") or {}
            host = source.get("host_paragraph")
        else:
            path = source.path
            image_format = source.image_format
            host = source.host_paragraph

        if not path:
            failed = ImageWriteResult(
                success=False,
                image_index=image_index,
                path="",
                error="Picture path is required.",
            )
            return ImageFullWriteResult(image=failed)

        image_result = self.apply_image_format(
            path, image_format, image_index=image_index
        )

        host_result = None
        if apply_host_paragraph and host:
            host_result = self.apply_host_paragraph(host)
            image_result.warnings.extend(host_result.get("warnings") or [])
            if not host_result.get("success"):
                image_result.success = False

        return ImageFullWriteResult(image=image_result, host_paragraph=host_result)

    def apply_at_path(
        self,
        path: str,
        image_format: dict[str, Any],
        *,
        host_paragraph: dict[str, Any] | None = None,
        apply_host_paragraph: bool = True,
    ) -> ImageFullWriteResult:
        host = host_paragraph
        if host is None and apply_host_paragraph:
            para_path = parent_paragraph_path(path)
            if para_path:
                host = {"path": para_path}
        return self.apply_image(
            0,
            {"path": path, "image_format": image_format, "host_paragraph": host},
            apply_host_paragraph=apply_host_paragraph and host is not None,
        )

    def apply_from_reader(
        self,
        source_doc: str | Path,
        source_index: int,
        target_index: int,
        *,
        apply_host_paragraph: bool = True,
    ) -> ImageFullWriteResult:
        """Read picture ``source_index`` from ``source_doc``, write to target by index."""
        reader = WordImageReader(source_doc, officecli=self.officecli)
        source = reader.read_at(source_index)
        if not source:
            return ImageFullWriteResult(
                image=ImageWriteResult(
                    success=False,
                    image_index=target_index,
                    path="",
                    error=f"Source picture {source_index} not found.",
                )
            )

        target_reader = WordImageReader(self.doc_path, officecli=self.officecli)
        target = target_reader.read_at(target_index)
        if not target:
            return ImageFullWriteResult(
                image=ImageWriteResult(
                    success=False,
                    image_index=target_index,
                    path="",
                    error=f"Target picture {target_index} not found.",
                )
            )

        migrated_format = source.image_format
        migrated_host = source.host_paragraph
        if migrated_host and target.host_paragraph:
            migrated_host = {
                **migrated_host,
                "path": target.host_paragraph.get("path") or migrated_host.get("path"),
            }

        return self.apply_image(
            target_index,
            {
                "path": target.path,
                "image_format": migrated_format,
                "host_paragraph": migrated_host,
            },
            apply_host_paragraph=apply_host_paragraph,
        )
