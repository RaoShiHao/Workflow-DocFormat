"""Serialize officecli access across threads (LLM can still run in parallel)."""

from __future__ import annotations

import threading
from contextlib import contextmanager

_LOCK = threading.RLock()


@contextmanager
def officecli_exclusive():
    with _LOCK:
        yield
