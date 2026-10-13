"""Word document format readers powered by officecli."""

from .page_reader import WordPageReader
from .table_reader import (
    TableCellFormatInfo,
    TableFormatInfo,
    TableParagraphInfo,
    TableRowFormatInfo,
    WordTableReader,
)
from .image_reader import ImageFormatInfo, WordImageReader
from .text_reader import TextFormatInfo, WordTextReader

__all__ = [
    "WordPageReader",
    "WordTextReader",
    "TextFormatInfo",
    "WordTableReader",
    "TableFormatInfo",
    "TableRowFormatInfo",
    "TableCellFormatInfo",
    "TableParagraphInfo",
    "WordImageReader",
    "ImageFormatInfo",
]
