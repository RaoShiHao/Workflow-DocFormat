"""One-shot rewrite of officecli commands that the tool rejected.

Used where there is no target-attribute anchor to patch: NoAttribution,
NoTarget, and NoLocalization. The model sees only the failed commands and
the tool error, and may return replacements for those paths alone.
"""
from __future__ import annotations

from typing import Any

import yaml

from LongDocFmt.constants import DEFAULT_CONFIG_PATH
from LongDocFmt.workflow.env_feedback import feedback_enabled
from LongDocFmt.workflow.json_util import parse_llm_json
from LongDocFmt.workflow.whitelist import (
    CELL_KEYS,
    HEADER_FOOTER_KEYS,
    TABLE_FORMAT_KEYS,
    whitelist_keys,
)

_PUBLIC = ("command", "op", "path", "parent", "props", "type", "find")

_SYSTEM = (
    "Officecli rejected the commands below. Return one JSON object "
    '{"commands":[{"command":"set","path":"...","props":{}}]}. '
    "Emit replacements only for the listed commands. Keep each path unchanged. "
    "Use only whitelist field names. Do not add commands for any other path."
)


def env_feedback_from_default_config() -> bool:
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return feedback_enabled(loaded if isinstance(loaded, dict) else {})


def public_command(cmd: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _PUBLIC:
        value = cmd.get(key)
        if value in (None, "", {}, []):
            continue
        out[key] = value
    if "command" not in out and out.get("op"):
        out["command"] = out.pop("op")
    return out


def failure_paths(failures: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("path") or "") for row in failures or [] if str(row.get("path") or "")}


def _path_hit(cmd_path: str, wanted: set[str]) -> bool:
    if not cmd_path:
        return False
    if cmd_path in wanted:
        return True
    for item in wanted:
        if not item:
            continue
        if cmd_path.startswith(item.rstrip("/") + "/") or item.startswith(cmd_path.rstrip("/") + "/"):
            return True
    return False


def keep_repair_failure(failure: dict[str, Any], inventory_paths: set[str]) -> bool:
    """Style commands stay. Paths outside the inventory are dropped."""
    path = str(failure.get("path") or "")
    if not path:
        return False
    if path.startswith("/styles"):
        return True
    if not inventory_paths:
        return True
    return _path_hit(path, inventory_paths)


def commands_for_paths(commands: list[dict[str, Any]], paths: set[str]) -> list[dict[str, Any]]:
    """Commands whose path is one of the rejected paths."""
    picked: list[dict[str, Any]] = []
    for cmd in commands or []:
        if not isinstance(cmd, dict):
            continue
        path = str(cmd.get("path") or "")
        parent = str(cmd.get("parent") or "")
        if _path_hit(path, paths) or _path_hit(parent, paths):
            picked.append(cmd)
    return picked


def whitelist_field_names() -> str:
    names = set(whitelist_keys("paragraph.body"))
    names |= set(whitelist_keys("section"))
    names |= set(whitelist_keys("run"))
    names |= set(whitelist_keys("image"))
    names |= set(TABLE_FORMAT_KEYS)
    names |= set(CELL_KEYS)
    names |= set(HEADER_FOOTER_KEYS)
    return ", ".join(sorted(names))


def parse_replacement_commands(result: Any, allowed_paths: set[str]) -> list[dict[str, Any]]:
    """Keep replacements whose path is one of the commands we asked to fix."""
    parsed = parse_llm_json(result)
    rows: Any = None
    if isinstance(parsed, dict):
        rows = parsed.get("commands")
        if rows is None and parsed.get("command"):
            rows = [parsed]
    elif isinstance(parsed, list):
        rows = parsed
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or row.get("parent") or "")
        if allowed_paths and not _path_hit(path, allowed_paths):
            continue
        command = str(row.get("command") or row.get("op") or "")
        if not command:
            continue
        cleaned = public_command({**row, "command": command})
        if cleaned:
            out.append(cleaned)
    return out


def revise_failed_commands(
    language_model: Any,
    commands: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    llm_kwargs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One model call. Unparseable output yields no replacements."""
    picked = commands_for_paths(commands, failure_paths(failures))
    if not picked:
        return []
    allowed = failure_paths(failures)
    lines = []
    by_path = {str(cmd.get("path") or ""): cmd for cmd in picked}
    for failure in failures:
        path = str(failure.get("path") or "")
        cmd = by_path.get(path) or next(
            (item for item in picked if _path_hit(str(item.get("path") or ""), {path})),
            None,
        )
        lines.append(
            "command="
            + str(public_command(cmd) if cmd else {"path": path})
            + f" error={failure.get('message') or ''}"
        )
    user = (
        "Whitelist field names:\n"
        + whitelist_field_names()
        + "\n\nRejected commands:\n"
        + "\n".join(lines[:80])
    )
    result = language_model.chat_json(system=_SYSTEM, user=user, **dict(llm_kwargs or {}))
    return parse_replacement_commands(result, allowed)
