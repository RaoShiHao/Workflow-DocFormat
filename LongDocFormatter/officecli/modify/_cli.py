"""officecli write helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read._cli import (
    OfficeCliError,
    close_document,
    get_element,
    officecli_document_session,
    open_document,
    query_elements,
    run_officecli,
    save_document,
)

__all__ = [
    "OfficeCliError",
    "add_element",
    "clear_header_footer_content",
    "close_document",
    "extract_officecli_warnings",
    "get_element",
    "officecli_document_session",
    "open_document",
    "query_elements",
    "run_officecli",
    "save_document",
    "set_properties",
    "set_with_find",
]


def _format_prop_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _append_props_args(args: list[str], props: dict[str, Any]) -> None:
    for key, value in props.items():
        if value is None:
            continue
        args.extend(["--prop", f"{key}={_format_prop_value(value)}"])


def extract_officecli_warnings(payload: dict[str, Any]) -> list[str]:
    warnings = payload.get("warnings") or []
    messages: list[str] = []
    for item in warnings:
        if isinstance(item, dict) and item.get("message"):
            messages.append(str(item["message"]))
        elif isinstance(item, str):
            messages.append(item)
    return messages


def matched_count(payload: dict[str, Any]) -> int | None:
    """Return find match count from officecli JSON (top-level or nested in data)."""
    if "matched" in payload:
        return int(payload["matched"])
    data = payload.get("data")
    if isinstance(data, dict) and "matched" in data:
        return int(data["matched"])
    return None


def set_properties(
    doc_path: Path,
    path: str,
    props: dict[str, Any],
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Run ``officecli set <file> <path> --prop k=v ... --json``."""
    args: list[str] = ["set", str(doc_path), path]
    _append_props_args(args, props)
    if len(args) == 3:
        raise OfficeCliError(f"No properties to set on {path}")
    args.append("--json")
    return run_officecli(*args, officecli=officecli)


def add_element(
    doc_path: Path,
    parent_path: str,
    element_type: str,
    props: dict[str, Any] | None = None,
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Run ``officecli add <file> <parent> --type TYPE --prop k=v ... --json``."""
    args: list[str] = ["add", str(doc_path), parent_path, "--type", element_type]
    _append_props_args(args, props or {})
    args.append("--json")
    return run_officecli(*args, officecli=officecli)


def set_with_find(
    doc_path: Path,
    path: str,
    find_text: str,
    props: dict[str, Any],
    *,
    regex: bool = False,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """
    Run ``officecli set <file> <path> --find TEXT --prop k=v ... --json``.

    Formats matched text inside the element (auto-splits runs). Use for ``runs[]`` writes.
    """
    if not find_text:
        raise OfficeCliError("find_text must be non-empty for set_with_find")
    args: list[str] = ["set", str(doc_path), path, "--find", find_text]
    _append_props_args(args, props)
    if regex:
        args.extend(["--prop", "regex=true"])
    if len(args) <= 4:
        raise OfficeCliError(f"No format properties to set for find={find_text!r}")
    args.append("--json")
    return run_officecli(*args, officecli=officecli)


def clear_header_footer_content(
    doc_path: Path,
    path: str,
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Clear header/footer body text (and fields) before applying new content."""
    return set_properties(
        doc_path,
        path,
        {"text": ""},
        officecli=officecli,
    )
