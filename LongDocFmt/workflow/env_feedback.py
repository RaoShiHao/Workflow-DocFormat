"""Environment-gated retries for the compiled workflow.

One switch, ``env_feedback.enabled``. When it is off, plan / attribute /
localization / apply keep the previous open-loop behavior.

When it is on, each guard runs at most once:

- plan: template file cannot be opened → replan as text-only (if a requirement exists)
- attribute spec: declared fields outside the officecli whitelist → one revision
- element loc: unparseable JSON → one revision of that call
- apply: residual officecli failures after the mechanical command retry
  route back to attribute spec (bad field names) or element loc (bad paths)
"""
from __future__ import annotations

import re
from typing import Any

_FILE_ACCESS = (
    "no such file",
    "cannot find",
    "not found",
    "filenotfound",
    "does not exist",
    "doesn't exist",
    "系统找不到",
    "找不到指定",
    "找不到文件",
)

_ATTR_ERROR = re.compile(
    r"unknown (property|key|field|attr)|invalid (property|key|field|attr)|"
    r"unsupported (property|key|field)|unrecognized|"
    r"not a valid (property|field|key)|no such (property|key|field)|"
    r"属性|字段",
    re.I,
)
_LOC_ERROR = re.compile(
    r"\bpath\b|selector|not found|no such|cannot find|doesn't exist|does not exist|"
    r"找不到|不存在|invalid location|out of range",
    re.I,
)


def feedback_enabled(cfg: dict[str, Any] | None) -> bool:
    block = (cfg or {}).get("env_feedback") or {}
    return bool(block.get("enabled", False))


def is_file_access_error(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    msg = str(exc).lower()
    return any(needle in msg for needle in _FILE_ACCESS)


def classify_tool_error(message: str, cmd: dict[str, Any] | None = None) -> str:
    """``attribute`` → revise target props; ``location`` → revise element loc; else ``other``."""
    text = str(message or "")
    if _ATTR_ERROR.search(text):
        return "attribute"
    if _LOC_ERROR.search(text):
        return "location"
    cmd = cmd or {}
    kind = str(cmd.get("command") or cmd.get("op") or "")
    if kind == "add" and str(cmd.get("type") or "") == "style":
        return "attribute"
    if kind == "set":
        return "location"
    return "other"


def format_stage_feedback(failures: list[dict[str, Any]], kind: str) -> str:
    rows = [row for row in failures if str(row.get("kind") or "") == kind]
    if not rows:
        return ""
    if kind == "attribute":
        head = (
            "Officecli rejected these attribute names. "
            "Revise the declarations and use only whitelist field names:"
        )
    else:
        head = (
            "Officecli rejected these element paths. "
            "Re-assign only the locations that failed:"
        )
    lines = []
    for row in rows[:40]:
        lines.append(
            f"- {row.get('command') or ''} {row.get('path') or ''} "
            f"props={row.get('props') or []} error={row.get('message') or ''}"
        )
    return head + "\n" + "\n".join(lines)


