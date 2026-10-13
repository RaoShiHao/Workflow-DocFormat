"""officecli subprocess helpers. External dependency: `officecli` on PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _bin() -> str:
    return shutil.which("officecli") or "officecli"


class OfficeCliError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [_bin(), *args]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise OfficeCliError(
            "officecli not found. Install from https://officecli.ai "
            "(Windows: irm https://d.officecli.ai/install.ps1 | iex)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OfficeCliError(
            f"officecli timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc
    if check and proc.returncode != 0:
        raise OfficeCliError(
            f"officecli failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def run_json(args: list[str]) -> dict[str, Any]:
    proc = run([*args, "--json"] if "--json" not in args else args, check=False)
    raw = (proc.stdout or "").strip()
    if not raw:
        if proc.returncode != 0:
            raise OfficeCliError(proc.stderr or f"exit {proc.returncode}")
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OfficeCliError(f"non-JSON officecli output: {raw[:240]}") from exc
    if isinstance(payload, dict) and payload.get("success") is False:
        raise OfficeCliError(str(payload.get("error") or payload))
    return payload if isinstance(payload, dict) else {}


def results_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or payload
    if isinstance(data, dict):
        raw = data.get("results") or data.get("Results") or []
    else:
        raw = payload.get("results") or []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def remove_path(doc: Path, path: str) -> None:
    run(["remove", str(doc), path], check=False)


def open_doc(doc: Path) -> None:
    run(["open", str(doc)], check=False)


def close_doc(doc: Path) -> None:
    run(["close", str(doc)], check=False)


def save_doc(doc: Path) -> None:
    """Flush resident changes without closing. No-op if nothing is open."""
    run(["save", str(doc)], check=False)


def encode_props(props: dict[str, Any] | None) -> dict[str, str]:
    """Normalize a props dict for `officecli set` / batch JSON."""
    out: dict[str, str] = {}
    for key, value in (props or {}).items():
        v: Any = _border(value) if str(key).startswith("border.") else value
        rendered = _render(v)
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


def batch_commands(
    doc: Path,
    commands: list[dict[str, Any]],
    *,
    best_effort: bool = True,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run `officecli batch --input` against an open resident.

    Prefer --best-effort so one bad item does not roll back the chunk.
    Disk flush is the caller's job (`save` / `close`).
    """
    if not commands:
        return {"summary": {"total": 0, "succeeded": 0, "failed": 0}}
    import tempfile

    fd, name = tempfile.mkstemp(prefix="longdoc_batch_", suffix=".json")
    os.close(fd)
    tmp = Path(name)
    tmp.write_text(json.dumps(commands, ensure_ascii=False), encoding="utf-8")
    args = ["batch", str(doc), "--input", str(tmp), "--json"]
    if best_effort:
        args.append("--best-effort")
    try:
        proc = run(args, check=False, timeout=timeout)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    err = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0 and batch_unsupported(OfficeCliError(err)):
        raise OfficeCliError("batch_unsupported: " + err[:400])
    raw = (proc.stdout or "").strip()
    if not raw:
        if proc.returncode != 0:
            raise OfficeCliError(proc.stderr or f"batch exit {proc.returncode}")
        return {"summary": {"total": len(commands), "succeeded": 0, "failed": 0}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        if proc.returncode != 0:
            raise OfficeCliError(f"batch failed: {err[:400]}") from exc
        raise OfficeCliError(f"non-JSON batch output: {raw[:240]}") from exc
    if not isinstance(payload, dict):
        return {"summary": {"total": len(commands)}}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else payload


def query(doc: Path, selector: str) -> list[dict[str, Any]]:
    return results_of(run_json(["query", str(doc), selector, "--json"]))


def get_node(doc: Path, path: str, *, depth: int = 1) -> dict[str, Any]:
    payload = run_json(["get", str(doc), path, "--depth", str(depth), "--json"])
    rows = results_of(payload)
    return rows[0] if rows else {}


def _render(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return None
    s = str(value)
    return _sanitize(s)


def _sanitize(value: str) -> str:
    import re as _re
    return _re.sub(r"[\x00-\x08\x0c\x0e-\x1f]", "", value)


def _border(value: Any) -> Any:
    import re as _re
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
    if _re.fullmatch(r"\d+(\.\d+)?(pt|cm|mm)?", s, flags=_re.I):
        return f"single;{s};auto"
    if ";" in s:
        head, _, rest = s.partition(";")
        if _re.fullmatch(r"\d+(\.\d+)?(pt|cm|mm)?", head.strip(), flags=_re.I):
            return f"single;{head.strip()};{rest}"
    return value


def set_props(doc: Path, path: str, props: dict[str, Any], *, find: str | None = None) -> None:
    args = ["set", str(doc), path]
    if find:
        args.extend(["--find", find])
    for key, value in (props or {}).items():
        if str(key).startswith("border."):
            value = _border(value)
        rendered = _render(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    if len(args) <= 3 or (find and len(args) <= 5):
        return
    run(args)


def add_props(doc: Path, path: str, *, typ: str, props: dict[str, Any]) -> None:
    args = ["add", str(doc), path, "--type", typ]
    for key, value in (props or {}).items():
        if str(key).startswith("border."):
            value = _border(value)
        rendered = _render(value)
        if rendered is None:
            continue
        args.extend(["--prop", f"{key}={rendered}"])
    run(args)
