from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator


OFFICECLI = shutil.which("officecli") or "officecli"

# officecli / OOXML: C0 controls except \t \n \v \r are rejected on text props.
_XML_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0c\x0e-\x1f]")

# Active batch session for the current render / step7 delta write.
_BATCH_SESSION: ContextVar["OfficecliBatchSession | None"] = ContextVar(
    "officecli_batch_session", default=None
)


def sanitize_officecli_text(value: str) -> str:
    """Drop XML-illegal control chars; keep ``\\t`` ``\\n`` ``\\v`` ``\\r``."""
    if not value or not _XML_ILLEGAL_CTRL_RE.search(value):
        return value
    return _XML_ILLEGAL_CTRL_RE.sub("", value)


def _officecli_verbose() -> bool:
    """Echo officecli stdout/stderr only when explicitly requested."""
    return os.environ.get("OFFICECLI_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")


def officecli_batch_enabled() -> bool:
    """Default on; set ``OFFICECLI_BATCH=0`` to force per-command subprocesses."""
    return os.environ.get("OFFICECLI_BATCH", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def officecli_batch_chunk_size() -> int:
    raw = os.environ.get("OFFICECLI_BATCH_CHUNK", "80").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 80


def run_officecli(
    args: list[str],
    *,
    check: bool = True,
    verbose: bool | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run officecli. Success output is silent by default (no Added/Updated spam).

    Set ``verbose=True`` or env ``OFFICECLI_VERBOSE=1`` to echo CLI logs.
    Failures still raise with full stdout/stderr in the exception.
    """
    cmd = [OFFICECLI, *args]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    echo = _officecli_verbose() if verbose is None else bool(verbose)
    if echo:
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"officecli failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def create_docx(path: Path) -> None:
    flush_officecli_batch(path)
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    run_officecli(["create", str(path)])


def _render_prop_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return None
    return sanitize_officecli_text(str(value))


def _cli_border_value(value: Any) -> Any:
    """Bare ``4`` / ``12`` is a size, not a BorderValues style — officecli would fail."""
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (int, float)):
        if value == 0:
            return "nil"
        sz = str(int(value) if float(value).is_integer() else value)
        return f"single;{sz};auto"
    s = str(value).strip()
    if not s:
        return value
    if re.fullmatch(r"\d+(\.\d+)?(pt|cm|mm)?", s, flags=re.I):
        return f"single;{s};auto"
    if ";" in s:
        head, _, rest = s.partition(";")
        if re.fullmatch(r"\d+(\.\d+)?(pt|cm|mm)?", head.strip(), flags=re.I):
            return f"single;{head.strip()};{rest}"
    return value


def encode_props(props: dict[str, Any] | None) -> dict[str, str]:
    """Normalize props for ``officecli set`` / ``batch`` JSON (string values)."""
    out: dict[str, str] = {}
    for key, value in (props or {}).items():
        v: Any = _cli_border_value(value) if str(key).startswith("border.") else value
        rendered = _render_prop_value(v)
        if rendered is not None:
            out[str(key)] = rendered
    return out


def batch_unsupported(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "unknown command",
            "unrecognized",
            "no such command",
            "invalid command",
            "batch_unsupported",
        )
    )


def _run_add_immediate(doc: Path, path: str, *, typ: str, props: dict[str, Any]) -> None:
    args = ["add", str(doc), path, "--type", typ]
    for key, value in props.items():
        if str(key).startswith("border."):
            value = _cli_border_value(value)
        rendered = _render_prop_value(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    run_officecli(args)


def _run_set_immediate(doc: Path, path: str, props: dict[str, Any]) -> None:
    args = ["set", str(doc), path]
    for key, value in props.items():
        if str(key).startswith("border."):
            value = _cli_border_value(value)
        rendered = _render_prop_value(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    run_officecli(args)


def _execute_batch_cmd(doc: Path, cmd: dict[str, Any]) -> None:
    """Run one batch-shaped command via single CLI (fallback / force_immediate)."""
    kind = str(cmd.get("command") or cmd.get("op") or "").strip().lower()
    props = cmd.get("props") if isinstance(cmd.get("props"), dict) else {}
    raw_props: dict[str, Any] = dict(props)
    if kind == "add":
        parent = str(cmd.get("parent") or cmd.get("path") or "")
        typ = str(cmd.get("type") or "paragraph")
        _run_add_immediate(doc, parent, typ=typ, props=raw_props)
        return
    if kind == "set":
        path = str(cmd.get("path") or "")
        _run_set_immediate(doc, path, raw_props)
        return
    raise RuntimeError(f"unsupported batched command: {kind!r}")


class OfficecliBatchSession:
    """Queue ``add``/``set`` and flush via ``officecli batch`` (step5/6 render, step7 delta)."""

    def __init__(
        self,
        doc: Path,
        *,
        chunk_size: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.doc = Path(doc).resolve()
        self.chunk_size = max(1, int(chunk_size or officecli_batch_chunk_size()))
        self.enabled = officecli_batch_enabled() if enabled is None else bool(enabled)
        self._queue: list[dict[str, Any]] = []
        self.n_enqueued = 0
        self.n_batches = 0
        self.n_flushed = 0
        self.mode = "batch" if self.enabled else "sequential"

    def enqueue_add(self, parent: str, *, typ: str, props: dict[str, Any]) -> None:
        encoded = encode_props(props)
        if not self.enabled:
            _run_add_immediate(self.doc, parent, typ=typ, props=props)
            return
        self._queue.append(
            {"command": "add", "parent": parent, "type": typ, "props": encoded}
        )
        self.n_enqueued += 1
        if len(self._queue) >= self.chunk_size:
            self.flush()

    def enqueue_set(self, path: str, props: dict[str, Any]) -> None:
        encoded = encode_props(props)
        if not encoded:
            return
        if not self.enabled:
            _run_set_immediate(self.doc, path, props)
            return
        self._queue.append({"command": "set", "path": path, "props": encoded})
        self.n_enqueued += 1
        if len(self._queue) >= self.chunk_size:
            self.flush()

    def flush(self) -> None:
        if not self._queue:
            return
        pending = self._queue
        self._queue = []
        if not self.enabled:
            for cmd in pending:
                _execute_batch_cmd(self.doc, cmd)
                self.n_flushed += 1
            return
        for i in range(0, len(pending), self.chunk_size):
            chunk = pending[i : i + self.chunk_size]
            self._flush_chunk(chunk)

    def _flush_chunk(self, chunk: list[dict[str, Any]]) -> None:
        try:
            payload = batch_commands(self.doc, chunk, best_effort=False)
            self.n_batches += 1
            self.n_flushed += len(chunk)
            summary = payload.get("summary") if isinstance(payload, dict) else None
            failed = 0
            if isinstance(summary, dict):
                failed = int(summary.get("failed") or 0)
            if failed:
                raise RuntimeError(
                    f"officecli batch reported failed={failed} "
                    f"(chunk_size={len(chunk)})"
                )
            return
        except Exception as exc:
            if batch_unsupported(exc):
                self.mode = "sequential_fallback"
                print(f"[officecli] batch unsupported → sequential ({exc})")
            else:
                # Atomic rollback left doc unchanged for this chunk; replay one-by-one
                # so the real failing command surfaces with full CLI stderr.
                print(
                    f"[officecli] batch chunk failed → sequential replay "
                    f"({len(chunk)} cmds): {str(exc)[:160]}"
                )
                self.mode = "sequential_fallback"
            for cmd in chunk:
                _execute_batch_cmd(self.doc, cmd)
                self.n_flushed += 1

    def barrier(self) -> None:
        """Flush before any readback / try-paths / validate."""
        self.flush()


@contextmanager
def officecli_batch_session(
    doc: Path,
    *,
    chunk_size: int | None = None,
    enabled: bool | None = None,
) -> Iterator[OfficecliBatchSession]:
    """Buffer add/set for ``doc`` until flush/barrier/exit."""
    session = OfficecliBatchSession(doc, chunk_size=chunk_size, enabled=enabled)
    token = _BATCH_SESSION.set(session)
    try:
        yield session
        session.flush()
    finally:
        try:
            session.flush()
        except Exception:  # noqa: BLE001
            pass
        _BATCH_SESSION.reset(token)


def current_officecli_batch() -> OfficecliBatchSession | None:
    return _BATCH_SESSION.get()


def flush_officecli_batch(doc: Path | None = None) -> None:
    sess = _BATCH_SESSION.get()
    if sess is None:
        return
    if doc is not None and sess.doc != Path(doc).resolve():
        return
    sess.flush()


def add_props(
    doc: Path,
    path: str,
    *,
    typ: str,
    props: dict[str, Any],
    force_immediate: bool = False,
) -> None:
    sess = _BATCH_SESSION.get()
    if (
        not force_immediate
        and sess is not None
        and sess.doc == Path(doc).resolve()
    ):
        sess.enqueue_add(path, typ=typ, props=props)
        return
    _run_add_immediate(doc, path, typ=typ, props=props)


def set_props(
    doc: Path,
    path: str,
    props: dict[str, Any],
    *,
    force_immediate: bool = False,
) -> None:
    sess = _BATCH_SESSION.get()
    if (
        not force_immediate
        and sess is not None
        and sess.doc == Path(doc).resolve()
    ):
        sess.enqueue_set(path, props)
        return
    _run_set_immediate(doc, path, props)


def batch_commands(
    doc: Path,
    commands: list[dict[str, Any]],
    *,
    best_effort: bool = True,
) -> dict[str, Any]:
    """Run ``officecli batch --input`` (one process / resident pass).

    Prefer ``--best-effort`` so one bad item does not roll back the chunk
    (officecli default since v1.0.137 is atomic rollback on any failure).
    Under an open resident, disk flush is deferred until ``save``/``close``.
    Data-build render sessions typically pass ``best_effort=False``.
    """
    if not commands:
        return {"summary": {"total": 0, "succeeded": 0, "failed": 0}}
    public: list[dict[str, Any]] = []
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        item = {k: v for k, v in cmd.items() if not str(k).startswith("_")}
        if "command" not in item and "op" in item:
            item["command"] = item.pop("op")
        public.append(item)
    if not public:
        return {"summary": {"total": 0, "succeeded": 0, "failed": 0}}

    fd, name = tempfile.mkstemp(prefix="longdoc_batch_", suffix=".json")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
        args = ["batch", str(doc), "--input", str(tmp), "--json"]
        if best_effort:
            args.append("--best-effort")
        else:
            args.append("--stop-on-error")
        proc = run_officecli(args, check=False)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    err = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0 and batch_unsupported(RuntimeError(err)):
        raise RuntimeError("batch_unsupported: " + err[:400])
    raw = (proc.stdout or "").strip()
    if not raw:
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or f"batch exit {proc.returncode}")
        return {"summary": {"total": len(public), "succeeded": 0, "failed": 0}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        if proc.returncode != 0:
            raise RuntimeError(f"batch failed: {err[:400]}") from exc
        raise RuntimeError(f"non-JSON batch output: {raw[:240]}") from exc
    if not isinstance(payload, dict):
        return {"summary": {"total": len(public)}}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    out = data if isinstance(data, dict) else payload
    if proc.returncode != 0:
        summary = out.get("summary") if isinstance(out, dict) else None
        detail = json.dumps(summary, ensure_ascii=False) if summary else err[:400]
        raise RuntimeError(f"officecli batch exit {proc.returncode}: {detail}")
    return out if isinstance(out, dict) else {"summary": {"total": len(public)}}


def get_json(doc: Path, path: str) -> dict[str, Any]:
    flush_officecli_batch(doc)
    proc = run_officecli(["get", str(doc), path, "--json"])
    return json.loads(proc.stdout)


def validate_docx(doc: Path) -> None:
    flush_officecli_batch(doc)
    run_officecli(["validate", str(doc)])


def save_docx(doc: Path) -> None:
    """Flush resident changes without closing (officecli ``save``)."""
    flush_officecli_batch(doc)
    run_officecli(["save", str(doc)], check=False)


def close_docx(doc: Path) -> None:
    flush_officecli_batch(doc)
    run_officecli(["close", str(doc)], check=False)
