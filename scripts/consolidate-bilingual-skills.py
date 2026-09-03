#!/usr/bin/env python3
"""Consolidate paired English and Chinese skills into single bilingual skill directories."""

import os
import re
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
PAIRS_JSON = ROOT / "scripts" / "skill_pairs.json"
MANIFEST_JSON = ROOT / "scripts" / "bilingual_manifest.json"

ADDITIONAL_PAIRS = [
    {"zh_id": "adhd-daily-planner", "en_id": "adhd-assistant", "reason": "semantic match", "score": 1.0},
    {"zh_id": "journalistic-portrait-cn", "en_id": "journalistic-portrait", "reason": "suffix -cn", "score": 1.0},
    {"zh_id": "stock-research-report-cn", "en_id": "stock-research-report", "reason": "suffix -cn", "score": 1.0},
    {"zh_id": "sop-writer", "en_id": "process-doc", "reason": "SOP and business process", "score": 1.0},
]

def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", text)
    if not m:
        return {}, "", text
    meta: dict[str, any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if v == "" and i + 1 < len(lines) and lines[i + 1].strip().startswith("-"):
                # list in yaml
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith("-"):
                    items.append(lines[i].strip()[1:].strip().strip("'\""))
                    i += 1
                meta[k] = items
                continue
            meta[k] = v
        i += 1
    return meta, m.group(0), text[m.end():]

def format_frontmatter(meta: dict, body: str) -> str:
    lines = ["---"]
    # Order keys nicely
    preferred = ["name", "description", "category", "license", "aliases", "argument-hint", "tags"]
    seen = set()
    for k in preferred:
        if k in meta:
            val = meta[k]
            if isinstance(val, list):
                lines.append(f"{k}:")
                for item in val:
                    lines.append(f"  - {item}")
            else:
                lines.append(format_field(k, str(val)))
            seen.add(k)
    for k, val in meta.items():
        if k not in seen:
            if isinstance(val, list):
                lines.append(f"{k}:")
                for item in val:
                    lines.append(f"  - {item}")
            else:
                lines.append(format_field(k, str(val)))
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\r\n")

def format_field(key: str, value: str) -> str:
    if key == "description":
        if "\n" in value:
            return f"{key}: >-\n  " + value.replace("\n", "\n  ")
        if any(c in value for c in [":", "{", "}", "[", "]", ",", "&", "*", "#", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`"]):
            safe = value.replace('"', '\\"')
            return f'{key}: "{safe}"'
        return f"{key}: {value}"
    if any(c in value for c in [":", "#", "{", "}"]):
        safe = value.replace('"', '\\"')
        return f'{key}: "{safe}"'
    return f"{key}: {value}"

def main():
    dry_run = "--dry-run" in sys.argv

    if not PAIRS_JSON.is_file():
        print(f"Error: {PAIRS_JSON} not found.", file=sys.stderr)
        return 1

    with open(PAIRS_JSON, "r", encoding="utf-8") as f:
        pairs = json.load(f)

    # Add extra pairs
    existing_zh = {p["zh_id"] for p in pairs}
    for extra in ADDITIONAL_PAIRS:
        if extra["zh_id"] not in existing_zh:
            pairs.append(extra)

    print(f"Total pair mappings to consolidate: {len(pairs)}")

    consolidated = []
    skipped = []

    for item in pairs:
        zh_id = item["zh_id"]
        en_id = item["en_id"]

        zh_dir = SKILLS_DIR / zh_id
        en_dir = SKILLS_DIR / en_id

        if not zh_dir.is_dir():
            skipped.append((zh_id, en_id, f"ZH dir {zh_dir} missing"))
            continue
        if not en_dir.is_dir():
            skipped.append((zh_id, en_id, f"EN dir {en_dir} missing"))
            continue

        zh_skill_md = zh_dir / "SKILL.md"
        en_skill_md = en_dir / "SKILL.md"

        if not zh_skill_md.is_file() or not en_skill_md.is_file():
            skipped.append((zh_id, en_id, "Missing SKILL.md in one of the directories"))
            continue

        zh_raw = zh_skill_md.read_text(encoding="utf-8", errors="replace")
        en_raw = en_skill_md.read_text(encoding="utf-8", errors="replace")

        zh_meta, _, zh_body = parse_frontmatter(zh_raw)
        en_meta, _, en_body = parse_frontmatter(en_raw)

        # Merge aliases into ZH frontmatter
        aliases = zh_meta.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        if en_id not in aliases and en_id != zh_id:
            aliases.append(en_id)
        zh_meta["aliases"] = aliases

        # Ensure category is present in ZH meta
        if not zh_meta.get("category") and en_meta.get("category"):
            zh_meta["category"] = en_meta["category"]

        # Ensure en_meta has category
        if not en_meta.get("category") and zh_meta.get("category"):
            en_meta["category"] = zh_meta["category"]

        # Copy over extra companion files from EN dir to ZH dir if not present
        copied_files = []
        for root, dirs, files in os.walk(en_dir):
            rel_dir = Path(root).relative_to(en_dir)
            for f in files:
                if f in ("SKILL.md", ".DS_Store", "Thumbs.db") or f.endswith(".pyc"):
                    continue
                src_file = Path(root) / f
                dst_file = zh_dir / rel_dir / f
                if not dst_file.exists():
                    copied_files.append(str(rel_dir / f))
                    if not dry_run:
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)

        if not dry_run:
            # 1. Update ZH SKILL.md with aliases
            updated_zh = format_frontmatter(zh_meta, zh_body)
            zh_skill_md.write_text(updated_zh, encoding="utf-8", newline="\n")

            # 2. Write EN SKILL.md as SKILL.en.md in ZH dir
            updated_en = format_frontmatter(en_meta, en_body)
            (zh_dir / "SKILL.en.md").write_text(updated_en, encoding="utf-8", newline="\n")

            # 3. Remove EN directory
            shutil.rmtree(en_dir)

        consolidated.append({
            "canonical_id": zh_id,
            "alias_id": en_id,
            "name_zh": zh_meta.get("name", zh_id),
            "name_en": en_meta.get("name", en_id),
            "category": zh_meta.get("category", ""),
            "copied_files": copied_files
        })
        print(f"Consolidated: {zh_id:32} <= {en_id:30} (extra files: {len(copied_files)})")

    print(f"\nDone! Successfully consolidated: {len(consolidated)}, Skipped: {len(skipped)}")
    if skipped:
        print("Skipped items:")
        for z, e, reason in skipped:
            print(f"  {z} <-> {e}: {reason}")

    if not dry_run:
        with open(MANIFEST_JSON, "w", encoding="utf-8") as f:
            json.dump(consolidated, f, ensure_ascii=False, indent=2)
        print(f"Manifest written to {MANIFEST_JSON}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
