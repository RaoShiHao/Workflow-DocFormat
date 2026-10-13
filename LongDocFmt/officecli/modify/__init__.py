"""Write Word documents via officecli."""

from .page_writer import PageWriteResult, SectionMappingError, WordPageWriter
from ._cli import set_with_find
from ._format_props import (
    assign_if_changed,
    augment_font_props_for_explicit_font,
    font_format_to_officecli_props,
    has_explicit_font_props,
    resolve_bool_write,
    run_entry_to_officecli_props,
)
from .text_format_scope import (
    PARAGRAPH_MIGRATION_FORMAT_KEYS,
    text_format_for_migration,
    text_format_metadata,
)
from .paragraph_writer import (
    ParagraphNotFoundError,
    ParagraphRunsWriteResult,
    ParagraphWriteResult,
    RunApplyResult,
    TextFormatWriteResult,
    WordParagraphWriter,
    text_format_to_officecli_props,
)
from .image_writer import (
    ImageFullWriteResult,
    ImageWriteResult,
    WordImageWriter,
)
from .table_writer import (
    CellWriteResult,
    RowWriteResult,
    TableFullWriteResult,
    TableWriteResult,
    WordTableWriter,
    cell_format_to_officecli_props,
    row_format_to_officecli_props,
    table_format_to_officecli_props,
)

__all__ = [
    "WordPageWriter",
    "PageWriteResult",
    "SectionMappingError",
    "WordParagraphWriter",
    "ParagraphWriteResult",
    "ParagraphRunsWriteResult",
    "TextFormatWriteResult",
    "RunApplyResult",
    "ParagraphNotFoundError",
    "text_format_to_officecli_props",
    "text_format_for_migration",
    "text_format_metadata",
    "PARAGRAPH_MIGRATION_FORMAT_KEYS",
    "font_format_to_officecli_props",
    "augment_font_props_for_explicit_font",
    "has_explicit_font_props",
    "run_entry_to_officecli_props",
    "resolve_bool_write",
    "assign_if_changed",
    "set_with_find",
    "WordTableWriter",
    "TableWriteResult",
    "RowWriteResult",
    "TableFullWriteResult",
    "CellWriteResult",
    "table_format_to_officecli_props",
    "row_format_to_officecli_props",
    "cell_format_to_officecli_props",
    "WordImageWriter",
    "ImageWriteResult",
    "ImageFullWriteResult",
]
