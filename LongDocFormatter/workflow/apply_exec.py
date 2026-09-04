"""Execute compiled officecli ops (re-exports the shared apply_core engine)."""

from __future__ import annotations

from LongDocFormatter.workflow.apply_core import (  # noqa: F401
    DEFAULT_CHUNK,
    add_cmd,
    execute_commands,
    merge_same_path,
    public_cmd,
    remove_cmd,
    set_cmd,
)
