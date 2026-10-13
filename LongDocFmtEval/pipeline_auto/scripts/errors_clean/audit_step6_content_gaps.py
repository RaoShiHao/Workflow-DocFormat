"""检测 step6 成稿中「标题连栈 / 叶标题下无正文」问题（plan + content 双层；输出较 noisy）。

推荐日常 gate 用 audit_plan_body_slots.py（仅 plan 硬规则）。

用法:
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/audit_step6_content_gaps.py --brief
  python LongDocFmtEval/pipeline_auto/scripts/errors_clean/audit_step6_content_gaps.py --ids 64
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from LongDocFmtEval.pipeline_auto.auto_paths import resolve_outline_sidecar
from LongDocFmtEval.pipeline_auto.scripts.errors_clean._paths import default_datasets_dir
from LongDocFmtEval.pipeline_auto.scripts.errors_clean.plan_body_slots import parse_topic_id
from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index
from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level
from LongDocFmtEval.pipeline_auto.seed.outline_metrics import measure_outline

_CHAPTER_RE = re.compile(r"^C(\d+)$", re.I)
_BLOCK_ID_RE = re.compile(r"^I\d+_B\d+", re.I)

_MIN_BODY_ZH = 80
_MIN_BODY_EN = 120


@dataclass
class GapHit:
    genre_id: int
    chapter: str
    topic_dir: str
    section_id: str
    kind: str
    detail: str
    block_ids: list[str] = field(default_factory=list)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_en(locale: str) -> bool:
    return str(locale or "").lower().startswith("en")


def _min_body(locale: str) -> int:
    return _MIN_BODY_EN if _is_en(locale) else _MIN_BODY_ZH


def _load_roles(doc_dir: Path) -> dict:
    tpl = doc_dir.parent / "step5_template"
    for base in (doc_dir, tpl):
        for name in ("role_params.json", "template.json"):
            p = base / name
            if p.is_file():
                data = _load_json(p)
                roles = data.get("roles") or {}
                if roles:
                    return roles
    return {}


def _load_plan(doc_dir: Path) -> dict:
    for base in (doc_dir, doc_dir.parent / "step3_outline", doc_dir.parent / "step5_template"):
        p = resolve_outline_sidecar(base, "plan.json") if base.is_dir() else base
        if isinstance(p, Path) and p.is_file():
            return _load_json(p)
        hit = base / "plan.json"
        if hit.is_file():
            return _load_json(hit)
    return {}


def _load_blocks(doc_dir: Path) -> dict:
    content_path = doc_dir / "content.json"
    blocks: dict = {}
    if content_path.is_file():
        blocks.update((_load_json(content_path).get("blocks") or {}))
    chunks_dir = doc_dir / "chunks"
    if chunks_dir.is_dir():
        for cp in sorted(chunks_dir.glob("*.json")):
            try:
                data = _load_json(cp)
            except Exception:  # noqa: BLE001
                continue
            raw = data.get("blocks") if isinstance(data.get("blocks"), dict) else data
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if _BLOCK_ID_RE.match(str(k)) and isinstance(v, dict):
                        blocks[k] = v
    return blocks


def _block_heading_level(b: dict, idx: dict) -> int | None:
    rid = str(b.get("role_id") or "")
    rec = idx.get(rid) or {}
    hl = rec.get("heading_level")
    if hl:
        return int(hl)
    dn = rec.get("display_name") or rid
    return heading_level(rid, str(dn))


def _is_body_slot(b: dict, idx: dict) -> bool:
    hint = str(b.get("length_hint") or "").lower()
    if hint in {"medium", "long"}:
        return True
    rid = str(b.get("role_id") or "")
    obj = str((idx.get(rid) or {}).get("object") or "")
    if obj in {"paragraph.body", "paragraph"} and not _block_heading_level(b, idx):
        return hint not in {"short", "table", "image", ""} or hint in {"medium", "long"}
    return False


def _text_len(val: dict | None) -> int:
    if not isinstance(val, dict):
        return 0
    return len(re.sub(r"\s+", "", str(val.get("text") or "")))


def _heading_label(b: dict) -> str:
    mp = str(b.get("main_point") or "").strip()
    if len(mp) <= 40:
        return mp or str(b.get("block_id") or "")
    return mp[:37] + "..."


def _scan_section_plan_gaps(
    blocks: list[dict],
    idx: dict,
) -> list[tuple[str, str, list[str]]]:
    hits: list[tuple[str, str, list[str]]] = []
    n = len(blocks)
    for i, b in enumerate(blocks):
        hl = _block_heading_level(b, idx)
        if not hl:
            continue
        bid = str(b.get("block_id") or "")
        has_child = False
        body_planned = 0
        j_end = n
        for j in range(i + 1, n):
            hj = _block_heading_level(blocks[j], idx)
            if hj:
                if hj > hl:
                    has_child = True
                    break
                j_end = j
                break
        else:
            j_end = n
        for j in range(i + 1, j_end):
            if _is_body_slot(blocks[j], idx):
                body_planned += 1
        if has_child:
            continue
        if body_planned == 0:
            hits.append((
                "plan_leaf_no_body",
                f"叶标题 H{hl}「{_heading_label(b)}」下未规划 medium/long 正文块",
                [bid],
            ))
        elif body_planned == 1:
            hits.append((
                "plan_leaf_one_body",
                f"叶标题 H{hl}「{_heading_label(b)}」下仅 1 个正文块（建议≥2）",
                [bid],
            ))

    run: list[str] = []
    for b in blocks:
        if _block_heading_level(b, idx):
            run.append(str(b.get("block_id") or ""))
            continue
        if _is_body_slot(b, idx) or str(b.get("length_hint") or "") == "table":
            if len(run) >= 2:
                labels = [_heading_label(x) for x in blocks if str(x.get("block_id") or "") in run]
                hits.append((
                    "plan_heading_run",
                    f"连续 {len(run)} 个标题块之间无正文/表：{' → '.join(labels[:6])}",
                    list(run),
                ))
            run = []
        else:
            if len(run) >= 2:
                labels = [_heading_label(x) for x in blocks if str(x.get("block_id") or "") in run]
                hits.append((
                    "plan_heading_run",
                    f"连续 {len(run)} 个标题块（中间仅有 short/其它）：{' → '.join(labels[:6])}",
                    list(run),
                ))
            run = []
    if len(run) >= 2:
        hits.append((
            "plan_heading_run",
            f"节末连续 {len(run)} 个标题块",
            list(run),
        ))
    return hits


def _scan_section_content_gaps(
    blocks: list[dict],
    idx: dict,
    content: dict,
    locale: str,
) -> list[tuple[str, str, list[str]]]:
    hits: list[tuple[str, str, list[str]]] = []
    min_body = _min_body(locale)
    n = len(blocks)

    for b in blocks:
        bid = str(b.get("block_id") or "")
        if not _is_body_slot(b, idx):
            continue
        nchars = _text_len(content.get(bid))
        if nchars == 0:
            hits.append((
                "content_empty_body",
                f"正文块 {bid}（{b.get('length_hint')}）无 text 或为空",
                [bid],
            ))
        elif nchars < min_body:
            hits.append((
                "content_thin_body",
                f"正文块 {bid}（{b.get('length_hint')}）仅 {nchars} 字（<{min_body}）",
                [bid],
            ))

    for i, b in enumerate(blocks):
        hl = _block_heading_level(b, idx)
        if not hl:
            continue
        bid = str(b.get("block_id") or "")
        has_child = False
        body_chars = 0
        j_end = n
        for j in range(i + 1, n):
            hj = _block_heading_level(blocks[j], idx)
            if hj:
                if hj > hl:
                    has_child = True
                    break
                j_end = j
                break
        else:
            j_end = n
        for j in range(i + 1, j_end):
            bj = blocks[j]
            if _is_body_slot(bj, idx):
                body_chars += _text_len(content.get(str(bj.get("block_id") or "")))
        if has_child:
            continue
        if body_chars < min_body:
            hits.append((
                "content_leaf_no_body",
                f"叶标题 H{hl}「{_heading_label(b)}」下无实质正文（合计 {body_chars} 字）",
                [bid],
            ))

    run_ids: list[str] = []
    run_labels: list[str] = []
    for b in blocks:
        hl = _block_heading_level(b, idx)
        bid = str(b.get("block_id") or "")
        if hl:
            run_ids.append(bid)
            run_labels.append(_heading_label(b))
            continue
        substantive = _is_body_slot(b, idx) and _text_len(content.get(bid)) >= min_body
        is_table = str(b.get("length_hint") or "") == "table" or str(b.get("table_ref") or "")
        if substantive or is_table:
            if len(run_ids) >= 2:
                hits.append((
                    "content_heading_run",
                    f"渲染序连续 {len(run_ids)} 标题无实质正文：{' → '.join(run_labels[:8])}",
                    list(run_ids),
                ))
            run_ids = []
            run_labels = []
    if len(run_ids) >= 2:
        hits.append((
            "content_heading_run",
            f"节末连续 {len(run_ids)} 标题：{' → '.join(run_labels[:8])}",
            list(run_ids),
        ))
    return hits


def audit_one(doc_dir: Path, *, genre_id: int, chapter: str, topic_dir: str) -> list[GapHit]:
    plan = _load_plan(doc_dir)
    if not plan:
        return [GapHit(genre_id, chapter, topic_dir, "?", "no_plan", f"未找到 plan.json：{doc_dir}", [])]

    idx = heading_index(plan)
    content = _load_blocks(doc_dir)
    locale = str((plan.get("meta") or {}).get("locale") or "zh")
    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}

    out: list[GapHit] = []
    metrics = measure_outline(plan)
    if int((metrics.get("leaf") or {}).get("n_leaves_eq0") or 0) > 0:
        out.append(GapHit(
            genre_id, chapter, topic_dir, "*",
            "plan_metrics",
            f"outline_metrics: {metrics['leaf']['n_leaves_eq0']} 个叶标题下 0 个正文块",
            [],
        ))

    for inst, blocks in outline_map.items():
        if not isinstance(blocks, list):
            continue
        for kind, detail, bids in _scan_section_plan_gaps(blocks, idx):
            out.append(GapHit(genre_id, chapter, topic_dir, str(inst), kind, detail, bids))
        for kind, detail, bids in _scan_section_content_gaps(blocks, idx, content, locale):
            out.append(GapHit(genre_id, chapter, topic_dir, str(inst), kind, detail, bids))
    return out


def scan_content_gaps(
    datasets_dir: Path,
    *,
    ids: set[int] | None,
) -> list[GapHit]:
    all_hits: list[GapHit] = []
    for topic in sorted(datasets_dir.iterdir()):
        if not topic.is_dir():
            continue
        gid = parse_topic_id(topic.name)
        if gid is None:
            continue
        if ids is not None and gid not in ids:
            continue
        for ch in sorted(topic.iterdir()):
            if not ch.is_dir() or not _CHAPTER_RE.match(ch.name):
                continue
            doc = ch / "step6_document"
            if not doc.is_dir():
                continue
            all_hits.extend(audit_one(doc, genre_id=gid, chapter=ch.name.upper(), topic_dir=topic.name))
    return all_hits


def main() -> None:
    ap = argparse.ArgumentParser(description="[errors_clean] 检测 step6 标题连栈 / 叶下无正文")
    ap.add_argument("--datasets-dir", default="", help="datasets 根目录")
    ap.add_argument("--doc-dir", default="", help="单个 step6_document 目录")
    ap.add_argument("--ids", default="", help="过滤体裁 id")
    ap.add_argument("--brief", action="store_true", help="只打印 summary")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    id_set: set[int] | None = None
    if args.ids.strip():
        id_set = {int(x.strip()) for x in args.ids.split(",") if x.strip()}

    if args.doc_dir.strip():
        doc = Path(args.doc_dir)
        topic = doc.parent.parent.name if doc.parent.name.startswith("C") else doc.parent.name
        gid = parse_topic_id(topic) or 0
        ch = doc.parent.name.upper() if _CHAPTER_RE.match(doc.parent.name) else "?"
        hits = audit_one(doc, genre_id=gid, chapter=ch, topic_dir=topic)
        print(f"[scan] {doc}")
    else:
        ds = Path(args.datasets_dir) if args.datasets_dir.strip() else default_datasets_dir()
        if not ds.is_dir():
            ap.error(f"datasets 不存在：{ds}")
        hits = scan_content_gaps(ds, ids=id_set)
        print(f"[scan] {ds}")
        if id_set:
            print(f"[filter] ids={sorted(id_set)}")

    if args.json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2))
        return

    from collections import Counter

    if not hits:
        print("\n[summary] 未发现标题/正文空洞问题。")
        return
    by_kind = Counter(h.kind for h in hits)
    by_sample = Counter((h.genre_id, h.chapter) for h in hits)
    print("\n[summary]")
    print(f"  命中 {len(hits)} 条，涉及 {len(by_sample)} 个 id×C")
    for kind, n in by_kind.most_common():
        print(f"  {kind}: {n}")

    if args.brief:
        return

    print(f"\n{'id':>4}  {'C':>3}  {'sec':<6}  {'kind':<22}  detail")
    print("-" * 100)
    limit = 200
    for h in hits[:limit]:
        sec = h.section_id[:6]
        detail = h.detail[:70] + ("..." if len(h.detail) > 70 else "")
        print(f"{h.genre_id:>4}  {h.chapter:>3}  {sec:<6}  {h.kind:<22}  {detail}")
    if len(hits) > limit:
        print(f"... 还有 {len(hits) - limit} 条")


if __name__ == "__main__":
    main()
