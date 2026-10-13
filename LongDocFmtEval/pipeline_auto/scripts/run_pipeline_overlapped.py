"""pipeline_auto · overlapped batch runner (LLM ∥ officecli).

Does **not** replace ``run_pipeline_auto.py``.

Scheduling unit = **sample** ``{topic_id}_{Cx}`` (plus per-topic bootstrap).

Global queues (defaults: 1 worker each):

  * **LLM**: bootstrap(step1–2); sample step3→4→step5 specimen/assets→step6 content
  * **Officecli**: step5 ``template.docx`` → step6 ``document.docx`` → optional step7

Sample dependency chain (render waits for its LLM inputs)::

  bootstrap → 3 → 4 → 5-LLM → 5-officecli → 6-LLM → 6-officecli → [7]

Cross-topic: while one sample's officecli runs, another sample (incl. next topic)
may run LLM. Bootstrap jobs have lower priority than in-flight sample LLM work.
Topic ``pipeline_report`` is written when that topic's samples reach ``until_step``.

Usage::

  python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_overlapped.py --ids 1,12 --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_pipeline_overlapped.py --id 12 --complexity C2
"""
from __future__ import annotations

import argparse
import itertools
import queue
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg  # noqa: E402
from LongDocFmtEval.common.themes_xlsx import COMPLEXITY_TIERS, _norm_complexity  # noqa: E402
from LongDocFmtEval.pipeline_auto.auto_paths import (  # noqa: E402
    DATASETS_DIR,
    DEFAULT_CHAPTER_WORKERS,
    DEFAULT_DOCUMENT_COUNT,
    DEFAULT_OUTLINE_COUNT,
    DEFAULT_RUN_THEME_COUNT,
    DEFAULT_THEME_COUNT,
)
from LongDocFmtEval.pipeline_auto.scripts import run_pipeline_auto as main_pipe  # noqa: E402
from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: E402
    document_dir,
    ensure_topic_scaffold,
    pairs_dir,
    resolve_topic_dir,
    role_dir,
    sample_id,
    styles_dir,
    template_dir,
    themes_xlsx_path,
    topic_dir_name,
    write_json,
)

_SENTINEL = object()

# PriorityQueue: lower tuple sorts first → samples before bootstraps
_PRIO_SAMPLE = 0
_PRIO_BOOTSTRAP = 1
_CX_ORDER = {"C1": 0, "C2": 1, "C3": 2}


class _Pending:
    """Count in-flight queue items so main can wait despite dynamic enqueue."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()
        self._done = threading.Condition(self._lock)

    def add(self, k: int = 1) -> None:
        with self._done:
            self._n += k
            self._done.notify_all()

    def done(self, k: int = 1) -> None:
        with self._done:
            self._n = max(0, self._n - k)
            self._done.notify_all()

    def wait_zero(self) -> None:
        with self._done:
            while self._n > 0:
                self._done.wait(timeout=0.5)

    @property
    def n(self) -> int:
        with self._done:
            return self._n


class _PrioQueue:
    """Thin PriorityQueue wrapper with (priority, topic_id, cx_ord, seq, item)."""

    def __init__(self) -> None:
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()

    def put(self, item: Any, *, prio: int, topic_id: int = 0, complexity: str = "") -> None:
        cx_ord = _CX_ORDER.get(complexity, 9)
        self._q.put((prio, int(topic_id), cx_ord, next(self._seq), item))

    def get(self, timeout: float | None = None) -> Any:
        _prio, _tid, _cx, _seq, item = self._q.get(timeout=timeout)
        return item

    def task_done(self) -> None:
        self._q.task_done()


@dataclass
class BootstrapJob:
    """Topic-level step0–2; then enqueues SampleLlmJobs (stage from_3)."""
    genre_row: dict
    theme_count: int
    complexities: list[str] | None
    overwrite: bool
    figures: bool
    skip_step1: bool
    until_step: int
    chapter_workers: int = DEFAULT_CHAPTER_WORKERS
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"bootstrap:{int(self.genre_row['id'])}"


@dataclass
class SampleLlmJob:
    """LLM segment for one sample: ``from_3`` (3→4→5-LLM) or ``step6_content``."""
    topic: Path
    topic_id: int
    genre_row: dict
    theme_row: dict
    complexity: str
    role_path: Path
    overwrite: bool
    figures: bool
    until_step: int
    stage: str = "from_3"  # from_3 | step6_content
    chapter_workers: int = DEFAULT_CHAPTER_WORKERS
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.topic_id}_{self.complexity}"


@dataclass
class OfficeJob:
    """Officecli segment: step5 template / step6 document (+ optional step7)."""
    sample: SampleLlmJob
    kind: str  # step5 | step6
    styles: Path | None = None
    tpl: Path | None = None
    doc: Path | None = None


@dataclass
class TopicState:
    topic: Path
    topic_id: int
    genre_row: dict
    meta: dict[str, Any]
    run_cx: list[str]
    stats: "RunStats" = field(default_factory=lambda: RunStats())
    role_path: Path | None = None
    stopped_at: int = 6
    aborted: bool = False
    sample_labels: list[str] = field(default_factory=list)
    samples_done: set[str] = field(default_factory=set)
    finalized: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RunStats:
    themes: int = 0
    outlines: int = 0
    templates: int = 0
    documents: int = 0
    pairs: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_error(self, sample: str, step: int, reason: str) -> None:
        with self.lock:
            self.errors.append({"sample": sample, "step": step, "reason": reason})

    def bump(self, key: str, n: int = 1) -> None:
        with self.lock:
            setattr(self, key, int(getattr(self, key)) + n)

    def as_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "themes": self.themes,
                "outlines": self.outlines,
                "templates": self.templates,
                "documents": self.documents,
                "pairs": self.pairs,
                "errors": list(self.errors),
            }


def _log(msg: str) -> None:
    print(msg, flush=True)


def _enqueue_llm(
    llm_q: _PrioQueue,
    llm_pending: _Pending,
    job: BootstrapJob | SampleLlmJob,
) -> None:
    llm_pending.add(1)
    if isinstance(job, BootstrapJob):
        tid = int(job.genre_row["id"])
        llm_q.put(job, prio=_PRIO_BOOTSTRAP, topic_id=tid)
    else:
        llm_q.put(
            job,
            prio=_PRIO_SAMPLE,
            topic_id=job.topic_id,
            complexity=job.complexity,
        )


def _enqueue_office(
    office_q: queue.Queue,
    office_pending: _Pending,
    job: OfficeJob,
) -> None:
    office_pending.add(1)
    office_q.put(job)


def _finalize_topic(state: TopicState) -> dict[str, Any]:
    with state.lock:
        if state.finalized:
            return {
                "topic": str(state.topic),
                "stats": state.stats.as_dict(),
                "stopped_at": -1 if state.aborted else state.stopped_at,
                "aborted": state.aborted,
                "remediation": None,
            }
        state.finalized = True
        abort = state.aborted
        stopped = -1 if abort else state.stopped_at
        stats_d = state.stats.as_dict()

    write_json(
        state.topic / "pipeline_report.json",
        {
            "meta": state.meta,
            "stats": stats_d,
            "stopped_at": stopped,
            "aborted": abort,
            "errors": stats_d.get("errors") or [],
            "llm_usage": main_pipe._aggregate_topic_llm_usage(state.topic),
            "mode": "overlapped_llm_officecli",
        },
    )
    if stats_d.get("errors"):
        write_json(
            state.topic / "errors.json",
            {"errors": stats_d["errors"], "aborted": abort},
        )
    remediation = None
    try:
        from LongDocFmtEval.pipeline_auto.seed.pipeline_remediation import (  # noqa: PLC0415
            write_topic_remediation,
        )
        remediation = write_topic_remediation(
            state.topic,
            complexities=tuple(state.run_cx) if state.run_cx else None,
        )
        counts = remediation.get("counts") or {}
        _log(
            f"[remediation] {state.topic.name} → "
            f"{DATASETS_DIR / '_batch_logs' / 'summary.json'} "
            f"manual={counts.get('manual_role_params', 0)} "
            f"retry={counts.get('retry', 0)} "
            f"delete={counts.get('delete_and_retry', 0)}"
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"[warn] remediation 报告失败: {exc}")
    _log(
        f"\n[topic done] {state.topic.name} stats={stats_d} "
        f"stopped_at={stopped} aborted={abort}"
    )
    return {
        "topic": str(state.topic),
        "stats": stats_d,
        "stopped_at": stopped,
        "aborted": abort,
        "remediation": remediation,
    }


def _maybe_finish_sample(
    state: TopicState | None,
    label: str,
    *,
    abort_topic: bool = False,
) -> None:
    if state is None:
        return
    with state.lock:
        if abort_topic:
            state.aborted = True
        state.samples_done.add(label)
        labels = list(state.sample_labels)
        done = set(state.samples_done)
        ready = (not labels) or labels_subset_done(labels, done)
    if ready:
        _finalize_topic(state)


def labels_subset_done(labels: list[str], done: set[str]) -> bool:
    return all(x in done for x in labels)


def _run_bootstrap(
    job: BootstrapJob,
    *,
    llm_q: _PrioQueue,
    llm_pending: _Pending,
    topic_states: dict[int, TopicState],
    states_lock: threading.Lock,
    abort_event: threading.Event,
    llm_workers: int,
    office_workers: int,
) -> None:
    genre_row = job.genre_row
    topic_id = int(genre_row["id"])
    theme_count = max(3, int(job.theme_count or DEFAULT_THEME_COUNT))
    until_step = job.until_step
    overwrite = job.overwrite
    skip_step1 = job.skip_step1
    run_cx = job.complexities or list(COMPLEXITY_TIERS)

    _log(
        f"\n######## BOOTSTRAP {topic_id} {genre_row['genre']} → "
        f"{topic_dir_name(topic_id, display_name=genre_row.get('display_name') or '', locale=genre_row['locale'], genre=genre_row['genre'])} "
        f"[llm∥officecli llm={llm_workers} office={office_workers}] ########"
    )
    _log(f"[until] 跑到 step{until_step} 为止（含）")

    topic = resolve_topic_dir(
        topic_id,
        genre=genre_row["genre"],
        locale=genre_row["locale"],
        display_name=genre_row.get("display_name") or "",
    )
    ensure_topic_scaffold(topic)
    meta = {
        "id": topic_id,
        "genre": genre_row["genre"],
        "locale": genre_row["locale"],
        "display_name": genre_row["display_name"],
        "notes": genre_row.get("notes") or "",
        "domain_id": genre_row.get("domain_id") or "",
        "theme_count": theme_count,
        "complexities": run_cx,
        "outline_count": 1,
        "document_count": 1,
        "until_step": until_step,
        "track": "pipeline_auto_overlapped",
        "layout": "datasets/{id}_{lang}_{topic_name}/step1_role+step2_themes+C*/step3..7",
        "overlap": {
            "mode": "llm_vs_officecli",
            "llm_workers": llm_workers,
            "office_workers": office_workers,
        },
        "no_seed": True,
    }
    main_pipe._bootstrap_step0(topic, meta, overwrite=overwrite and not skip_step1)
    write_json(topic / "step0" / "meta.json", meta)

    state = TopicState(
        topic=topic,
        topic_id=topic_id,
        genre_row=genre_row,
        meta=meta,
        run_cx=list(run_cx),
        stopped_at=until_step,
    )
    with states_lock:
        topic_states[topic_id] = state

    if until_step <= 0:
        _finalize_topic(state)
        return

    role_path = role_dir(topic)
    if skip_step1 and (role_path / "draft.yaml").is_file():
        _log(f"[LLM][bootstrap {topic_id}] step1 reuse {role_path}")
    else:
        draft = main_pipe._run_step1(genre_row, overwrite=overwrite)
        role_path = main_pipe._sync_role_into_topic(draft, topic)
        _log(f"[LLM][bootstrap {topic_id}] step1 → {role_path}")
    state.role_path = role_path

    if until_step <= 1:
        _finalize_topic(state)
        return

    need_themes = (overwrite and not skip_step1) or not themes_xlsx_path(topic).is_file()
    if need_themes:
        main_pipe._run_step2_themes(
            topic, role_path, theme_count=theme_count, overwrite=overwrite,
        )
    else:
        _log(f"[LLM][bootstrap {topic_id}] step2 reuse {themes_xlsx_path(topic)}")
    _log(f"[LLM][bootstrap {topic_id}] step2 → {themes_xlsx_path(topic)}")

    if until_step <= 2:
        state.stats.bump("themes", theme_count)
        _finalize_topic(state)
        return

    theme_rows = main_pipe._read_theme_rows(topic, complexities=run_cx)
    if not theme_rows:
        state.stats.add_error(f"{topic_id}", 2, "themes.xlsx 无可用 complexity 主题")
        state.aborted = True
        _log(f"[LLM][bootstrap {topic_id}] fail: no theme rows")
        _finalize_topic(state)
        return

    state.stats.bump("themes", len(theme_rows))
    samples: list[SampleLlmJob] = []
    for trow in theme_rows:
        cx = _norm_complexity(trow.get("complexity")) or ""
        if cx not in COMPLEXITY_TIERS:
            _log(f"[skip] 无效 complexity: {trow.get('complexity')}")
            continue
        samples.append(SampleLlmJob(
            topic=topic,
            topic_id=topic_id,
            genre_row=genre_row,
            theme_row=trow,
            complexity=cx,
            role_path=role_path,
            overwrite=overwrite,
            figures=job.figures,
            until_step=until_step,
            stage="from_3",
            chapter_workers=job.chapter_workers,
        ))
    state.sample_labels = [s.label for s in samples]
    _log(
        f"[LLM][bootstrap {topic_id}] enqueue samples "
        f"{[s.complexity for s in samples]} (prio > later bootstraps)"
    )

    if abort_event.is_set():
        state.aborted = True
        _finalize_topic(state)
        return

    if not samples:
        _finalize_topic(state)
        return

    for s in samples:
        _enqueue_llm(llm_q, llm_pending, s)


def _run_sample_llm_from_3(
    job: SampleLlmJob,
    stats: RunStats,
    *,
    office_q: queue.Queue,
    office_pending: _Pending,
) -> str:
    """Run step3→4→5-LLM. Returns: done | fail | enqueued_office5 | continue_step6."""
    from LongDocFmtEval.pipeline_auto.scripts import run_step5  # noqa: PLC0415

    cx = job.complexity
    until = job.until_step
    label = job.label
    _log(f"\n[LLM] ==== sample {label} (from_3): {str(job.theme_row.get('theme') or '')[:48]} ====")

    od = main_pipe._run_outline_for_complexity(
        topic=job.topic,
        role_library_dir=job.role_path,
        genre_row=job.genre_row,
        theme_row=job.theme_row,
        complexity=cx,
        overwrite=job.overwrite,
    )
    if od is None:
        stats.add_error(label, 3, "outline 失败或无效（见 C*/outline/error.json）")
        _log(f"[LLM][fail] {label} step3")
        return "fail"
    stats.bump("outlines")
    if until <= 3:
        _log(f"[LLM] {label} stop at step3")
        return "done"

    st = styles_dir(job.topic, complexity=cx)
    _log(f"[LLM] -- step4 {st.relative_to(job.topic)} --")
    if not main_pipe._run_step4_styles(od, st, overwrite=job.overwrite):
        stats.add_error(label, 4, "styles 失败")
        _log(f"[LLM][fail] {label} step4")
        return "fail"
    stats.bump("templates")
    if until <= 4:
        _log(f"[LLM] {label} stop at step4")
        return "done"

    tpl = template_dir(job.topic, complexity=cx)
    if not job.overwrite and run_step5.template_docx_ready(tpl):
        _log(f"[LLM] {label} step5 template.docx cache → skip 5-LLM/5-officecli")
        if until <= 5:
            return "done"
        return "continue_step6"

    _log(f"[LLM] -- step5 specimen {tpl.relative_to(job.topic)} --")
    ok = run_step5.prepare_specimen_assets(
        tpl,
        job.figures,
        styles_dir=st,
        figures_overwrite=bool(job.overwrite),
        overwrite_specimen=bool(job.overwrite),
        chapter_workers=job.chapter_workers,
    )
    if not ok:
        stats.add_error(label, 5, "template 失败（specimen LLM）")
        _log(f"[LLM][fail] {label} step5 specimen")
        return "fail"

    _log(f"[LLM] {label} specimen ready → enqueue officecli step5")
    _enqueue_office(
        office_q,
        office_pending,
        OfficeJob(sample=job, kind="step5", styles=st, tpl=tpl),
    )
    return "enqueued_office5"


def _run_sample_llm_step6(
    job: SampleLlmJob,
    stats: RunStats,
    *,
    office_q: queue.Queue,
    office_pending: _Pending,
) -> str:
    """Write step6 content; enqueue officecli render (or step7-only if docx ready)."""
    from LongDocFmtEval.pipeline_auto.scripts import run_step6  # noqa: PLC0415

    cx = job.complexity
    until = job.until_step
    label = job.label
    tpl = template_dir(job.topic, complexity=cx)
    doc = document_dir(job.topic, complexity=cx)
    sid = sample_id(job.topic_id, cx)
    _log(f"\n[LLM] ==== sample {label} (step6 content) ====")

    if not job.overwrite and run_step6._document_docx_ready(doc):
        stats.bump("documents")
        write_json(doc / "sample_id.json", {"sample_id": sid, "complexity": cx})
        if until < 7:
            _log(f"[LLM] {label} document.docx cache → skip officecli")
            return "done"
        _log(f"[LLM] {label} document.docx cache → enqueue step7 via officecli")
        _enqueue_office(
            office_q,
            office_pending,
            OfficeJob(sample=job, kind="step6", tpl=tpl, doc=doc),
        )
        return "enqueued_office6"

    _log(f"[LLM] -- step6 content {sid} (no_render) --")
    ok = run_step6.run_one(
        tpl,
        out_dir=doc,
        overwrite=job.overwrite,
        resume=False,
        temperature=0.7,
        max_tokens=16384,
        no_render=True,
        figures=job.figures,
        rerender=False,
        chapter_workers=job.chapter_workers,
    )
    if not ok:
        stats.add_error(label, 6, "document 失败（写作阶段）")
        _log(f"[LLM][fail] {label} step6 content")
        return "fail"

    write_json(doc / "sample_id.json", {"sample_id": sid, "complexity": cx})
    content = doc / "content.json"
    if not content.is_file():
        stats.add_error(label, 6, "document 失败（无 content.json）")
        return "fail"

    _log(f"[LLM] {label} content ready → enqueue officecli step6")
    _enqueue_office(
        office_q,
        office_pending,
        OfficeJob(sample=job, kind="step6", tpl=tpl, doc=doc),
    )
    return "enqueued_office6"


def _run_office_step5(job: OfficeJob, stats: RunStats) -> str:
    from LongDocFmtEval.pipeline_auto.scripts import run_step5  # noqa: PLC0415

    sample = job.sample
    label = sample.label
    tpl = job.tpl or template_dir(sample.topic, complexity=sample.complexity)
    st = job.styles or styles_dir(sample.topic, complexity=sample.complexity)
    _log(f"\n[OFFICE] ==== sample {label} step5 template.docx ====")

    if not sample.overwrite and run_step5.template_docx_ready(tpl):
        _log(f"[OFFICE] {label} template.docx cache")
    else:
        ok = run_step5.render_template_docx_only(
            tpl,
            sample.figures,
            styles_dir=st,
            figures_overwrite=False,
        )
        if not ok:
            stats.add_error(label, 5, "template 失败（officecli 渲染）")
            _log(f"[OFFICE][fail] {label} step5 render")
            return "fail"
        _log(f"[OFFICE] {label} template.docx ready")

    if sample.until_step <= 5:
        return "done"
    return "continue_step6"


def _run_office_step6(job: OfficeJob, stats: RunStats) -> str:
    from LongDocFmtEval.pipeline_auto.scripts import run_step6  # noqa: PLC0415

    sample = job.sample
    label = sample.label
    until = sample.until_step
    tpl = job.tpl or template_dir(sample.topic, complexity=sample.complexity)
    doc = job.doc or document_dir(sample.topic, complexity=sample.complexity)
    docx_path = doc / "document.docx"
    _log(f"\n[OFFICE] ==== sample {label} step6 document.docx ====")

    already = (not sample.overwrite) and run_step6._document_docx_ready(doc)
    if already:
        _log(f"[OFFICE] {label} document.docx cache")
    else:
        if docx_path.is_file() and not sample.overwrite:
            _log(f"[OFFICE] {label} document.docx not ready → re-render")
        _log(f"[OFFICE] {label} render document.docx")
        ok = run_step6.run_one(
            tpl,
            out_dir=doc,
            overwrite=False,
            resume=False,
            temperature=0.7,
            max_tokens=16384,
            no_render=False,
            figures=sample.figures,
            rerender=True,
        )
        if not ok or not run_step6._document_docx_ready(doc):
            stats.add_error(label, 6, "document 失败（渲染阶段）")
            _log(f"[OFFICE][fail] {label} step6 render")
            return "fail"
        stats.bump("documents")
        _log(f"[OFFICE] {label} document.docx ready")

    if until < 7:
        return "done"

    pair = pairs_dir(sample.topic, complexity=sample.complexity)
    _log(f"[OFFICE] -- step7 {pair.relative_to(sample.topic)} --")
    if main_pipe._run_step7_pairs(doc, pair, overwrite=sample.overwrite):
        stats.bump("pairs")
        return "done"
    stats.add_error(label, 7, "pairs 失败")
    _log(f"[OFFICE][fail] {label} step7")
    return "fail"


def _llm_worker(
    llm_q: _PrioQueue,
    llm_pending: _Pending,
    office_q: queue.Queue,
    office_pending: _Pending,
    topic_states: dict[int, TopicState],
    states_lock: threading.Lock,
    abort_event: threading.Event,
    continue_on_error: bool,
    llm_workers: int,
    office_workers: int,
) -> None:
    while True:
        try:
            item = llm_q.get(timeout=0.5)
        except queue.Empty:
            if abort_event.is_set():
                break
            continue
        if item is _SENTINEL:
            llm_q.task_done()
            break
        try:
            if abort_event.is_set() and not isinstance(item, BootstrapJob):
                # Still drain bootstrap finalization paths lightly
                if isinstance(item, SampleLlmJob):
                    with states_lock:
                        state = topic_states.get(item.topic_id)
                    _maybe_finish_sample(state, item.label, abort_topic=True)
                continue

            if isinstance(item, BootstrapJob):
                try:
                    _run_bootstrap(
                        item,
                        llm_q=llm_q,
                        llm_pending=llm_pending,
                        topic_states=topic_states,
                        states_lock=states_lock,
                        abort_event=abort_event,
                        llm_workers=llm_workers,
                        office_workers=office_workers,
                    )
                except Exception as exc:  # noqa: BLE001
                    tid = int(item.genre_row.get("id") or 0)
                    _log(f"[LLM][FAIL] {item.label}: {exc}\n{traceback.format_exc()[-500:]}")
                    with states_lock:
                        state = topic_states.get(tid)
                    if state:
                        state.stats.add_error(f"{tid}", 0, f"bootstrap 异常: {exc}"[:200])
                        state.aborted = True
                        _finalize_topic(state)
                    if not continue_on_error:
                        abort_event.set()
            elif isinstance(item, SampleLlmJob):
                with states_lock:
                    state = topic_states.get(item.topic_id)
                stats = state.stats if state else RunStats()
                try:
                    if item.stage == "step6_content":
                        result = _run_sample_llm_step6(
                            item, stats, office_q=office_q, office_pending=office_pending,
                        )
                    else:
                        result = _run_sample_llm_from_3(
                            item, stats, office_q=office_q, office_pending=office_pending,
                        )
                        if result == "continue_step6":
                            result = _run_sample_llm_step6(
                                item, stats, office_q=office_q, office_pending=office_pending,
                            )

                    if result == "fail":
                        if not continue_on_error:
                            abort_event.set()
                            _maybe_finish_sample(state, item.label, abort_topic=True)
                        else:
                            _maybe_finish_sample(state, item.label)
                    elif result == "done":
                        _maybe_finish_sample(state, item.label)
                    # enqueued_office* → sample completion deferred to office worker
                except Exception as exc:  # noqa: BLE001
                    stats.add_error(item.label, 0, f"LLM 阶段异常: {exc}"[:200])
                    _log(f"[LLM][FAIL] {item.label}: {exc}\n{traceback.format_exc()[-500:]}")
                    if not continue_on_error:
                        abort_event.set()
                        _maybe_finish_sample(state, item.label, abort_topic=True)
                    else:
                        _maybe_finish_sample(state, item.label)
            else:
                _log(f"[LLM][warn] unknown job type: {type(item)}")
        finally:
            llm_pending.done()
            llm_q.task_done()


def _office_worker(
    office_q: queue.Queue,
    office_pending: _Pending,
    llm_q: _PrioQueue,
    llm_pending: _Pending,
    topic_states: dict[int, TopicState],
    states_lock: threading.Lock,
    abort_event: threading.Event,
    continue_on_error: bool,
) -> None:
    while True:
        try:
            item = office_q.get(timeout=0.5)
        except queue.Empty:
            if abort_event.is_set():
                break
            continue
        if item is _SENTINEL:
            office_q.task_done()
            break
        assert isinstance(item, OfficeJob)
        sample = item.sample
        tid = sample.topic_id
        try:
            with states_lock:
                state = topic_states.get(tid)
            stats = state.stats if state else RunStats()
            if abort_event.is_set():
                _maybe_finish_sample(state, sample.label, abort_topic=True)
                continue

            if item.kind == "step5":
                result = _run_office_step5(item, stats)
                if result == "fail":
                    if not continue_on_error:
                        abort_event.set()
                        _maybe_finish_sample(state, sample.label, abort_topic=True)
                    else:
                        _maybe_finish_sample(state, sample.label)
                elif result == "done":
                    _maybe_finish_sample(state, sample.label)
                elif result == "continue_step6":
                    nxt = SampleLlmJob(
                        topic=sample.topic,
                        topic_id=sample.topic_id,
                        genre_row=sample.genre_row,
                        theme_row=sample.theme_row,
                        complexity=sample.complexity,
                        role_path=sample.role_path,
                        overwrite=sample.overwrite,
                        figures=sample.figures,
                        until_step=sample.until_step,
                        stage="step6_content",
                        chapter_workers=sample.chapter_workers,
                        label=sample.label,
                    )
                    _enqueue_llm(llm_q, llm_pending, nxt)
            else:
                result = _run_office_step6(item, stats)
                if result == "fail":
                    if not continue_on_error:
                        abort_event.set()
                        _maybe_finish_sample(state, sample.label, abort_topic=True)
                    else:
                        _maybe_finish_sample(state, sample.label)
                else:
                    _maybe_finish_sample(state, sample.label)
        except Exception as exc:  # noqa: BLE001
            with states_lock:
                state = topic_states.get(tid)
            if state:
                state.stats.add_error(
                    sample.label, 6, f"officecli 异常: {exc}"[:200],
                )
            _log(f"[OFFICE][FAIL] {sample.label}: {exc}")
            if not continue_on_error:
                abort_event.set()
                _maybe_finish_sample(state, sample.label, abort_topic=True)
            else:
                _maybe_finish_sample(state, sample.label)
        finally:
            office_pending.done()
            office_q.task_done()


def run_batch_overlapped(
    genre_rows: list[dict],
    *,
    theme_count: int,
    complexities: list[str] | None,
    overwrite: bool,
    figures: bool,
    skip_step1: bool,
    until_step: int = main_pipe.DEFAULT_UNTIL_STEP,
    continue_on_error: bool = True,
    llm_workers: int = 1,
    docx_workers: int = 1,
    chapter_workers: int = DEFAULT_CHAPTER_WORKERS,
) -> list[dict[str, Any]]:
    """Run many topics with global LLM ∥ officecli overlap."""
    llm_workers = max(1, int(llm_workers))
    office_workers = max(1, int(docx_workers))
    chapter_workers = max(1, int(chapter_workers or DEFAULT_CHAPTER_WORKERS))
    theme_count = max(3, int(theme_count or DEFAULT_THEME_COUNT))

    _log(
        f"\n[batch overlapped] topics={[int(g['id']) for g in genre_rows]} "
        f"mode=llm_vs_officecli llm_workers={llm_workers} office_workers={office_workers} "
        f"chapter_workers={chapter_workers} until_step={until_step}"
    )

    llm_q = _PrioQueue()
    office_q: queue.Queue = queue.Queue()
    llm_pending = _Pending()
    office_pending = _Pending()
    abort_event = threading.Event()
    topic_states: dict[int, TopicState] = {}
    states_lock = threading.Lock()

    office_threads = [
        threading.Thread(
            target=_office_worker,
            args=(
                office_q, office_pending, llm_q, llm_pending,
                topic_states, states_lock, abort_event, continue_on_error,
            ),
            name=f"office-{i}",
            daemon=True,
        )
        for i in range(office_workers)
    ]
    llm_threads = [
        threading.Thread(
            target=_llm_worker,
            args=(
                llm_q, llm_pending, office_q, office_pending,
                topic_states, states_lock, abort_event, continue_on_error,
                llm_workers, office_workers,
            ),
            name=f"llm-{i}",
            daemon=True,
        )
        for i in range(llm_workers)
    ]
    for t in office_threads + llm_threads:
        t.start()

    for g in genre_rows:
        if abort_event.is_set():
            break
        _enqueue_llm(
            llm_q,
            llm_pending,
            BootstrapJob(
                genre_row=g,
                theme_count=theme_count,
                complexities=complexities,
                overwrite=overwrite,
                figures=figures,
                skip_step1=skip_step1,
                until_step=until_step,
                chapter_workers=chapter_workers,
            ),
        )

    # Quiesce both queues: officecli may re-enqueue LLM (e.g. after step5 render).
    while True:
        llm_pending.wait_zero()
        office_pending.wait_zero()
        if llm_pending.n == 0 and office_pending.n == 0:
            break

    for _ in range(llm_workers):
        llm_q.put(_SENTINEL, prio=99, topic_id=10**9)
    for t in llm_threads:
        t.join(timeout=30)

    for _ in range(office_workers):
        office_q.put(_SENTINEL)
    for t in office_threads:
        t.join(timeout=30)

    # Any topic that never finalized (e.g. abort mid-flight): finalize now
    results: list[dict[str, Any]] = []
    with states_lock:
        states = list(topic_states.values())
    for state in sorted(states, key=lambda s: s.topic_id):
        results.append(_finalize_topic(state))
    return results


def run_topic_overlapped(genre_row: dict, **kwargs: Any) -> dict[str, Any]:
    rows = run_batch_overlapped([genre_row], **kwargs)
    return rows[0] if rows else {"topic": "", "stats": {}, "stopped_at": -1, "aborted": True}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "pipeline_auto overlapped：全局 LLM∥officecli 队列；"
            "step5/6 拆成生成后再渲染；样本 LLM 优先于后续 topic bootstrap。"
        )
    )
    ap.add_argument("--all", action="store_true", help="对 genres.xlsx 全部 enabled 体裁执行")
    ap.add_argument("--id", dest="topic_id", type=int, default=None, help="指定 topic/体裁 id")
    ap.add_argument("--ids", default="", help="多个 topic id，如 1,26 或 1-3")
    ap.add_argument(
        "--complexity", "--c",
        dest="complexity",
        default="",
        help="只跑指定复杂度档，如 C2 或 C1,C3（默认 C1,C2,C3）",
    )
    ap.add_argument("--themes", type=int, default=DEFAULT_THEME_COUNT, help="step2 主题预算行数")
    ap.add_argument("--overwrite", action="store_true", help="强制重跑已有产物")
    ap.add_argument(
        "--skip-step1", action="store_true",
        help="保留已有 step1；若 themes.xlsx 已在亦保留 step2",
    )
    ap.add_argument("--no-figures", action="store_true", help="step5/6 不出图")
    ap.add_argument(
        "--until-step",
        type=int,
        default=None,
        metavar="N",
        choices=list(main_pipe.PIPELINE_STEPS),
        help=f"跑到第 N 步（含）。默认 {main_pipe.DEFAULT_UNTIL_STEP}",
    )
    ap.add_argument("--step7", action="store_true", help="未指定 --until-step 时跑到 step7")
    ap.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="某样本失败时继续其它样本（默认开启）",
    )
    ap.add_argument(
        "--llm-workers", type=int, default=1,
        help="全局 LLM 队列并发（默认 1）",
    )
    ap.add_argument(
        "--docx-workers", type=int, default=1,
        help="全局 officecli 队列并发（默认 1；Windows/Word 建议保持 1）",
    )
    ap.add_argument(
        "--chapter-workers", type=int, default=DEFAULT_CHAPTER_WORKERS,
        help=f"step5 specimen / step6 分章写作 LLM 并发（默认 {DEFAULT_CHAPTER_WORKERS}）",
    )
    ap.add_argument(
        "--scan-remediation",
        action="store_true",
        help="不跑生成：扫描未完成样本，写出 datasets/_batch_logs/{id}_{Cx}.json 与 summary.json",
    )
    ap.add_argument("--run-themes", type=int, default=DEFAULT_RUN_THEME_COUNT, help=argparse.SUPPRESS)
    ap.add_argument("--outlines", type=int, default=DEFAULT_OUTLINE_COUNT, help=argparse.SUPPRESS)
    ap.add_argument("--documents", type=int, default=DEFAULT_DOCUMENT_COUNT, help=argparse.SUPPRESS)
    args = ap.parse_args()

    try:
        until_step = main_pipe._normalize_until_step(args.until_step, step7=bool(args.step7))
        complexities = main_pipe.parse_complexity_arg(args.complexity)
    except ValueError as exc:
        ap.error(str(exc))

    genres = main_pipe._load_genres()
    by_id = {int(g["id"]): g for g in genres}
    if args.all:
        selected = genres
    else:
        ids = parse_ids_arg(args.ids) if args.ids else (
            [args.topic_id] if args.topic_id is not None else []
        )
        if not ids:
            ap.error("请指定 --all 或 --id / --ids")
        missing = [i for i in ids if i not in by_id]
        if missing:
            ap.error(f"genres.xlsx 中无 id={missing}")
        selected = [by_id[i] for i in ids]

    cx_label = ",".join(complexities) if complexities else "C1,C2,C3"
    mode = "scan-remediation" if args.scan_remediation else "overlapped-llm-officecli"
    _log(
        f"[pipeline_overlapped] mode={mode} topics={[g['id'] for g in selected]} "
        f"complexity={cx_label} until_step={until_step} "
        f"llm_workers={args.llm_workers} office_workers={args.docx_workers} "
        f"chapter_workers={args.chapter_workers}"
    )
    _log(f"[datasets] {DATASETS_DIR.resolve()}")

    from LongDocFmtEval.pipeline_auto.seed.pipeline_remediation import (  # noqa: PLC0415
        scan_topics_and_write,
        write_batch_remediation,
    )

    if args.scan_remediation:
        topic_paths: list[Path] = []
        for g in selected:
            topic = resolve_topic_dir(
                int(g["id"]),
                genre=str(g.get("genre") or ""),
                locale=str(g.get("locale") or "zh"),
                display_name=str(g.get("display_name") or ""),
            )
            if not topic.is_dir():
                _log(f"[skip] topic 目录不存在: {topic}")
                continue
            topic_paths.append(topic)
        rem = scan_topics_and_write(
            topic_paths,
            complexities=tuple(complexities) if complexities else None,
            label=f"ids={[g['id'] for g in selected]}",
        )
        counts = rem.get("counts") or {}
        _log(
            f"[remediation] → {DATASETS_DIR / '_batch_logs' / 'summary.json'} "
            f"manual={counts.get('manual_role_params', 0)} "
            f"retry={counts.get('retry', 0)} "
            f"delete={counts.get('delete_and_retry', 0)}"
        )
        _log(f"\n完成扫描 {len(topic_paths)}/{len(selected)} 个 topic")
        return

    results = run_batch_overlapped(
        selected,
        theme_count=int(args.themes),
        complexities=complexities,
        overwrite=bool(args.overwrite),
        figures=not args.no_figures,
        skip_step1=bool(args.skip_step1),
        until_step=until_step,
        continue_on_error=bool(args.continue_on_error),
        llm_workers=int(args.llm_workers),
        docx_workers=int(args.docx_workers),
        chapter_workers=max(1, int(args.chapter_workers)),
    )
    batch_path = write_batch_remediation(
        out_dir=DATASETS_DIR / "_batch_logs",
        label=f"overlapped ids={[g['id'] for g in selected]}",
    )
    _log(f"[remediation] → {batch_path}")
    ok = sum(1 for r in results if not r.get("aborted"))
    _log(f"\n完成 {ok}/{len(selected)} 个 topic（LLM∥officecli）")


# if __name__ == "__main__":
#     main()

if __name__ == "__main__":
    import sys
    # 模拟命令行参数 - 在这里修改 ID 列表
    sys.argv = [
        "run_pipeline_overlapped.py",
        # "--ids", "94,95,96,97,99,100,101,103,105,106,107,108",
        "--ids", "71,72,73,74,76,77,78,80,82,83,84,85,86,87,88",
        "--until-step", "6",
        # 可选参数示例：
        "--llm-workers", "2",
        "--chapter-workers", "2",
        # "--docx-workers", "1",
        # "--overwrite"
    ]
    main()