"""Shared officecli subprocess helpers."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class OfficeCliError(RuntimeError):
    """Raised when officecli exits with an error or returns invalid JSON."""


def run_officecli(
    *args: str,
    officecli: str = "officecli",
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run officecli with arguments and parse JSON stdout."""
    cmd = [officecli, *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OfficeCliError(
            "officecli not found. Install: irm https://d.officecli.ai/install.ps1 | iex"
        ) from exc

    raw = (completed.stdout or "").strip()
    payload: dict[str, Any] | None = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None

    if payload is not None:
        if payload.get("success", True):
            return payload
        raise OfficeCliError(str(payload.get("error") or payload))

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        detail = stderr or raw or f"exit code {completed.returncode}"
        raise OfficeCliError(f"officecli failed: {detail}")

    if not raw:
        raise OfficeCliError("officecli returned empty output")
    raise OfficeCliError(f"officecli returned non-JSON output: {raw[:200]}")


def query_elements(
    doc_path: Path,
    selector: str,
    *,
    officecli: str = "officecli",
    find_text: str | None = None,
) -> list[dict[str, Any]]:
    """Run `officecli query <file> <selector> --json` and return result nodes."""
    args = ["query", str(doc_path), selector, "--json"]
    if find_text:
        args.extend(["--find", find_text])
    payload = run_officecli(*args, officecli=officecli)
    data = payload.get("data") or {}
    return list(data.get("results") or [])


def get_element(
    doc_path: Path,
    path: str,
    *,
    officecli: str = "officecli",
    depth: int = 0,
) -> dict[str, Any] | None:
    """Run `officecli get <file> <path> --json` and return the first match."""
    args = ["get", str(doc_path), path, "--json"]
    if depth > 0:
        args.extend(["--depth", str(depth)])
    payload = run_officecli(*args, officecli=officecli)
    data = payload.get("data") or {}
    results = data.get("results") or []
    return results[0] if results else None


def open_document(
    doc_path: Path | str,
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Start an officecli resident session for faster repeated get/set on the same file."""
    return run_officecli("open", str(doc_path), "--json", officecli=officecli)


def save_document(
    doc_path: Path | str,
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Flush resident in-memory edits to disk without stopping the resident."""
    return run_officecli("save", str(doc_path), "--json", officecli=officecli)


def close_document(
    doc_path: Path | str,
    *,
    officecli: str = "officecli",
) -> dict[str, Any]:
    """Flush resident edits to disk and stop the resident process."""
    return run_officecli("close", str(doc_path), "--json", officecli=officecli)


def close_stale_officecli_resident(
    doc_path: Path | str,
    *,
    officecli: str = "officecli",
) -> None:
    """Best-effort close for a leftover resident from an aborted prior run."""
    try:
        close_document(doc_path, officecli=officecli)
    except OfficeCliError:
        pass


@contextmanager
def officecli_document_session(
    doc_path: Path | str,
    *,
    officecli: str = "officecli",
    flush_on_exit: bool = True,
) -> Iterator[Path]:
    """
    Open the document once, run many officecli commands, then close.

    Subsequent ``get``/``set``/``query`` calls reuse the in-memory resident instead of
    reopening the file for every subprocess invocation.
    """
    path = Path(doc_path).resolve()
    open_document(path, officecli=officecli)
    try:
        yield path
    finally:
        if flush_on_exit:
            close_document(path, officecli=officecli)
