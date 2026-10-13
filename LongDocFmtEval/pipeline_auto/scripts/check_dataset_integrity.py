"""Scan pipeline_auto/datasets for unreadable / corrupted files.

Typical use after a partial delete+undo or a failed USB copy:

  python LongDocFmtEval/pipeline_auto/scripts/check_dataset_integrity.py
  python LongDocFmtEval/pipeline_auto/scripts/check_dataset_integrity.py --root D:\\path\\to\\datasets
  python LongDocFmtEval/pipeline_auto/scripts/check_dataset_integrity.py --topic 1_en_Annual_Report --strict-zip

Reports which topic / Cx sample / relative path failed, and writes a JSON report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = (
    Path(__file__).resolve().parents[1] / "datasets"
)

# Formats we can cheaply validate beyond "open + read a bit".
JSON_EXTS = {".json", ".jsonl"}
ZIP_EXTS = {".docx", ".xlsx", ".zip"}
PNG_EXTS = {".png"}
TEXTISH_EXTS = {".md", ".txt", ".yaml", ".yml", ".csv", ".tsv", ".xml"}


@dataclass
class Issue:
    topic: str
    sample: str  # e.g. C1 / C2 / C3 / topic_root / _batch_logs
    rel_path: str
    size: int
    kind: str
    detail: str


def _topic_and_sample(root: Path, path: Path) -> tuple[str, str]:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "?", "?"
    parts = rel.parts
    if not parts:
        return "?", "?"
    topic = parts[0]
    if len(parts) >= 2 and parts[1] in {"C1", "C2", "C3"}:
        return topic, parts[1]
    if topic.startswith("_"):
        return topic, "meta"
    return topic, "topic_root"


def _read_prefix(path: Path, nbytes: int = 65536) -> bytes:
    with path.open("rb") as f:
        return f.read(nbytes)


def _check_json(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return f"utf-8 decode failed: {e}"
    except OSError as e:
        return f"read failed: {e}"
    if path.suffix.lower() == ".jsonl":
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s:
                continue
            try:
                json.loads(s)
            except json.JSONDecodeError as e:
                return f"jsonl line {i}: {e}"
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        return f"json parse failed: {e}"
    return None


def _check_zip(path: Path, *, test: bool) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip() if test else None
            if bad is not None:
                return f"zip CRC failed on member: {bad}"
            # Empty archive is suspicious for docx/xlsx.
            if not zf.namelist():
                return "zip archive has no members"
            # Minimal OOXML markers.
            names = set(zf.namelist())
            if path.suffix.lower() == ".docx" and "[Content_Types].xml" not in names:
                return "docx missing [Content_Types].xml"
            if path.suffix.lower() == ".xlsx" and "[Content_Types].xml" not in names:
                return "xlsx missing [Content_Types].xml"
    except zipfile.BadZipFile as e:
        return f"bad zip: {e}"
    except OSError as e:
        return f"zip open failed: {e}"
    return None


def _check_png(path: Path) -> str | None:
    try:
        head = _read_prefix(path, 24)
    except OSError as e:
        return f"read failed: {e}"
    if len(head) < 8 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return "missing PNG signature"
    return None


def _check_textish(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"read failed: {e}"
    # Allow UTF-8 / UTF-8-SIG; reject hard binary with lots of NULs.
    if b"\x00" in raw[:4096]:
        return "unexpected NUL bytes in text-like file"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            return f"text decode failed: {e}"
    return None


def _empty_ok(path: Path) -> bool:
    """Some pipeline sidecars are legitimately empty (e.g. no LLM calls logged)."""
    parts = {p.lower() for p in path.parts}
    name = path.name.lower()
    if "usage" in parts and name in {"calls.jsonl", "calls.json"}:
        return True
    # Failed LLM rewrite attempts may leave 0-byte attempt logs.
    if ".attempt" in name and name.endswith(".txt"):
        return True
    return False


def check_file(path: Path, *, strict_zip: bool, allow_empty: bool) -> str | None:
    """Return error detail or None if OK."""
    try:
        st = path.stat()
    except OSError as e:
        return f"stat failed: {e}"

    if st.st_size == 0:
        if allow_empty or _empty_ok(path):
            return None
        return "empty file (0 bytes)"

    ext = path.suffix.lower()
    try:
        # Always prove the OS can read at least some bytes.
        _read_prefix(path, min(4096, st.st_size))
    except OSError as e:
        return f"unreadable: {e}"

    if ext in JSON_EXTS:
        return _check_json(path)
    if ext in ZIP_EXTS:
        return _check_zip(path, test=strict_zip)
    if ext in PNG_EXTS:
        return _check_png(path)
    if ext in TEXTISH_EXTS:
        return _check_textish(path)

    # Unknown binary: full sequential read to surface I/O errors / truncated media.
    try:
        with path.open("rb") as f:
            while f.read(1024 * 1024):
                pass
    except OSError as e:
        return f"full read failed: {e}"
    return None


def iter_files(root: Path, topic_filter: str | None) -> Iterable[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"datasets root not found: {root}")
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip caches.
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        p = Path(dirpath)
        if topic_filter:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] != topic_filter:
                # Still allow walking into root itself.
                if rel.parts:
                    continue
        for name in filenames:
            yield p / name


def scan(
    root: Path,
    *,
    topic_filter: str | None,
    strict_zip: bool,
    allow_empty: bool,
) -> tuple[list[Issue], int]:
    issues: list[Issue] = []
    total = 0
    for path in iter_files(root, topic_filter):
        total += 1
        detail = check_file(
            path, strict_zip=strict_zip, allow_empty=allow_empty
        )
        if detail is None:
            continue
        topic, sample = _topic_and_sample(root, path)
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        kind = detail.split(":", 1)[0].strip()
        issues.append(
            Issue(
                topic=topic,
                sample=sample,
                rel_path=str(path.relative_to(root)).replace("\\", "/"),
                size=size,
                kind=kind,
                detail=detail,
            )
        )
    return issues, total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"datasets root (default: {DEFAULT_ROOT})",
    )
    ap.add_argument(
        "--topic",
        default=None,
        help="Only scan one topic folder name, e.g. 1_en_Annual_Report",
    )
    ap.add_argument(
        "--strict-zip",
        action="store_true",
        help="CRC-test every member inside .docx/.xlsx (slower, more thorough)",
    )
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not report 0-byte files as errors",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write JSON report path (default: <root>/_integrity_report.json)",
    )
    args = ap.parse_args(argv)

    root = args.root.resolve()
    report_path = (args.report or (root / "_integrity_report.json")).resolve()

    print(f"Scanning: {root}")
    if args.topic:
        print(f"Topic filter: {args.topic}")
    print(f"strict_zip={args.strict_zip} allow_empty={args.allow_empty}")

    issues, total = scan(
        root,
        topic_filter=args.topic,
        strict_zip=args.strict_zip,
        allow_empty=args.allow_empty,
    )

    by_sample: dict[str, list[Issue]] = defaultdict(list)
    for iss in issues:
        by_sample[f"{iss.topic}/{iss.sample}"].append(iss)

    print(f"Checked {total} files; bad={len(issues)}")
    if issues:
        print("\nBroken samples (topic/Cx → file count):")
        for key in sorted(by_sample):
            print(f"  {key}: {len(by_sample[key])} file(s)")
        print("\nFirst failures:")
        for iss in issues[:50]:
            print(f"  [{iss.kind}] {iss.rel_path}  ({iss.detail})")
        if len(issues) > 50:
            print(f"  ... and {len(issues) - 50} more (see report)")

    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "total_files": total,
        "bad_files": len(issues),
        "bad_samples": sorted(by_sample),
        "issues": [asdict(x) for x in issues],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nReport: {report_path}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
