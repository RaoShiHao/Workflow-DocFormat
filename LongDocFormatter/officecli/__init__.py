"""officecli Word read/write helpers used by LongDocFormatter."""

from LongDocFormatter.officecli.read import (
    WordImageReader,
    WordPageReader,
    WordTableReader,
    WordTextReader,
)

_WRITE_NAMES = frozenset(
    {
        "WordImageWriter",
        "WordPageWriter",
        "WordParagraphWriter",
        "WordTableWriter",
    }
)


def __getattr__(name: str):
    if name in _WRITE_NAMES:
        from LongDocFormatter.officecli import modify as _modify

        return getattr(_modify, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "WordPageReader",
    "WordTextReader",
    "WordTableReader",
    "WordImageReader",
    "WordPageWriter",
    "WordParagraphWriter",
    "WordTableWriter",
    "WordImageWriter",
]
