"""Resolve step1 draft dirs / step2 outline dirs by stable numeric ids."""
from __future__ import annotations

import json
import re
from pathlib import Path


def parse_ids_arg(text: str) -> list[int]:
    """Parse '1,2,5' or '1-3,8' into sorted unique ints."""
    ids: list[int] = []
    for part in (text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            if lo > hi:
                lo, hi = hi, lo
            ids.extend(range(lo, hi + 1))
        else:
            ids.append(int(part))
    return sorted(set(ids))


_DIR_ID_RE = re.compile(r"^(\d+)_")
_THEME_SUFFIX_RE = re.compile(r"__theme(\d+)$", re.I)


def _read_meta(draft_dir: Path) -> dict | None:
    meta_path = draft_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def find_draft_dirs_by_id(drafts_root: Path, genre_id: int) -> list[Path]:
    """Find step1 draft directories whose meta.id or {id:02d}_* prefix matches."""
    if not drafts_root.exists():
        return []
    hits: list[Path] = []
    prefix = f"{int(genre_id):02d}_"
    for p in sorted(drafts_root.iterdir()):
        if not p.is_dir():
            continue
        meta = _read_meta(p)
        if meta is not None and meta.get("id") is not None:
            try:
                if int(meta["id"]) == int(genre_id):
                    hits.append(p)
                    continue
            except (TypeError, ValueError):
                pass
        if p.name.startswith(prefix):
            hits.append(p)
    # unique preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for p in hits:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def resolve_draft_dir(
    drafts_root: Path,
    genre_id: int,
    *,
    genre: str = "",
    locale: str = "",
) -> Path:
    """Resolve exactly one draft dir for genre_id; raise FileNotFoundError if ambiguous/missing.

    When ``genre`` / ``locale`` are given (e.g. from genres.xlsx), prefer the exact
    ``{id:02d}_{genre}_{locale}`` folder or matching meta — avoids picking a stale
    draft after id reassignment (e.g. old ``02_doctor_*`` vs new ``02_group_*``).
    """
    hits = find_draft_dirs_by_id(drafts_root, genre_id)
    if not hits:
        raise FileNotFoundError(
            f"找不到 id={genre_id} 的 step1 草稿目录（在 {drafts_root} 下找 "
            f"meta.json.id 或 {{id:02d}}_* 前缀）"
        )
    g = str(genre or "").strip()
    loc = "en" if str(locale or "").lower().startswith("en") else ("zh" if locale else "")
    if g and loc:
        exact = f"{int(genre_id):02d}_{g}_{loc}"
        for p in hits:
            if p.name == exact:
                return p
        matched: list[Path] = []
        for p in hits:
            meta = draft_meta(p)
            mg = str(meta.get("genre") or "").strip()
            ml = "en" if str(meta.get("locale") or "").lower().startswith("en") else "zh"
            if mg == g and ml == loc:
                matched.append(p)
            elif mg.endswith(f"_{loc}") and mg[: -(len(loc) + 1)] == g and ml == loc:
                matched.append(p)
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            names = ", ".join(p.name for p in matched)
            raise FileNotFoundError(
                f"id={genre_id} genre={g} locale={loc} 匹配到多个草稿: {names}"
            )
        names = ", ".join(p.name for p in hits)
        raise FileNotFoundError(
            f"id={genre_id} 期望 {exact}，但只找到: {names}。"
            f"请清理旧草稿或加 --overwrite 重建。"
        )
    if len(hits) > 1:
        names = ", ".join(p.name for p in hits)
        raise FileNotFoundError(
            f"id={genre_id} 匹配到多个草稿目录，请传 genre+locale 或整理后重试: {names}"
        )
    return hits[0]


def draft_meta(draft_dir: Path) -> dict:
    meta = _read_meta(draft_dir) or {}
    if "genre" not in meta or "locale" not in meta:
        # fall back to directory name: {id:02d}_{genre}_{locale} or {genre}_{locale}
        name = draft_dir.name
        m = _DIR_ID_RE.match(name)
        rest = name[m.end():] if m else name
        if "_" in rest:
            genre, loc = rest.rsplit("_", 1)
            meta.setdefault("genre", genre)
            meta.setdefault("locale", loc)
    loc = "en" if str(meta.get("locale", "zh")).lower().startswith("en") else "zh"
    meta["locale"] = loc
    meta.setdefault("lib_slug", f"{meta.get('genre', '')}_{loc}")
    meta.setdefault("dir_name", draft_dir.name)
    if meta.get("id") is None:
        m = _DIR_ID_RE.match(draft_dir.name)
        if m:
            meta["id"] = int(m.group(1))
    return meta


def outline_dir_name(genre_id: int | None, genre: str, locale: str, theme_id: int) -> str:
    """Canonical step2/step3 slug: {id:02d}_{genre}_{locale}__theme{N}."""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    theme = f"theme{int(theme_id)}"
    if genre_id is None:
        return f"{genre}_{loc}__{theme}"
    return f"{int(genre_id):02d}_{genre}_{loc}__{theme}"


def _plan_meta(outline_dir: Path) -> dict:
    plan_path = outline_dir / "plan.json"
    if not plan_path.exists():
        return {}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    meta = data.get("meta") if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


def find_outline_dirs(
    outlines_root: Path,
    *,
    genre_id: int | None,
    genre: str,
    locale: str,
    theme_id: int,
) -> list[Path]:
    """Find outline dirs for genre/theme, preferring canonical id-prefixed name."""
    if not outlines_root.exists():
        return []
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    theme_key = f"theme{int(theme_id)}"
    suffix = f"__{theme_key}"
    canonical = outline_dir_name(genre_id, genre, loc, theme_id)
    legacy = f"{genre}_{loc}{suffix}"

    ranked: list[tuple[int, Path]] = []
    for p in sorted(outlines_root.iterdir()):
        if not p.is_dir() or not (p / "plan.json").exists():
            continue
        name = p.name
        score: int | None = None
        if name == canonical:
            score = 0
        elif name == legacy:
            score = 1
        elif name.endswith(suffix):
            # e.g. user rename five_year_plan_1_outline_zh__theme1
            body = name[: -len(suffix)]
            looks_related = genre.replace("_", "") in body.replace("_", "") or genre in body
            id_ok = True
            if genre_id is not None:
                id_ok = (
                    body.startswith(f"{int(genre_id):02d}_")
                    or f"_{int(genre_id)}_" in f"_{body}_"
                    or body.endswith(f"_{int(genre_id)}")
                )
            # confirm via plan.meta when possible
            pm = _plan_meta(p)
            meta_genre = str(pm.get("genre") or "")
            meta_theme = str(pm.get("theme_key") or "")
            meta_ok = (not meta_genre or meta_genre in (genre, f"{genre}_{loc}")) and (
                not meta_theme or meta_theme == theme_key
            )
            if looks_related and id_ok and meta_ok:
                score = 2
            elif meta_genre in (genre, f"{genre}_{loc}") and meta_theme == theme_key:
                score = 3
        if score is not None:
            ranked.append((score, p))

    ranked.sort(key=lambda x: (x[0], x[1].name))
    # unique
    seen: set[str] = set()
    out: list[Path] = []
    for _, p in ranked:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def resolve_outline_dir(
    outlines_root: Path,
    draft_dir: Path,
    theme_id: int,
    *,
    genre_id: int | None = None,
) -> Path:
    """Resolve step2 outline dir; returns canonical path (may not exist yet for writers).

    For readers: if canonical missing, fall back to legacy / fuzzy matches that exist.
    """
    meta = draft_meta(draft_dir)
    gid = genre_id if genre_id is not None else meta.get("id")
    try:
        gid_int = int(gid) if gid is not None else None
    except (TypeError, ValueError):
        gid_int = None
    genre = str(meta.get("genre") or "")
    loc = str(meta.get("locale") or "zh")
    canonical = outlines_root / outline_dir_name(gid_int, genre, loc, theme_id)
    if canonical.exists():
        return canonical
    hits = find_outline_dirs(
        outlines_root, genre_id=gid_int, genre=genre, locale=loc, theme_id=theme_id
    )
    if hits:
        return hits[0]
    return canonical
