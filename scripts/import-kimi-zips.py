#!/usr/bin/env python3
"""Import Kimi skill zip volumes into skills/."""

from __future__ import annotations

import os
import re
import sys
import zipfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
ZIPS = [
    os.path.join(ROOT, "Kimi全部技能-第1卷.zip"),
    os.path.join(ROOT, "Kimi全部技能-第2卷.zip"),
]

SKIP_NAMES = {".ds_store", "thumbs.db"}
SKIP_EXT = {".pyc", ".dll"}
SKIP_DIR_PARTS = {"__pycache__"}
SKIP_IDS = {"_fallback"}

CATEGORIES = [
    "视听创作",
    "图文创作",
    "学术工作",
    "数据方法",
    "商业金融",
    "工程研发",
    "办公文档",
    "营销增长",
    "法律合规",
    "生活效率",
    "技能工具",
]

RULES: list[tuple[str, list[str]]] = [
    ("视听创作", [
        "video", "podcast", "audio", "speech-synt", "screenplay", "drama",
        "lyrics", "tts", "edge-tts", "short-video", "audio_generation",
        "musepool", "keynote",
    ]),
    ("图文创作", [
        "wechat", "xhs", "zhihu", "copywrit", "copy-edit", "copy-editor",
        "creative-writing", "fiction", "poetry", "newsletter", "x-thread",
        "humanizer", "longread", "essay", "fanfiction", "game-writing",
        "murder-mystery", "trpg", "journalistic", "photo-magazine",
        "general-writing", "letter", "rhetoric", "translation-craft",
        "xindaya", "audience-adapt", "content-research", "report-writing",
        "work-recap", "work-report", "email-newsletter", "html-email",
        "html-mail", "pro-email", "professional-email", "customer-reply",
        "support-response", "ad-copy",
    ]),
    ("学术工作", [
        "paper", "thesis", "cite", "scholar", "academic", "research-advisor",
        "scientific-problem", "bloom-quiz", "sci-paper", "mba-thesis",
        "paper-review", "paper-writing", "astro-observation", "research-writer",
        "research-paper", "scholarly", "ref-style", "cite-style", "interactive-research",
    ]),
    ("数据方法", [
        "regression", "dataset", "data-viz", "chart-gen", "chart-image",
        "sql-", "auto-stat", "outlier", "corr-", "correlation", "hypothesis",
        "infographic", "weighted-scor", "auto-stat", "auto-hypothesis",
        "code-to-chart", "code-to-diagram", "baoyu-infographic", "risk-heatmap",
    ]),
    ("商业金融", [
        "stock", "equity", "finance", "financial", "caixin", "wind", "ifind",
        "etf", "trading", "valuation", "cashflow", "dcf", "invest",
        "yahoo_finance", "imf", "world_bank", "sec_edgar", "commodity",
        "commodit", "fund-risk", "earnings", "cn-finance", "value-invest",
        "vc-industry", "primary-market", "market-insight", "market-research",
        "saas-analyzer", "saas-metrics", "discounted-cash", "tianyancha",
        "igo_open", "event-etf", "strategy-backtest",
    ]),
    ("工程研发", [
        "backend", "webapp", "code-", "git-", "gitlab", "k8s", "kubectl",
        "terraform", "tdd", "test-", "api-", "refactor", "vuln", "security",
        "playwright", "rust-browser", "ddd-", "pipeline", "vibecoding",
        "programming-tutor", "code-mentor", "dev-guide", "deep-module",
        "database-", "http-load", "log-diagnostic", "log-error", "repo-audit",
        "secure-code", "software-testing", "sprint-plan", "story-map",
        "user-story", "idea-to-prd", "product-spec", "interface-design",
        "ui-blueprint", "design-system", "landing-page", "lp-proto",
        "route-to-openapi", "conventional-commit", "smart-commit",
        "py-perf", "storage-analyzer", "swarm", "whatsapp", "locale-guard",
        "localization", "incident-retro", "incident-review", "kimi-widget",
        "kimi-design", "theme-factory", "theme-kit", "nuwa",
    ]),
    ("办公文档", [
        "excel", "xlsx", "docx", "pdf", "ppt", "slides", "gantt",
        "kimi-excel", "kimi-pdf", "kimi-word", "kimi-slides", "guizang-ppt",
        "business-plan-ppt", "geo-magazine", "obsidian", "process-doc",
        "sop-writer", "structured-minutes", "meeting-recap", "email-to-calendar",
        "email-manager", "imap-smtp", "chrono-flow", "timeline-builder",
        "pitch-deck",
    ]),
    ("营销增长", [
        "ad-creative", "campaign", "seo", "churn", "pricing", "brand-nam",
        "competitor", "retention", "ecom-", "marketing", "competitive-seo",
        "split-test",
    ]),
    ("法律合规", [
        "legal", "tos-", "yuandian", "iso-27001", "compliance", "regulatory",
    ]),
    ("生活效率", [
        "adhd", "cv-", "resume", "interview", "okr", "anki", "flashcard",
        "sun-path", "sunlight", "fashion", "workload", "about-me",
        "iteration-planner",
    ]),
    ("技能工具", [
        "kimi-find", "kimi-skills", "kimi-help", "browse", "scraper",
        "skill-creator", "batch-download", "r2-upload", "image_generation",
        "deep-research", "fast-browser", "deep-probe", "skill-kit",
        "cross-examine", "cross-platform",
    ]),
]


def decode_name(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        return name


def should_skip_member(path: str) -> bool:
    parts = path.lower().replace("\\", "/").split("/")
    if any(p in SKIP_DIR_PARTS for p in parts):
        return True
    base = os.path.basename(path).lower()
    if base in SKIP_NAMES:
        return True
    ext = os.path.splitext(base)[1]
    if ext in SKIP_EXT:
        return True
    return False


def find_packages(zf: zipfile.ZipFile) -> dict[str, str]:
    skill_dirs: set[str] = set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        n = decode_name(info)
        if os.path.basename(n) != "SKILL.md":
            continue
        skill_dirs.add(os.path.dirname(n).replace("\\", "/"))

    packages: dict[str, str] = {}
    for d in skill_dirs:
        parent = os.path.dirname(d)
        nested = False
        while parent and parent not in (".", "/"):
            if parent in skill_dirs:
                nested = True
                break
            parent = os.path.dirname(parent)
        if nested:
            continue
        sid = os.path.basename(d)
        if sid in SKIP_IDS:
            continue
        packages[d] = sid
    return packages


def extract_package(zf: zipfile.ZipFile, prefix: str, dest: str) -> int:
    os.makedirs(dest, exist_ok=True)
    count = 0
    prefix = prefix.rstrip("/") + "/"
    for info in zf.infolist():
        n = decode_name(info)
        if n.rstrip("/") == prefix.rstrip("/"):
            continue
        if not (n.startswith(prefix) or n.rstrip("/") == prefix.rstrip("/")):
            continue
        rel = n[len(prefix):]
        if not rel or should_skip_member(rel):
            continue
        out = os.path.join(dest, *rel.split("/"))
        if info.is_dir() or n.endswith("/"):
            os.makedirs(out, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with zf.open(info) as src, open(out, "wb") as dst:
            dst.write(src.read())
        count += 1
    return count


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", text)
    if not m:
        return {}, "", text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip("'\"")
    return meta, m.group(0), text[m.end():]


def classify(skill_id: str, description: str) -> str:
    blob = f"{skill_id} {description}".lower()
    tokens = set(re.findall(r"[a-z0-9_]+", blob))
    sid = skill_id.lower()
    for cat, keys in RULES:
        for k in keys:
            k = k.lower()
            if "-" in k or "_" in k:
                if k in blob:
                    return cat
            elif sid == k or sid.startswith(k + "-") or k in tokens:
                return cat
    return "技能工具"


def inject_category(skill_md: str, category: str) -> None:
    text = open(skill_md, "r", encoding="utf-8", errors="replace").read()
    meta, fm, body = parse_frontmatter(text)
    meta["category"] = category
    if "name" not in meta:
        meta["name"] = os.path.basename(os.path.dirname(skill_md))
    lines = ["---"]
    order = ["name", "description", "license", "category", "tags"]
    seen = set()
    for k in order:
        if k in meta:
            lines.append(f"{k}: {meta[k]}" if k != "description" else format_desc(k, meta[k]))
            seen.add(k)
    for k, v in meta.items():
        if k not in seen:
            lines.append(f"{k}: {v}")
    lines.append("---")
    new = "\n".join(lines) + "\n" + body.lstrip("\n")
    open(skill_md, "w", encoding="utf-8", newline="\n").write(new)


def format_desc(key: str, value: str) -> str:
    if value.startswith('"') or ":" in value or value.startswith("'"):
        if not (value.startswith('"') and value.endswith('"')):
            value = '"' + value.replace('"', '\\"') + '"'
        return f"{key}: {value}"
    return f"{key}: {value}"


def main() -> int:
    existing = {name for name in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, name))}
    chosen: dict[str, tuple[str, str, int]] = {}
    # id -> (zip, prefix, file_count estimate)
    for zip_path in ZIPS:
        if not os.path.isfile(zip_path):
            print("missing", zip_path, file=sys.stderr)
            return 1
        zf = zipfile.ZipFile(zip_path)
        pkgs = find_packages(zf)
        sizes = defaultdict(int)
        for info in zf.infolist():
            n = decode_name(info)
            for prefix in pkgs:
                if n.startswith(prefix.rstrip("/") + "/") or n.rstrip("/") == prefix:
                    sizes[prefix] += info.file_size
        for prefix, sid in pkgs.items():
            score = sizes[prefix]
            prev = chosen.get(sid)
            if prev is None or score > prev[2]:
                chosen[sid] = (zip_path, prefix, score)
        zf.close()

    print(f"packages: {len(chosen)}")
    imported = 0
    skipped_keep = 0
    by_cat = defaultdict(list)

    zip_cache: dict[str, zipfile.ZipFile] = {}
    try:
        for sid, (zip_path, prefix, _score) in sorted(chosen.items()):
            dest = os.path.join(SKILLS, sid)
            if sid in existing and os.path.isfile(os.path.join(dest, "SKILL.md")):
                # Refresh files from zip but keep folder
                pass
            zf = zip_cache.get(zip_path)
            if zf is None:
                zf = zipfile.ZipFile(zip_path)
                zip_cache[zip_path] = zf
            n = extract_package(zf, prefix, dest)
            skill_md = os.path.join(dest, "SKILL.md")
            if not os.path.isfile(skill_md):
                print("no SKILL.md after extract:", sid)
                continue
            raw = open(skill_md, "r", encoding="utf-8", errors="replace").read()
            meta, _, _ = parse_frontmatter(raw)
            cat = classify(sid, meta.get("description", ""))
            inject_category(skill_md, cat)
            by_cat[cat].append(sid)
            imported += 1
            print(f"{cat:8}  {sid:40}  {n:4} files")
    finally:
        for zf in zip_cache.values():
            zf.close()

    print("\n=== counts ===")
    for cat in CATEGORIES:
        print(f"{cat}: {len(by_cat[cat])}")
    extra = [c for c in by_cat if c not in CATEGORIES]
    for c in extra:
        print(f"{c}: {len(by_cat[c])}")
    print("imported", imported, "skipped_keep", skipped_keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
