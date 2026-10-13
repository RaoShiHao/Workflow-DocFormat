"""Step 1 runner: draft a genre Role library (themes moved to step2).

Role library = soft genre prior (what content types this genre usually has).
It is NOT a hard selection menu and does NOT require mutually distinct formats.

Pipeline per genre:
  1) LLM → draft.yaml / inventory.md / tree.md
  2) themes：请用 ``run_step2.py``（C1/C2/C3）；本脚本默认 ``--theme-count 0``

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/run_step1.py --id 1 --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_step1.py --excel LongDocFmtEval/pipeline_auto/config/genres.xlsx --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_step1.py --excel ... --all --overwrite
  python LongDocFmtEval/pipeline_auto/scripts/run_step1.py --make-template LongDocFmtEval/pipeline_auto/config/genres.xlsx
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.llm import load_prompt, render_template
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import (
    CONFIG_DIR,
    GENRE_SUGGESTIONS_DIR,
    ROLE_LIBRARY_DRAFTS_DIR,
    ROLE_LIBRARY_LLM_CONFIG_PATH,
    WORK_DIR,
)
from LongDocFmtEval.common.role_library import (
    ROLE_LIBRARY_RESPONSE_FORMAT,
    lenient_json_load,
    normalize_to_parent,
    summary,
    validate,
    write_inventory_md,
)
from LongDocFmtEval.common.themes_xlsx import make_themes_template

DRAFTS_DIR = ROLE_LIBRARY_DRAFTS_DIR
SUGGEST_DIR = GENRE_SUGGESTIONS_DIR

STEP1_ERROR_FILENAME = "error.json"
STEP1_FAIL_MARKER = "STEP1_FAILED"


def _write_step1_error(
    out_dir: Path,
    *,
    stage: str,
    problems: list[str] | None = None,
    error: str | None = None,
    genre_id: int | None = None,
    genre: str = "",
    locale: str = "",
    extra: dict | None = None,
) -> Path:
    """Persist a marked failure record next to draft/raw for later browsing."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "ok": False,
        "step": "step1",
        "marker": STEP1_FAIL_MARKER,
        "stage": stage,
        "id": genre_id,
        "genre": genre,
        "locale": locale,
        "problems": list(problems or []),
        "error": error,
        "hint": (
            "Role 库未通过校验或未解析成功；下游 step2/3 勿依赖本目录产物。"
            "未知 format_focus 会被丢掉（不阻断）；结构问题仍失败。"
            "请检查 raw.txt / draft.yaml 后必要时 --overwrite 重跑 step1。"
        ),
        "artifacts": {
            "raw": str((out_dir / "raw.txt").resolve()) if (out_dir / "raw.txt").is_file() else None,
            "draft": str((out_dir / "draft.yaml").resolve()) if (out_dir / "draft.yaml").is_file() else None,
            "inventory": str((out_dir / "inventory.md").resolve()) if (out_dir / "inventory.md").is_file() else None,
        },
    }
    if extra:
        payload.update(extra)
    path = out_dir / STEP1_ERROR_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _clear_step1_error(out_dir: Path) -> None:
    path = Path(out_dir) / STEP1_ERROR_FILENAME
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


# genres：每行一种语言的独立体裁。locale 决定建库语言；展示名/notes 用单语列。
GENRE_COLUMNS = [
    "id", "domain_id", "domain", "genre", "locale",
    "genre_display_name", "supplementary_notes", "enabled",
]

EXAMPLE_ROWS = [
    {
        "id": 1,
        "domain_id": "academic_theses",
        "domain": "学术学位论文",
        "genre": "gaobiaozhun_xueshu_xueweishu",
        "locale": "zh",
        "genre_display_name": "高标准学术学位论文",
        "supplementary_notes": "内容参考宜覆盖：封面校徽/院徽标识图、公式截图、算法流程图、实验装置照片等不同语义的图类型（供大纲参考，非硬性全选）。",
        "enabled": 1,
    },
    {
        "id": 2,
        "domain_id": "academic_theses",
        "domain": "Academic Theses",
        "genre": "phd_dissertation_stem",
        "locale": "en",
        "genre_display_name": "STEM PhD dissertation",
        "supplementary_notes": "Prefer covering cover emblem, equation screenshots, algorithm flowcharts, apparatus photos; front matter lower-roman pagination (soft inventory).",
        "enabled": 1,
    },
]


def _parse_id(value) -> int | None:
    s = str(value if value is not None else "").strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def assign_row_ids(rows: list[dict], *, start: int = 1) -> list[dict]:
    """Ensure each row has a sequential integer id (sheet order)."""
    out: list[dict] = []
    n = start
    for r in rows:
        rr = dict(r)
        rr["id"] = n
        n += 1
        out.append(rr)
    return out


def draft_dir_name(genre_id: int, genre: str, locale: str) -> str:
    """step1 产物目录名：{id:02d}_{genre}_{locale}。"""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    return f"{int(genre_id):02d}_{genre}_{loc}"


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


SCOPE_COLUMNS = ["label", "domain_scope_zh", "example_genres_zh", "domain_scope_en", "example_genres_en",
                 "count_zh", "count_en", "enabled"]

# Distilled from config/domain_scopes.md.
# 中英各自独立生成体裁（默认各 20），禁止互译配对。
# (label, zh_scope, zh_examples, en_scope, en_examples, count_zh, count_en, enabled)
SCOPE_ROWS = [
    ("broad_all", "复杂排版的正式文档", "学位论文、判决书、招股说明书、政府公文、国家标准、商业计划书、临床研究报告、专利说明书",
     "Complex-layout formal documents", "thesis, court judgment, prospectus, government document, national standard, business plan, clinical study report, patent specification", 20, 20, 0),
    ("complex_layout", "版式复杂、异构性高的长文档", "多分节/分栏文档、含三线表与网格表的报告、图文混排画册、带页眉页脚与页码规则的正式文本",
     "Layout-rich, highly heterogeneous long documents", "multi-section/multi-column documents, reports with three-line and grid tables, image-text albums, formal texts with header/footer and page-number rules", 20, 20, 0),
    ("academic", "学术与科研类文档", "学位论文、期刊论文、开题报告、综述、基金申请书、实验报告、会议论文",
     "Academic & research documents", "thesis, journal paper, research proposal, literature review, grant application, lab report, conference paper", 20, 20, 1),
    ("legal", "司法与法律类文档", "判决书、起诉状、上诉状、法律意见书、合同、仲裁裁决书、公证书",
     "Judicial & legal documents", "court judgment, complaint, appellate brief, legal opinion, contract, arbitration award, notarial deed", 20, 20, 1),
    ("finance_disclosure", "金融披露类文档", "招股说明书、年度报告、募集说明书、审计报告、评级报告、尽职调查报告",
     "Financial disclosure documents", "IPO prospectus, annual report, offering circular, audit report, rating report, due diligence report", 20, 20, 1),
    ("government", "政务公文类文档", "通知、通报、请示、批复、会议纪要、政府工作报告、预算报告",
     "Government official documents", "notice, circular, request for instructions, official reply, meeting minutes, government work report, budget report", 20, 20, 1),
    ("standard", "技术标准与规范类文档", "国家/行业标准、技术规范、检验规程、产品说明书、技术白皮书",
     "Technical standards & specifications", "national/industry standard, technical specification, inspection procedure, product manual, technical white paper", 20, 20, 1),
    ("business_report", "商业报告类文档", "商业计划书、市场研究报告、咨询报告、投标书、可行性研究报告",
     "Business report documents", "business plan, market research report, consulting report, tender document, feasibility study report", 20, 20, 1),
    ("medical", "医疗卫生类文档", "临床研究报告、诊疗指南、病历、知情同意书、药品说明书、卫生统计年报",
     "Healthcare & medical documents", "clinical study report, clinical practice guideline, medical record, informed consent form, drug package insert, health statistics yearbook", 20, 20, 1),
    ("ip_patent", "知识产权与专利类文档", "专利说明书、权利要求书、商标注册文件、专利检索报告、无效宣告请求书",
     "Intellectual property & patent documents", "patent specification, claims, trademark registration file, patent search report, invalidation request", 20, 20, 1),
    ("engineering", "工程与建筑类文档", "施工组织设计、工程勘察报告、招标文件、竣工验收报告、设计说明书",
     "Engineering & construction documents", "construction organization design, geotechnical survey report, tender document, completion acceptance report, design specification", 20, 20, 1),
    ("education", "教育与考试类文档", "教材、考试大纲、试卷、教学大纲、评估报告、课程标准",
     "Education & examination documents", "textbook, exam syllabus, exam paper, teaching syllabus, evaluation report, curriculum standard", 20, 20, 1),
    ("publishing", "出版与传媒类文档", "图书排版稿、期刊、画册、年鉴、新闻稿、宣传册",
     "Publishing & media documents", "book typesetting draft, magazine, picture album, yearbook, press release, brochure", 20, 20, 1),
    ("hr_admin", "人力资源与行政类文档", "员工手册、规章制度、岗位说明书、劳动合同、培训手册",
     "HR & administrative documents", "employee handbook, rules and regulations, job description, labor contract, training manual", 20, 20, 1),
    ("audit_accounting", "审计与会计类文档", "审计报告、财务报表附注、内控评价报告、税务鉴证报告",
     "Audit & accounting documents", "audit report, notes to financial statements, internal control evaluation report, tax attestation report", 20, 20, 1),
    ("urban_env", "城市规划与环境类文档", "国土空间规划、环境影响评价报告、可行性研究、专项规划文本",
     "Urban planning & environmental documents", "territorial spatial plan, environmental impact assessment report, feasibility study, special plan text", 20, 20, 1),
    ("insurance", "保险类文档", "保险条款、保单、理赔说明、精算报告、产品说明书",
     "Insurance documents", "insurance clauses, policy, claims explanation, actuarial report, product manual", 20, 20, 1),
    ("intl_affairs", "国际组织与外事类文档", "协定、备忘录、决议、白皮书、公约文本",
     "International organization & foreign affairs documents", "treaty, memorandum, resolution, white paper, convention text", 20, 20, 1),
]


def make_scopes_template(path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "scopes"
    ws.append(SCOPE_COLUMNS)
    for row in SCOPE_ROWS:
        ws.append(list(row))
    head_fill = PatternFill("solid", fgColor="4F81BD")
    head_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill, cell.font = head_fill, head_font
    ws.freeze_panes = "A2"
    for col, w in {"A": 16, "B": 22, "C": 46, "D": 24, "E": 48, "F": 10, "G": 10, "H": 8}.items():
        ws.column_dimensions[col].width = w
    for r in ws.iter_rows(min_row=2):
        for c in (1, 2, 3, 4):  # scope/examples zh & en
            r[c].alignment = Alignment(wrap_text=True, vertical="top")
    dv = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"H2:H{ws.max_row}")

    ws2 = wb.create_sheet("说明")
    for line in [
        ["字段", "含义"],
        ["label", "领域短标识（用作输出子目录名）"],
        ["domain_scope_zh", "中文领域范围；只用于生成中文体裁"],
        ["example_genres_zh", "中文体裁示例（顿号分隔），参考锚点、非硬性清单"],
        ["domain_scope_en", "英文领域范围；只用于生成英文体裁"],
        ["example_genres_en", "英文体裁示例（逗号分隔），参考锚点"],
        ["count_zh", "该领域生成多少条中文体裁（默认 20；可被 --count / --count-zh 覆盖）"],
        ["count_en", "该领域生成多少条英文体裁（默认 20；可被 --count / --count-en 覆盖）"],
        ["enabled", "1=参与批量；0=跳过"],
        ["", ""],
        ["独立性", "中英各自独立生成，禁止互译配对；同一题材下不同语言可有不同文书类型"],
        ["运行", "python LongDocFmtEval/pipeline_auto/scripts/run_step1.py --scopes-excel LongDocFmtEval/pipeline_auto/config/domain_scopes.xlsx"],
        ["产物", "work/step0_genre_suggestions/<时间戳>_batch/<label>/… + 汇总 genres_all.xlsx（人工筛选后进 config/genres.xlsx）"],
    ]:
        ws2.append(line)
    ws2.column_dimensions["A"].width = 18
    ws2.column_dimensions["B"].width = 90
    for r in ws2.iter_rows():
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"[scopes template] {path} （{len(SCOPE_ROWS)} 个领域）")


def make_template(path: Path, blank_rows: int = 8, rows: list[dict] | None = None) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "genres"
    ws.append(GENRE_COLUMNS)
    src_rows = rows if rows is not None else EXAMPLE_ROWS
    # 写出前统一按表序编号，保证 id 连续、可复现
    src_rows = assign_row_ids(list(src_rows))
    for r in src_rows:
        ws.append([r.get(c, "") for c in GENRE_COLUMNS])
    blank = {"locale": "zh", "enabled": 1}
    for _ in range(blank_rows):
        ws.append([blank.get(c, "") for c in GENRE_COLUMNS])

    head_fill = PatternFill("solid", fgColor="4F81BD")
    head_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    widths = {
        "id": 6, "domain_id": 18, "domain": 18, "genre": 28, "locale": 8,
        "genre_display_name": 28, "supplementary_notes": 72, "enabled": 9,
    }
    wrap_cols = {"supplementary_notes"}
    for idx, col in enumerate(GENRE_COLUMNS):
        letter = get_column_letter(idx + 1)
        ws.column_dimensions[letter].width = widths.get(col, 16)
        if col in wrap_cols:
            for row in ws.iter_rows(min_row=2):
                row[idx].alignment = Alignment(wrap_text=True, vertical="top")

    en_col = get_column_letter(GENRE_COLUMNS.index("enabled") + 1)
    dv = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{en_col}2:{en_col}{ws.max_row}")

    ws2 = wb.create_sheet("说明")
    for line in [
        ["字段", "含义 / 填写说明"],
        ["id", "★稳定编号（从 1 起）。CLI 用 --id / --ids 指定；产物目录前缀为 {id:02d}_…"],
        ["domain_id", "所属一级领域 id（可选）"],
        ["domain", "本行 locale 下的领域展示名（可选）"],
        ["genre", "体裁标识（snake_case）；中英可不同，勿要求互译配对"],
        ["locale", "★建库语言：zh 或 en（每行一种语言的独立体裁）"],
        ["genre_display_name", "本行 locale 下的展示名"],
        ["supplementary_notes", "★本行 locale 下的补充说明"],
        ["enabled", "1=本行参与批量运行；0=跳过"],
        ["", ""],
        ["说明", "中英文体裁各自独立：不要把同一体裁做成中英对照翻译行"],
        ["兼容", "仍可读旧表 genre_display_name_zh/_en；优先无后缀单语列"],
        ["按 id 跑", "python …/run_step1.py --excel config/genres.xlsx --ids 1,26 --overwrite"],
        ["产物", "datasets/{id}_{lang}_{topic_name}/step1_role/（--id）；flat 兼容 work/step1_role_library_drafts/"],
    ]:
        ws2.append(line)
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 92
    for row in ws2.iter_rows():
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    ws2["A1"].font = Font(bold=True)
    ws2["B1"].font = Font(bold=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"[template] {path}")


def _prompt_path(locale: str, name: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name)


def _suggest_locale(*, locale: str, scope: str, examples: str, count: int,
                    extra_notes: str, run_dir: Path, temp: float, max_tokens: int,
                    seen: set[str], per_round: int = 5) -> list[dict]:
    """Generate `count` monolingual genres for one locale; archive per-round under run_dir/<locale>/."""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    if not scope.strip():
        print(f"[skip] locale={loc}: 领域描述为空，跳过")
        return []
    count = max(0, int(count))
    if count <= 0:
        return []

    loc_dir = run_dir / loc
    loc_dir.mkdir(parents=True, exist_ok=True)
    prompt = load_prompt(_prompt_path(loc, "step0_suggest_genres.yaml"))
    locale_label = "中文" if loc == "zh" else "English"
    default_examples = "（无，自行发散）" if loc == "zh" else "(none, diverge freely)"
    rows: list[dict] = []
    rounds = max(1, (count + per_round - 1) // per_round)
    remaining = count

    for r in range(1, rounds + 1):
        n_this = min(per_round, remaining)
        avoid = sorted({row["genre"] for row in rows if row.get("genre")})
        note = extra_notes or ("无" if loc == "zh" else "none")
        if avoid:
            note = f"{note}\n已生成 genre ids（请勿重复 / already produced, do not repeat）: {', '.join(avoid)}"
        user = render_template(
            prompt["user_template"],
            domain_scope=scope,
            example_genres=examples or default_examples,
            locale=loc,
            locale_label=locale_label,
            count=str(n_this),
            extra_notes=note,
        )
        # system 模板也含 locale_label
        system = render_template(prompt["system"], locale_label=locale_label, locale=loc)
        tag = f"round{r:02d}"
        print(f"\n=== step0 [{run_dir.name}/{loc}] round {r}/{rounds} x{n_this} ===")
        raw = stream_llm_text(system=system, user=user, temperature=temp, max_tokens=max_tokens)
        (loc_dir / f"{tag}.raw.txt").write_text(raw, encoding="utf-8")
        try:
            data = lenient_json_load(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {loc}/{tag} 解析失败: {e}")
            continue
        round_rows: list[dict] = []
        for g in data.get("genres") or []:
            if len(rows) >= count:
                break
            gid = str(g.get("genre", "")).strip()
            name = str(g.get("genre_display_name", "") or "").strip()
            notes = str(g.get("supplementary_notes", "") or "").strip()
            if not gid or not name:
                continue
            base, k = gid, 2
            while gid in seen:
                gid = f"{base}_{k}"
                k += 1
            seen.add(gid)
            row = {
                "genre": gid,
                "locale": loc,
                "genre_display_name_zh": name if loc == "zh" else "",
                "genre_display_name_en": name if loc == "en" else "",
                "supplementary_notes_zh": notes if loc == "zh" else "",
                "supplementary_notes_en": notes if loc == "en" else "",
                "enabled": 1,
            }
            round_rows.append(row)
            rows.append(row)
        (loc_dir / f"{tag}.json").write_text(json.dumps(round_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        make_template(loc_dir / f"{tag}.xlsx", blank_rows=0, rows=round_rows)
        remaining = count - len(rows)
        if remaining <= 0:
            break

    make_template(loc_dir / "genres.xlsx", blank_rows=0, rows=rows)
    print(f"  [{loc}] {len(rows)}/{count} 条")
    return rows


def _maybe_read_text(value: str) -> str:
    """If value is an existing file path, read UTF-8 text; otherwise return as-is."""
    s = (value or "").strip()
    if not s:
        return ""
    if s.startswith("@"):
        s = s[1:].strip()
    p = Path(s)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return value


def _suggest_into(scope_zh: str, examples_zh: str, scope_en: str, examples_en: str,
                  count_zh: int, count_en: int,
                  extra_notes: str, run_dir: Path, temp: float, max_tokens: int,
                  seen: set[str], per_round: int = 5,
                  extra_notes_zh: str = "", extra_notes_en: str = "") -> list[dict]:
    """Core step0 for ONE domain: independently draft zh then en genres (no translation pairs)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    notes_zh = (extra_notes_zh or extra_notes or "").strip()
    notes_en = (extra_notes_en or extra_notes or "").strip()
    rows: list[dict] = []
    rows.extend(_suggest_locale(
        locale="zh", scope=scope_zh, examples=examples_zh, count=count_zh,
        extra_notes=notes_zh, run_dir=run_dir, temp=temp, max_tokens=max_tokens,
        seen=seen, per_round=per_round,
    ))
    rows.extend(_suggest_locale(
        locale="en", scope=scope_en or scope_zh, examples=examples_en, count=count_en,
        extra_notes=notes_en, run_dir=run_dir, temp=temp, max_tokens=max_tokens,
        seen=seen, per_round=per_round,
    ))
    make_template(run_dir / "genres.xlsx", blank_rows=0, rows=rows)
    return rows


def _slug(text: str, i: int) -> str:
    import re
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(text)).strip("_").lower()
    return (s[:24] or f"scope{i}")


def suggest_genres(scope_zh: str, examples_zh: str, scope_en: str, examples_en: str,
                   count_zh: int, count_en: int,
                   extra_notes: str, out_xlsx: Path | None, temperature: float | None,
                   max_tokens: int, per_round: int = 5,
                   extra_notes_zh: str = "", extra_notes_en: str = "") -> None:
    """Single domain -> its own timestamped dir (never overwrites). zh/en each independent."""
    temp = temperature if temperature is not None else 0.95
    run_dir = SUGGEST_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = _suggest_into(
        scope_zh, examples_zh, scope_en, examples_en, count_zh, count_en,
        extra_notes, run_dir, temp, max_tokens, set(), per_round=per_round,
        extra_notes_zh=extra_notes_zh, extra_notes_en=extra_notes_en,
    )
    meta = {
        "domain_scope_zh": scope_zh, "example_genres_zh": examples_zh,
        "domain_scope_en": scope_en, "example_genres_en": examples_en,
        "count_zh": count_zh, "count_en": count_en, "per_round": per_round,
        "temperature": temp, "total": len(rows),
        "n_zh": sum(1 for r in rows if r.get("locale") == "zh"),
        "n_en": sum(1 for r in rows if r.get("locale") == "en"),
        "policy": "zh/en independent monolingual lists; no translation pairs",
        "has_extra_notes_zh": bool((extra_notes_zh or extra_notes or "").strip()),
        "has_extra_notes_en": bool((extra_notes_en or extra_notes or "").strip()),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[suggested] {len(rows)} genres (zh={meta['n_zh']}, en={meta['n_en']}) -> {run_dir}\\genres.xlsx")
    for r in rows:
        name = r.get("genre_display_name_zh") or r.get("genre_display_name_en") or ""
        print(f"  - [{r['locale']}] {r['genre']}: {name}")
    if out_xlsx is not None:
        make_template(out_xlsx, blank_rows=0, rows=rows)
        print(f"[also written] {out_xlsx}")


def run_scopes_excel(path: Path, temperature: float | None, max_tokens: int,
                     count_zh_override: int | None, count_en_override: int | None,
                     per_round: int = 5) -> None:
    """Batch over a domain-scope Excel; each enabled row -> its own subfolder; pool a master Excel."""
    import pandas as pd

    temp = temperature if temperature is not None else 0.95
    df = pd.read_excel(path, sheet_name=0).fillna("")
    parent = SUGGEST_DIR / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_batch")
    parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    master: list[dict] = []
    used_labels: set[str] = set()
    n_rows = 0
    for i, r in df.iterrows():
        scope_zh = str(r.get("domain_scope_zh", "") or r.get("domain_scope", "")).strip()
        if not scope_zh:
            continue
        if str(r.get("enabled", 1)).strip() in ("0", "false", "False"):
            continue
        examples_zh = str(r.get("example_genres_zh", "") or r.get("example_genres", "")).strip()
        scope_en = str(r.get("domain_scope_en", "")).strip()
        examples_en = str(r.get("example_genres_en", "")).strip()
        # 兼容旧表：rounds * per_round → count；新表用 count_zh / count_en
        if "count_zh" in df.columns or "count_en" in df.columns:
            count_zh = count_zh_override if count_zh_override is not None else int(r.get("count_zh") or 20)
            count_en = count_en_override if count_en_override is not None else int(r.get("count_en") or 20)
        else:
            rounds = int(r.get("rounds") or 1)
            pr = int(r.get("per_round") or 5)
            legacy = max(1, rounds * pr)
            count_zh = count_zh_override if count_zh_override is not None else legacy
            count_en = count_en_override if count_en_override is not None else legacy
        label = str(r.get("label", "")).strip() or _slug(scope_zh, i)
        base = label
        j = 2
        while label in used_labels:
            label = f"{base}_{j}"; j += 1
        used_labels.add(label)
        rows = _suggest_into(scope_zh, examples_zh, scope_en, examples_en, count_zh, count_en, "",
                             parent / label, temp, max_tokens, seen, per_round=per_round)
        master.extend(rows)
        n_rows += 1

    make_template(parent / "genres_all.xlsx", blank_rows=0, rows=master)
    n_zh = sum(1 for x in master if x.get("locale") == "zh")
    n_en = sum(1 for x in master if x.get("locale") == "en")
    (parent / "meta.json").write_text(json.dumps(
        {"source": str(path), "domains": n_rows, "total_genres": len(master),
         "n_zh": n_zh, "n_en": n_en, "temperature": temp,
         "policy": "zh/en independent monolingual lists; no translation pairs"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[batch done] {n_rows} 个领域 → {len(master)} 个体裁（zh={n_zh}, en={n_en}）")
    print(f"[parent dir] {parent}")
    print(f"[master]     {parent}\\genres_all.xlsx（人工筛选后整理进 config/genres.xlsx）")


def run_domains_list(
    domains_zh: list[str],
    domains_en: list[str] | None,
    *,
    count_zh: int,
    count_en: int,
    temperature: float | None,
    max_tokens: int,
    per_round: int = 5,
    extra_notes: str = "",
) -> None:
    """按多个领域主题批量发散：每个主题各出中英体裁（默认各 count_* 条）。"""
    domains_zh = [d.strip() for d in domains_zh if d and str(d).strip()]
    if not domains_zh:
        raise ValueError("domains 列表为空")
    domains_en = [d.strip() for d in (domains_en or []) if d and str(d).strip()]
    temp = temperature if temperature is not None else 0.95
    parent = SUGGEST_DIR / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_domains")
    parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    master: list[dict] = []
    used_labels: set[str] = set()

    print(f"[domains] {len(domains_zh)} 个主题 → 每主题 zh×{count_zh} + en×{count_en}")
    print(f"[hint] --example-genres 非必需；不填则由模型在该主题下自行发散")

    for i, scope_zh in enumerate(domains_zh):
        if i < len(domains_en):
            scope_en = domains_en[i]
        else:
            # 无配对英文时：用「该主题下的英语文书生态」作独立发散锚点，禁止直译中文清单
            scope_en = (
                f"Formal / complex-layout documents in the domain of 「{scope_zh}」 "
                f"(English-speaking practice; invent genre types independently — do NOT translate Chinese lists)"
            )
        label = _slug(scope_zh, i)
        base = label
        j = 2
        while label in used_labels:
            label = f"{base}_{j}"
            j += 1
        used_labels.add(label)
        print(f"\n----- domain ({i + 1}/{len(domains_zh)}) {scope_zh} -----")
        rows = _suggest_into(
            scope_zh, "", scope_en, "", count_zh, count_en, extra_notes,
            parent / label, temp, max_tokens, seen, per_round=per_round,
        )
        master.extend(rows)

    make_template(parent / "genres_all.xlsx", blank_rows=0, rows=master)
    n_zh = sum(1 for x in master if x.get("locale") == "zh")
    n_en = sum(1 for x in master if x.get("locale") == "en")
    (parent / "meta.json").write_text(json.dumps({
        "domains_zh": domains_zh,
        "domains_en": domains_en,
        "count_zh": count_zh,
        "count_en": count_en,
        "total_genres": len(master),
        "n_zh": n_zh,
        "n_en": n_en,
        "policy": "multi-domain diverge; zh/en independent; examples optional",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[domains done] {len(domains_zh)} 主题 → {len(master)} 体裁（zh={n_zh}, en={n_en}）")
    print(f"[parent dir] {parent}")
    print(f"[master]     {parent}\\genres_all.xlsx")


def _parse_domains_arg(text: str) -> list[str]:
    """支持逗号 / 顿号 / 分号 / 换行分隔。"""
    import re
    parts = re.split(r"[,，;；\n]+", text or "")
    return [p.strip() for p in parts if p.strip()]


def _load_domains_file(path: Path) -> tuple[list[str], list[str]]:
    """每行一个主题。格式：中文 或 中文|英文 或 中文\\t英文。# 开头为注释。"""
    zh, en = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "|" in s:
            a, b = s.split("|", 1)
        elif "\t" in s:
            a, b = s.split("\t", 1)
        else:
            a, b = s, ""
        a, b = a.strip(), b.strip()
        if a:
            zh.append(a)
            if b:
                en.append(b)
    return zh, en


def _pick(r, base: str, locale: str):
    """Resolve a bilingual field by locale, with fallback (also supports old single-column format)."""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    for key in (f"{base}_{loc}", f"{base}_zh", f"{base}_en", base):
        v = str(r.get(key, "") or "").strip()
        if v:
            return v
    return ""


def read_excel(
    path: Path,
    build_locales: list[str] | None = None,
    *,
    only_ids: list[int] | None = None,
    include_disabled: bool = False,
) -> list[dict]:
    """Read genre rows. If build_locales is given, expand each genre to those languages
    (ignoring the row's own locale); otherwise use the row's locale column (default zh).

    ``id``：表中有则用；缺省则按表序从 1 编号（含 disabled 行，保证编号稳定）。
    ``only_ids``：只返回这些 id（仍尊重 enabled，除非 include_disabled）。
    """
    import pandas as pd

    df = pd.read_excel(path, sheet_name=0).fillna("")
    has_id_col = "id" in df.columns
    # 先扫全表（含 enabled=0），再过滤；id 缺失/重复时按表序重编号
    staged: list[dict] = []
    for _, r in df.iterrows():
        genre = str(r.get("genre", "") or "").strip()
        if not genre:
            continue
        enabled = str(r.get("enabled", 1)).strip() not in ("0", "false", "False")
        staged.append({
            "id": _parse_id(r.get("id")) if has_id_col else None,
            "genre": genre,
            "locale_raw": str(r.get("locale", "") or "zh").strip() or "zh",
            "display_zh": str(r.get("genre_display_name_zh", "") or "").strip(),
            "display_en": str(r.get("genre_display_name_en", "") or "").strip(),
            "notes_zh": str(r.get("supplementary_notes_zh", "") or "").strip(),
            "notes_en": str(r.get("supplementary_notes_en", "") or "").strip(),
            "enabled": enabled,
            "_row": r,
        })

    ids_seen = [s["id"] for s in staged]
    if (not has_id_col) or any(i is None for i in ids_seen) or len(ids_seen) != len(set(ids_seen)):
        for i, s in enumerate(staged, 1):
            s["id"] = i

    id_filter = set(only_ids) if only_ids else None
    rows = []
    for s in staged:
        if id_filter is not None and s["id"] not in id_filter:
            continue
        if not include_disabled and not s["enabled"]:
            continue
        locs = build_locales or [s["locale_raw"]]
        for locale in locs:
            loc = "en" if str(locale).lower().startswith("en") else "zh"
            display = s["display_en"] if loc == "en" else s["display_zh"]
            if not display:
                display = _pick(s["_row"], "genre_display_name", loc) or s["genre"]
            notes = s["notes_en"] if loc == "en" else s["notes_zh"]
            if not notes:
                notes = _pick(s["_row"], "supplementary_notes", loc) or ("none" if loc == "en" else "无")
            rows.append({
                "id": s["id"],
                "genre": s["genre"],
                "locale": loc,
                "display": display,
                "notes": notes,
            })
    return rows


def rewrite_genres_excel_with_ids(path: Path) -> Path:
    """Read existing genres.xlsx and rewrite with sequential id column (1..n)."""
    import pandas as pd

    df = pd.read_excel(path, sheet_name=0).fillna("")
    rows: list[dict] = []
    for _, r in df.iterrows():
        genre = str(r.get("genre", "") or "").strip()
        if not genre:
            continue
        rows.append({
            "genre": genre,
            "locale": str(r.get("locale", "") or "zh").strip() or "zh",
            "genre_display_name_zh": str(r.get("genre_display_name_zh", "") or "").strip(),
            "genre_display_name_en": str(r.get("genre_display_name_en", "") or "").strip(),
            "supplementary_notes_zh": str(r.get("supplementary_notes_zh", "") or "").strip(),
            "supplementary_notes_en": str(r.get("supplementary_notes_en", "") or "").strip(),
            "enabled": 0 if str(r.get("enabled", 1)).strip() in ("0", "false", "False") else 1,
        })
    rows = assign_row_ids(rows)
    make_template(path, blank_rows=0, rows=rows)
    print(f"[ids] 已写入 id=1..{len(rows)} → {path}")
    return path


def generate_themes_via_library(
    out_dir: Path,
    *,
    count: int,
    genre: str,
    locale: str,
    overwrite: bool,
    temperature: float | None = None,
    max_tokens: int = 16384,
) -> bool:
    """Themes grounded in Role library via step2 prompt (``step2_suggest_themes.yaml``)."""
    from LongDocFmtEval.pipeline_auto.scripts.run_step2 import run_one as themes_run_one

    return themes_run_one(
        out_dir,
        count=max(1, count),
        library=None,
        genre_arg=genre,
        locale_arg=locale,
        extra_notes="",
        temperature=temperature,
        max_tokens=max_tokens,
        overwrite=overwrite,
        sim_threshold=0.82,
        enable_sim_filter=False,
        out_dir=out_dir,
    )


def gen_themes(genre: str, display: str, locale: str, out_dir: Path, count: int,
               extra_notes: str = "无", temperature: float = 0.9, max_tokens: int = 6000) -> int:
    """Deprecated alias: themes without a separate prompt — use library-grounded step2."""
    _ = (display, extra_notes)
    ok = generate_themes_via_library(
        out_dir,
        count=count,
        genre=genre,
        locale=locale,
        overwrite=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not ok:
        return 0
    xlsx = out_dir / "themes.xlsx"
    if not xlsx.is_file():
        return 0
    try:
        from openpyxl import load_workbook  # noqa: PLC0415

        wb = load_workbook(xlsx, read_only=True)
        n = max(0, (wb.active.max_row or 1) - 1)
        wb.close()
        return n
    except Exception:  # noqa: BLE001
        return count


def run_one(genre: str, display: str, locale: str, notes: str, temperature: float | None,
            max_tokens: int | None,
            theme_count: int = 0, overwrite: bool = False, genre_id: int | None = None,
            out_dir: Path | None = None) -> bool:
    temperature = temperature if temperature is not None else 0.4
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    # 无 id 时仍可用 genre_locale（兼容手写 --genre）；有 id 则绑定编号目录
    if genre_id is not None:
        dir_name = draft_dir_name(genre_id, genre, loc)
    else:
        dir_name = f"{genre}_{loc}"
    lib_slug = f"{genre}_{loc}"  # 提示词 / 金标文件名仍用 genre_locale
    # 默认：有 id → datasets/.../step1_role/；否则 → work flat mirror
    if out_dir is not None:
        out_dir = Path(out_dir)
    elif genre_id is not None:
        from LongDocFmtEval.pipeline_auto.topic_layout import resolve_topic_dir, role_dir  # noqa: PLC0415

        topic = resolve_topic_dir(
            int(genre_id), genre=genre, locale=loc, display_name=display or genre,
        )
        out_dir = role_dir(topic)
    else:
        out_dir = DRAFTS_DIR / dir_name
    draft = out_dir / "draft.yaml"
    themes_xlsx = out_dir / "themes.xlsx"

    skipped_lib = False
    if draft.exists() and not overwrite:
        print(f"\n=== step1: id={genre_id} {dir_name} ===")
        print(f"[cache] Role 库已存在，跳过建库：{out_dir}（--overwrite 可强制重跑）")
        skipped_lib = True
        try:
            cached = yaml.safe_load(draft.read_text(encoding="utf-8")) or {}
            if not isinstance(cached, dict):
                cached = {}
            dropped_focus: set[str] = set()
            cached = normalize_to_parent(cached, dropped_focus=dropped_focus)
            if dropped_focus:
                print(f"[focus] 丢弃未知标签: {sorted(dropped_focus)}")
                header = (
                    f"# 体裁内容参考清单草稿（Role 库 / step1）\n"
                    f"# id={genre_id}  dir={dir_name}\n"
                    f"# sanitized: dropped unknown format_focus {sorted(dropped_focus)}\n"
                )
                draft.write_text(
                    header + yaml.safe_dump(cached, allow_unicode=True, sort_keys=False, width=1000),
                    encoding="utf-8",
                )
                try:
                    write_inventory_md(cached, out_dir / "inventory.md")
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 写出 inventory 失败: {e}")
            problems = validate(cached)
            if problems:
                ep = _write_step1_error(
                    out_dir,
                    stage="validate_cached",
                    problems=problems,
                    genre_id=genre_id,
                    genre=genre,
                    locale=loc,
                    extra={"counts": {k: len(v) for k, v in summary(cached).items()}},
                )
                print(f"[validate] cached draft 不合格 → {ep.name}: {problems}")
                lib_ok = False
            else:
                _clear_step1_error(out_dir)
                lib_ok = True
        except Exception as e:  # noqa: BLE001
            ep = _write_step1_error(
                out_dir,
                stage="validate_cached",
                error=str(e),
                genre_id=genre_id,
                genre=genre,
                locale=loc,
            )
            print(f"[validate] cached draft 读取/校验异常 → {ep.name}: {e}")
            lib_ok = False
    else:
        from LongDocFmtEval.common.llm_usage import (  # noqa: PLC0415
            print_usage_summary,
            usage_scope,
            write_step_usage_report,
        )

        prompt = load_prompt(_prompt_path(locale, "step1_role_library.yaml"))
        user = render_template(
            prompt["user_template"],
            genre=lib_slug,
            genre_display_name=display or genre,
            locale=locale or "zh",
            supplementary_notes=notes or "无",
        )
        print(f"\n=== step1: id={genre_id} {dir_name} ===")
        llm_cfg = ROLE_LIBRARY_LLM_CONFIG_PATH
        print(
            f"[llm] route=role_library → {llm_cfg} "
            f"max_tokens={max_tokens if max_tokens is not None else '(from yaml / omit)'}"
        )

        def _gen_role_library(*, label: str = "role_library") -> str:
            # 整库 JSON 很长：非流式会卡在「请求中」直到全部生成，中转常 120s 报 524。
            return stream_llm_text(
                system=prompt["system"],
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                route="role_library",
                llm_config=llm_cfg,
                label=label,
                response_format=ROLE_LIBRARY_RESPONSE_FORMAT,
            )

        recover_strategies: list[str] = []
        with usage_scope("step1") as _usage_rows:
            raw = _gen_role_library(label="role_library")
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "raw.txt").write_text(raw, encoding="utf-8")
            try:
                dropped_focus: set[str] = set()
                data = normalize_to_parent(lenient_json_load(raw), dropped_focus=dropped_focus)
                recover_strategies = ["ok"]
                if dropped_focus:
                    print(f"[focus] 丢弃未知标签: {sorted(dropped_focus)}")
            except Exception as e:  # noqa: BLE001
                print(f"[retry] 首轮解析失败: {e}")
                print("[retry] 整步重跑 step1 Role 库生成（1 次）…", flush=True)
                (out_dir / "raw.attempt1.txt").write_text(raw, encoding="utf-8")
                recover_strategies = ["retry"]
                try:
                    raw = _gen_role_library(label="role_library_retry")
                    (out_dir / "raw.txt").write_text(raw, encoding="utf-8")
                    dropped_focus = set()
                    data = normalize_to_parent(lenient_json_load(raw), dropped_focus=dropped_focus)
                    print("[retry] 重试解析成功")
                    if dropped_focus:
                        print(f"[focus] 丢弃未知标签: {sorted(dropped_focus)}")
                except Exception as e2:  # noqa: BLE001
                    e = e2
                    ep = _write_step1_error(
                        out_dir,
                        stage="parse_normalize",
                        error=str(e),
                        genre_id=genre_id,
                        genre=genre,
                        locale=loc,
                        extra={
                            "raw_chars": len(raw),
                            "recover_strategies": recover_strategies,
                        },
                    )
                    try:
                        (out_dir / "meta.json").write_text(
                            json.dumps(
                                {
                                    "id": genre_id,
                                    "genre": genre,
                                    "locale": loc,
                                    "display": display or genre,
                                    "dir_name": dir_name,
                                    "lib_slug": lib_slug,
                                    "ok": False,
                                    "status": "failed",
                                    "marker": STEP1_FAIL_MARKER,
                                    "error_file": STEP1_ERROR_FILENAME,
                                    "stage": "parse_normalize",
                                    "recover_strategies": recover_strategies,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ) + "\n",
                            encoding="utf-8",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        write_step_usage_report(
                            out_dir, _usage_rows, step="step1",
                            extra={"recover_strategies": recover_strategies, "failed": True},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    print(f"[FAIL] 解析/归一失败（整步重试 1 次后仍无效）: {e} (raw chars={len(raw)}) → {ep}")
                    return False

            try:
                report = write_step_usage_report(
                    out_dir, _usage_rows, step="step1",
                    extra={"recover_strategies": recover_strategies},
                )
                print_usage_summary(report["summary"], prefix="[step1 tokens]")
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 写出 usage/llm_usage.json 失败: {e}")

        ordered = {"usage": "reference"}
        for k, v in data.items():
            if k != "usage":
                ordered[k] = v
        data = ordered

        sm = {k: len(v) for k, v in summary(data).items()}
        missing = [k for k, n in sm.items() if n == 0]
        if missing:
            ep = _write_step1_error(
                out_dir,
                stage="empty_roles",
                error="parsed JSON missing layers: " + ", ".join(missing),
                genre_id=genre_id,
                genre=genre,
                locale=loc,
                extra={"counts": sm},
            )
            print(f"[FAIL] 缺层 {missing} → {sm} → {ep}")
            return False

        header = (
            f"# 体裁内容参考清单草稿（Role 库 / step1）\n"
            f"# id={genre_id}  dir={dir_name}\n"
            f"# usage=reference：软先验，供 step2 选题与 step3 大纲/stylesheet 查阅；非硬选型。\n"
            f"# 人工审改后移入 config/role_library/{lib_slug}.yaml\n"
            f"# 浏览：同目录 inventory.md（表格）/ tree.md（树）\n"
            f"# 主题：由 step2 写入 step2_themes/themes.xlsx（C1/C2/C3），本步默认不写\n"
            f"# recover={','.join(recover_strategies) if recover_strategies else 'ok'}\n"
        )
        draft.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")

        try:
            problems = validate(data)
        except Exception as e:  # noqa: BLE001
            problems = [f"validate 异常: {type(e).__name__}: {e}"]
        sm = {k: len(v) for k, v in summary(data).items()}
        print(f"[out] {out_dir}")
        print(f"[counts] {sm}")
        print("[validate]", "OK" if not problems else problems)
        lib_ok = not problems

        try:
            inv = write_inventory_md(data, out_dir / "inventory.md")
            print(f"[browse] {inv.name}")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 写出 inventory 失败: {e}")

        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).with_name("viz_role_library.py")),
                 str(draft), str(out_dir / "tree.md")],
                check=False,
            )
            if (out_dir / "tree.md").is_file():
                print("[browse] tree.md")
        except Exception:  # noqa: BLE001
            pass

        if problems:
            ep = _write_step1_error(
                out_dir,
                stage="validate",
                problems=problems,
                genre_id=genre_id,
                genre=genre,
                locale=loc,
                extra={"counts": sm},
            )
            print(f"[FAIL] Role 库校验未通过（已写 {ep.name}，marker={STEP1_FAIL_MARKER}）")
        else:
            _clear_step1_error(out_dir)

    # 始终刷新 meta，便于按 id 追溯
    meta = {
        "id": genre_id,
        "genre": genre,
        "locale": loc,
        "display": display or genre,
        "dir_name": dir_name,
        "lib_slug": lib_slug,
        "ok": bool(lib_ok),
        "status": "ok" if lib_ok else "failed",
        "marker": None if lib_ok else STEP1_FAIL_MARKER,
        "error_file": STEP1_ERROR_FILENAME if not lib_ok else None,
    }
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 写出 meta.json 失败: {e}")

    if theme_count > 0:
        need_themes = overwrite or (not themes_xlsx.exists())
        if not need_themes and skipped_lib:
            print(f"[cache] 主题已存在，跳过：{themes_xlsx}")
        else:
            try:
                ok_t = generate_themes_via_library(
                    out_dir,
                    count=theme_count,
                    genre=genre,
                    locale=locale,
                    overwrite=overwrite or (not themes_xlsx.exists()),
                    temperature=None,
                    max_tokens=min(max_tokens, 16384),
                )
                if not ok_t:
                    print("[warn] 主题生成未成功（不影响 Role 库）")
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 主题起草失败（不影响建库）: {e}")
                try:
                    n = gen_themes(genre, display, locale, out_dir, theme_count)
                    print(f"[themes-fallback] {n} 条（未注入 Role 库上下文）")
                except Exception as e2:  # noqa: BLE001
                    print(f"[warn] 主题 fallback 也失败: {e2}")
    # theme_count==0（默认）：主题由 step2 生成，此处不提示、不校验

    return lib_ok


def main():
    ap = argparse.ArgumentParser(
        description="Step1：起草 Role 库（主题请用 step2 / C1–C3）。"
                    "Excel 批量默认只跑 1 个体裁；--all 跑全部。默认有 draft 则跳过建库。"
    )
    ap.add_argument("--genre")
    ap.add_argument("--display", default="")
    ap.add_argument("--locale", default="zh")
    ap.add_argument("--notes", default="无")
    ap.add_argument("--excel")
    ap.add_argument("--id", type=int, default=None,
                    help="按 genres.xlsx 的 id 跑单条（可省略 --excel，默认 config/genres.xlsx）")
    ap.add_argument("--ids", default="",
                    help="按 id 列表跑多条，如 1,26 或 1-3,26（配合 --excel；默认 config/genres.xlsx）")
    ap.add_argument("--assign-ids", metavar="GENRES_XLSX",
                    help="给已有 genres.xlsx 补写/重写 id=1..N 列后退出")
    ap.add_argument("--build-locales", default="",
                    help="配合 --excel：对每个体裁按此语言列表各建一次库（如 zh,en），忽略行内 locale")
    ap.add_argument("--overwrite", action="store_true",
                    help="强制重跑并覆盖 Role 库（默认：已存在则跳过）")
    ap.add_argument("--limit", type=int, default=1,
                    help="配合 --excel：只跑前 N 个体裁×语言（默认 1；与 --id/--ids 同用时忽略）")
    ap.add_argument("--all", action="store_true",
                    help="配合 --excel：跑全部 enabled 行（忽略 --limit）")
    ap.add_argument("--make-template")
    ap.add_argument("--suggest-genres", metavar="DOMAIN_SCOPE_ZH",
                    help="【单主题】只填中文领域名即可；--example-genres / 英文参数均可选")
    ap.add_argument("--example-genres", default="",
                    help="可选：中文体裁示例锚点（不填则模型自行发散）")
    ap.add_argument("--domain-scope-en", default="",
                    help="可选：英文领域名（单主题模式；不填则用中文主题作英文发散锚点）")
    ap.add_argument("--example-genres-en", default="", help="可选：英文体裁示例锚点")
    ap.add_argument("--domains", default="",
                    help="【多主题】逗号/顿号分隔的中文领域列表，按主题分别发散体裁")
    ap.add_argument("--domains-en", default="",
                    help="可选：与 --domains 顺序对应的英文领域名（逗号分隔）")
    ap.add_argument("--domains-file", default="",
                    help="【多主题】文本文件，每行一个主题；支持「中文」或「中文|英文」")
    ap.add_argument("--scopes-excel", help="【多主题】按领域清单 Excel 批量起草（最完整）")
    ap.add_argument("--make-scopes", help="生成领域清单 Excel 模板")
    ap.add_argument("--count", type=int, default=None,
                    help="step0：中英各自生成条数（默认 20；同时覆盖 --count-zh/--count-en）")
    ap.add_argument("--count-zh", type=int, default=None, help="step0：仅中文体裁条数（默认 20）")
    ap.add_argument("--count-en", type=int, default=None, help="step0：仅英文体裁条数（默认 20）")
    ap.add_argument("--per-round", type=int, default=5,
                    help="step0：每轮 LLM 生成个数（默认 5；用多轮凑满 count）")
    ap.add_argument("--rounds", type=int, default=None,
                    help=argparse.SUPPRESS)  # 旧参数，忽略；改用 --count
    ap.add_argument("--extra-notes", default="",
                    help="step0：中英共用补充说明；若值为已存在文件路径（或 @路径）则读入文件内容")
    ap.add_argument("--extra-notes-zh", default="",
                    help="step0：仅中文轮 extra_notes（优先于 --extra-notes；支持文件路径）")
    ap.add_argument("--extra-notes-en", default="",
                    help="step0：仅英文轮 extra_notes（优先于 --extra-notes；支持文件路径）")
    ap.add_argument("--out", default=None, help="额外把汇总 Excel 复制到此路径")
    ap.add_argument("--temperature", type=float, default=None,
                    help="不填时：建库用 0.4，体裁起草(step0)用 0.95")
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--theme-count", type=int, default=0,
                    help="兼容：>0 时顺带写 themes.xlsx；默认 0（请用 step2 生成 C1/C2/C3）")
    args = ap.parse_args()

    def _counts() -> tuple[int, int]:
        base = args.count if args.count is not None else 20
        cz = args.count_zh if args.count_zh is not None else base
        ce = args.count_en if args.count_en is not None else base
        return cz, ce

    def _notes() -> tuple[str, str, str]:
        shared = _maybe_read_text(args.extra_notes)
        zh = _maybe_read_text(args.extra_notes_zh) or shared
        en = _maybe_read_text(args.extra_notes_en) or shared
        return shared, zh, en

    if args.make_scopes:
        make_scopes_template(Path(args.make_scopes))
        return
    if args.scopes_excel:
        cz, ce = _counts()
        zh_over = cz if (args.count is not None or args.count_zh is not None) else None
        en_over = ce if (args.count is not None or args.count_en is not None) else None
        run_scopes_excel(Path(args.scopes_excel), args.temperature, args.max_tokens,
                         zh_over, en_over, per_round=args.per_round)
        return
    if args.domains or args.domains_file:
        cz, ce = _counts()
        shared, _, _ = _notes()
        if args.domains_file:
            dzh, den = _load_domains_file(Path(args.domains_file))
        else:
            dzh = _parse_domains_arg(args.domains)
            den = _parse_domains_arg(args.domains_en)
        run_domains_list(
            dzh, den or None,
            count_zh=cz, count_en=ce,
            temperature=args.temperature, max_tokens=args.max_tokens,
            per_round=args.per_round, extra_notes=shared,
        )
        return
    if args.suggest_genres:
        cz, ce = _counts()
        shared, notes_zh, notes_en = _notes()
        suggest_genres(args.suggest_genres, args.example_genres, args.domain_scope_en, args.example_genres_en,
                       cz, ce, shared,
                       Path(args.out) if args.out else None, args.temperature, args.max_tokens,
                       per_round=args.per_round,
                       extra_notes_zh=notes_zh, extra_notes_en=notes_en)
        return
    if args.make_template:
        make_template(Path(args.make_template))
        return
    if args.assign_ids:
        rewrite_genres_excel_with_ids(Path(args.assign_ids))
        return

    default_excel = CONFIG_DIR / "genres.xlsx"
    id_list: list[int] = []
    if args.id is not None:
        id_list.append(int(args.id))
    if args.ids:
        id_list.extend(parse_ids_arg(args.ids))
    id_list = sorted(set(id_list))

    excel_path: Path | None = Path(args.excel) if args.excel else None
    if id_list and excel_path is None:
        excel_path = default_excel

    if excel_path is not None:
        if not excel_path.exists():
            ap.error(f"genres Excel 不存在: {excel_path}")
        build_locales = [x.strip() for x in args.build_locales.split(",") if x.strip()] or None
        rows = read_excel(excel_path, build_locales, only_ids=id_list or None)
        total = len(rows)
        if id_list:
            missing = [i for i in id_list if i not in {r["id"] for r in rows}]
            print(f"[ids] 指定 {id_list} → 命中 {len(rows)} 条" + (f"；未命中 {missing}" if missing else ""))
            if missing:
                print("[hint] 若缺 id：先跑 --assign-ids 给表补编号，或检查 enabled=0")
        elif not args.all:
            if args.limit < 1:
                ap.error("--limit 须 ≥ 1（或改用 --all / --ids）")
            rows = rows[: args.limit]
            print(f"[limit] 只跑前 {len(rows)}/{total} 个体裁×语言（默认 1；--all 跑全部；推荐 --ids）")
        else:
            print(f"[all] 跑全部 {total} 个体裁×语言")
        print("[定位] Role 库 = 体裁内容参考清单（usage=reference），非硬选型")
        if args.theme_count > 0:
            print(f"[compat] --theme-count={args.theme_count}：将顺带写 themes.xlsx（正式流程请用 step2）")
        print("[缓存] 默认：draft.yaml 已存在则跳过建库；--overwrite 全覆盖")
        print(f"[产物目录] {DRAFTS_DIR.resolve()}  （{{id:02d}}_{{genre}}_{{locale}}/）")
        if not rows:
            print("[warn] Excel 中无匹配的 enabled 行可跑")
            return
        ok = 0
        for idx, r in enumerate(rows, 1):
            print(f"\n----- ({idx}/{len(rows)}) id={r['id']} {r['genre']}_{r['locale']} -----")
            if run_one(r["genre"], r["display"], r["locale"], r["notes"], args.temperature, args.max_tokens,
                       args.theme_count, overwrite=args.overwrite, genre_id=r["id"]):
                ok += 1
        print(f"\n[done] {ok}/{len(rows)} 校验通过或命中缓存")
        print(f"[产物目录] {DRAFTS_DIR.resolve()}")
        print("  每个体裁：{id:02d}_{genre}_{locale}/meta.json + draft.yaml …")
        print("  主题与 C1/C2/C3：请跑 step2 → step2_themes/themes.xlsx。")
        print("  审改 draft.yaml 后可移入 config/role_library/{genre}_{locale}.yaml。")
        return
    if args.genre:
        run_one(args.genre, args.display, args.locale, args.notes, args.temperature, args.max_tokens,
                args.theme_count, overwrite=args.overwrite, genre_id=args.id)
        return
    ap.error("需指定 --genre / --excel / --id|--ids / --make-template / --assign-ids")


if __name__ == "__main__":
    main()
