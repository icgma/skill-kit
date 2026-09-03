# md2docx: Citation-Marked Markdown → Word

Convert Markdown that carries **platform citation markers** into a Word document with real footnotes, endnotes, or hyperlink cross-references. For editing an existing docx, see the route table in SKILL.md; this page only covers the case where **Markdown is the finished source**.

## When To Use This Pipeline (wide gate, no hard wall)

- The Markdown contains platform citation protocol markers `[^123^]` or a companion `citation.jsonl` — **this case must use the pipeline**: bare pandoc renders the markers as plain text and loses the source mapping.
- **Markdown without `[^N^]` markers and without a companion `citation.jsonl` goes straight to bare pandoc — do not enter this pipeline.** In particular `[^1]` plus a leading `[^1]: definition` is a *standard Markdown footnote*: bare pandoc converts it into a real Word footnote, while this pipeline would hijack it as a "marker missing its right caret" — the definition line becomes an orphan body paragraph and the footnote text gets replaced by the database URL. The T2 compatibility layer only serves "platform marker missing its caret **and** a database is attached".
- An upstream workflow has delivered a finished `final.md` (Deep Research, orchestrator/swarm handoff, or the user names md2docx) — treat the Markdown as final content; do not redo research, rewrite prose, or alter source IDs.
- Plain Markdown without citations does not need this pipeline — pandoc directly (see `create.md`).

**Known gap:** for standard Markdown, pandoc can only emit **footnotes**. The endnote/hyperlink styles exist only in this pipeline, which requires platform markers. If a standard-Markdown job explicitly needs endnotes, convert with pandoc first, then handle the footnote→endnote move as a follow-up task (not covered by the bundled scripts).

Run from the skill directory, or resolve `scripts/md2docx/md2docx_convert.py` against it.

## Quick Start

```bash
python3 {skill_path}/scripts/md2docx/md2docx_convert.py <file.md> \
    --citation <citation.jsonl> \
    --style footnote \
    --output-dir <output-directory>
```

Parameters: `md_file` (required) · `--citation` (required, jsonl path) · `--style` (`footnote`/`endnote`/`hyperlink`, default `footnote`) · `--output-dir` (specify explicitly for the environment).

## Style Selection

| style | Scenario | Citation location |
|---|---|---|
| **footnote** (default) | Research reports, policy analysis, general reports | Page bottom (per page) |
| **endnote** | Academic papers, books, long scholarly documents | Collected at document end |
| **hyperlink** | WPS compatibility needs | Reference list at end of body |

## citation.jsonl Convention

One JSON object per line: `{"id": 123, "url": "https://example.com", "page": {"site_name": "Example Site"}}`.

- When a `.citation.jsonl` / `citation.jsonl` ships alongside the Markdown, **pass that file itself**.
- In agent container environments the default is `/mnt/agents/.store/citation.jsonl` — use it when it exists.
- For local regression fixtures or exported workspaces, use the jsonl paired with the md.

## Citation Format Auto-Detection (decreasing priority)

| Tier | Pattern | Confidence | Behavior |
|---|---|---|---|
| T1 | `[^123^]` | High | Convert directly |
| T2 | `[^123]` | Medium | Compat conversion + WARNING |
| T3 | `[123]` | Low | Requires cross-validation against citation_db (convert only when hit rate >50% and >5 hits) |

## Pre-Flight Checks

1. **Image paths**: agent-produced Markdown often contains container absolute paths that may not exist in the current environment. First run `grep -oE '!\[.*?\]\(.*?\)' file.md`, and fix unreachable paths to locally resolvable relative paths (or drop the image) — pandoc aborts when an image path does not exist.
2. **UTF-8 gate**: the converter rejects invalid UTF-8 and latin-1 double-encoded CJK Markdown before pandoc runs — fix or regenerate the md; do not force a degraded conversion.

## Pipeline Internals (no manual intervention needed)

```
md + citation.jsonl
 ├─ 1. Citation format detection (T1→T2→T3 tiered fallback)
 ├─ 2. Renumber by first appearance → ^N^ superscripts
 ├─ 3. Edge-case detection (non-numeric citations → WARNING only)
 ├─ 4. pandoc → base.docx
 ├─ 4.5 OMML normalization (fixes two schema quirks in pandoc's math output)
 └─ 5. OOXML post-processing (branches by style)
      ├─ footnote:  first use → real footnote object, repeats → NOTEREF cross-reference
      ├─ endnote:   first use → real endnote object,  repeats → NOTEREF
      └─ hyperlink: REF field → bibliography bookmark
```

Three outputs: `{name}.{style}.docx` (the deliverable), `{name}.converted.md` (intermediate; citations already converted to `^N^` superscripts), `{name}.base.docx` (pandoc raw output, intermediate).

Behavior details: source IDs are only for database lookup — never altered; the note numbers in Word are display numbers renumbered by first appearance; the same ID cited twice produces one note, with later uses as NOTEREF; superscript note numbers are clickable and jump to their footnote/endnote; non-numeric IDs (`[^Insight6^]`) are escaped in the intermediate md and their marker text stays visible; **IDs not found in the database keep their original markers and produce per-line WARNINGs — evidence pointers are never silently deleted.**

## Hard Rules

**Every WARNING/ERROR is handled, never swallowed.** Each WARNING is either resolved or reported with the deliverable; errors are fixed on the working copy and the conversion rerun until clean. Never fabricate mappings, delete markers, or present a failed conversion as a final result.

## Acceptance (Two Gates)

1. The converter's own output is clean (no unhandled WARNING/ERROR).
2. `python3 {skill_path}/scripts/verify/verify.py <output>.docx` (the same final gate as every new document — see `create.md`).

Known exemptions: after pandoc emits `base.docx`, the pipeline automatically normalizes two upstream OMML quirks (`m:nor` coexisting with `m:sty` — deduplicated — and `m:mcPr` child ordering; both are texmath behaviors that strict schema and Microsoft's OpenXML SDK judge invalid) — math output should pass validation directly. If new `m:`-class violations appear (an unseen new quirk), record and disclose them with the delivery; `w:`-class violations must converge.

## Regression Tests

The regression suite (12 real fixtures + UTF-8 rejection gate + verify gate) lives in the skill's upstream repository under `tests/md2docx/` and is **not** distributed with the skill package; run the upstream regressions before changing the pipeline itself.
