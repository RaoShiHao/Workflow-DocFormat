"""Which ``text_format`` fields are migrated vs read-only metadata (e.g. ``style_name``)."""

from __future__ import annotations

from typing import Any

# Keep in sync with ``LongDocFormatter.workflow.migration_property_scope.PARAGRAPH_PATH_A_KEYS``.
PARAGRAPH_MIGRATION_FORMAT_KEYS = frozenset(
    {
        "base_font",
        "advanced_font",
        "alignment",
        "outline_level",
        "pagination_control",
        "spacing",
        "indent",
        "runs",
    }
)

PARAGRAPH_READ_METADATA_KEYS = frozenset({"style_name"})


def text_format_for_migration(text_format: dict[str, Any] | None) -> dict[str, Any]:
    """Subset safe to pass to :class:`WordParagraphWriter` for format migration."""
    from LongDocFormatter.workflow.migration_property_scope import text_format_for_path_a

    return text_format_for_path_a(text_format)


def text_format_metadata(text_format: dict[str, Any] | None) -> dict[str, Any]:
    """Read-only paragraph metadata (not written during tag migration)."""
    if not text_format:
        return {}
    return {
        key: text_format[key]
        for key in PARAGRAPH_READ_METADATA_KEYS
        if key in text_format
    }
