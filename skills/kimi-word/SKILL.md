---
name: kimi-word
description: Create, edit, repair, and review Word documents (.docx). Use for docx-js document creation, in-place editing of existing .docx files, comments, tracked changes, Markdown-to-docx conversion with citations, and DOCX package validation/auto-repair.
category: 办公文档
---
# DOCX Skill

## Route

| Task | Route | Read |
|------|-------|------|
| Create a new `.docx` with docx-js (preferred) | write a docx-js script, then `python3 {skill_path}/scripts/docx.py build` | `references/create.md`, then `assets/example.js` |
| Create a simple `.docx` with `python-docx` | generate with python-docx, then `python3 {skill_path}/scripts/docx.py validate` (`build` only accepts `.js`) | `references/create.md` ("python-docx Notes") |
| Create native Word equations | docx-js Math API; hand-written OMML only for matrices | `references/create.md`, `references/omml.md` |
| Edit an existing `.docx` (text/style/tables/images) | working-copy workflow: `python3 {skill_path}/scripts/verify/prep.py file.docx -o work/` → edit `work/unpacked/` → `opc.py pack` → `verify.py --baseline` | `references/editing.md` |
| Read or audit a `.docx` without changing it | `prep.py` produces `view.txt`; `read.py` for slices and raw XML | `references/editing.md` §1 |
| Add / reply to / resolve comments | `python3 {skill_path}/scripts/verify/comment.py` | `references/editing.md` §2 |
| Tracked changes (revision mode) | `python3 {skill_path}/scripts/verify/track.py` | `references/editing.md` §3 |
| Accept / reject existing revisions | `python3 {skill_path}/scripts/verify/revisions.py --accept\|--reject` | `references/editing.md` §3 |
| Markdown with platform citation markers `[^N^]` or a companion `citation.jsonl` → `.docx` | `python3 {skill_path}/scripts/md2docx/md2docx_convert.py` | `references/md2docx.md` |
| Plain Markdown (including standard `[^id]` footnotes) → `.docx` | bare `pandoc` — standard footnotes become real Word footnotes natively | `references/md2docx.md` |
| Validate or repair a `.docx` | `python3 {skill_path}/scripts/docx.py validate <file.docx>` — runs the verify engine (no-baseline: the whole file is in scope) | this file |

**Final gate:** every created or edited `.docx` must pass the verify gate
before delivery. `python3 {skill_path}/scripts/docx.py validate <file.docx>`
is the gate — it delegates to the verify engine
(`scripts/verify/verify.py`, no baseline: the whole file is your product).
For edits, you can scope checks and repairs to what the edit introduced by
calling the engine directly with a baseline:

```bash
# new document or full-file check (also what `docx.py validate` runs)
python3 {skill_path}/scripts/verify/verify.py /absolute/path/output.docx

# after an edit — the baseline comes from prep (references/editing.md §0);
# checks and repairs are scoped to what the edit introduced
python3 {skill_path}/scripts/verify/verify.py /absolute/path/output.docx --baseline /absolute/path/work/baseline.docx
```

The gate auto-repairs deterministic defects and lists each repair; treat the
repair list itself as changes to review (`--no-repair` to only report).
`lxml` is a **hard** requirement for the gate; if it is missing, `pip install
lxml`; if that fails, disclose that the validation gate could not run — there
is no lighter fallback validator.

**When to use `md2docx` (gate):** only when the Markdown carries platform
citation markers (`[^123^]` style) or a companion `citation.jsonl` — bare
pandoc would render those markers as plain text and lose the source mapping.
Standard Markdown footnotes (`[^id]` + `[^id]: Title. Date. URL`) are the
opposite case: send them to **bare pandoc**, which converts them into real
Word footnotes natively; the pipeline would misread them as platform markers.
For a one-off document the user asks you to write, author it natively with
`docx-js` (the Create route); hand-authored layout is cleaner than converted
Markdown. See `references/md2docx.md`.

## Dependencies

| Purpose | Requirement | Notes |
|---|---|---|
| Create route | Node ≥18 + `docx` npm package installed in the script's project directory (preferred), or `python-docx` | at least one; check with `python3 {skill_path}/scripts/docx.py check-docx <script-dir>` |
| Edit route | python3 + `lxml` | working-copy workflow with bundled `verify/` scripts; python-docx may be mixed in on the working copy but is not required |
| Validation gate (`docx.py validate` / `verify.py`) | python3 + `lxml` | **hard**; if missing, `pip install lxml`; if that fails, disclose that the gate could not run |
| md2docx pipeline | `pandoc`, python3, `python-docx`, `lxml` | needed only for that route |
| Visual QA render | LibreOffice (`soffice`) + managed `pypdfium2`/`Pillow` | optional; model-written render step, skip and disclose when absent (Hard Rules) |

## Skill Map

```
docx/
├── SKILL.md                 # routing, dependencies, validation contract
├── references/
│   ├── create.md            # creation standard: pitfalls, JS skeleton, TOC, visual defaults, few-shots
│   ├── editing.md           # existing-document edits, comments, tracked changes
│   ├── omml.md              # native Word equation patterns and ordering traps
│   └── md2docx.md           # citation-marked Markdown → docx pipeline
├── assets/
│   └── example.js           # small build-tested docx-js seed
└── scripts/
    ├── docx.py              # build / validate / lint / md2docx / check-docx entry point (pure Python)
    ├── lint_docx_js.py      # static checks for docx-js authoring mistakes
    ├── md2docx/             # citation-marked Markdown → docx pipeline
    ├── verify/              # validation engine + editing workflow (prep/read/track/comment/revisions/opc) + the single shared OOXML schema set
    └── tests/               # unittest regression suite: python3 -m unittest tests.test_docx_tooling
```

Progressive disclosure:
- New document creation: read `references/create.md` first, then inspect `assets/example.js` before writing code.
- Native equation requirements: also read `references/omml.md`; keep math order explicit.
- Existing `.docx` edits: read `references/editing.md`; do not read creation docs unless rebuilding from scratch.
- Validation failures: use the error text first; read scripts only when changing the skill itself.

## Required Commands

Use the bundled entry point; do not stop after running `node` manually.
`{skill_path}` is the directory containing this SKILL.md — resolve it from this
file's own location.

```bash
# Build from a Node script. The script must write process.argv[2].
python3 {skill_path}/scripts/docx.py build /absolute/path/create.js /absolute/path/output.docx

# Validate and auto-repair an existing DOCX package (the verify engine,
# no-baseline full mode; same gate that `build` runs at the end).
python3 {skill_path}/scripts/docx.py validate /absolute/path/file.docx

# Run only docx-js static checks.
python3 {skill_path}/scripts/docx.py lint /absolute/path/create.js

# Detect whether the docx npm package resolves from the script directory.
python3 {skill_path}/scripts/docx.py check-docx /absolute/path/dir-containing-create-js

# Pre-delivery final gate for edit-scope checking (auto-repair + report).
python3 {skill_path}/scripts/verify/verify.py /absolute/path/output.docx
```

`build` runs: JavaScript syntax check -> docx-js lint -> Node generation -> DOCX auto-fix/validation.

`docx-js` does not provide an OpenXML SDK-style official validator. The validation stack is `node --check`, docx-js lint, docx-js generation, and the verify engine's bundled OOXML/OPC XSD validation plus package checks. `docx.py validate` and `verify.py` are the same engine; `build` already runs it, so a separate call is only needed for files produced outside `build`.

Use `python3 {skill_path}/scripts/docx.py check-docx <script-dir>` before building. If it reports `MISSING`, run the printed `cd ... && npm install docx` in the script's project directory, then rerun the check — Node resolves bare imports from the script directory upward, so a global or unrelated install is not visible to the build. `build` also handles ESM `.js` scripts even in a CommonJS workspace; do not edit `package.json` just to switch module type.

For Word tasks, the final artifact must be `.docx`. Markdown, extracted text, and `read_file` output are intermediate notes, not deliverables, unless the user explicitly asks for Markdown.

## Auto-Fix And Validation

`docx.py validate` runs the verify engine: it unpacks the DOCX, applies
deterministic mechanical repairs first (listed one by one), then runs bundled
XSD validation plus semantic package checks, and exits 0 only when the file
passes. Treat `validate` as a mutating repair pass on the final artifact, not
a read-only checker.

Output contract:
- `· <part>: <repair> ×N` lines followed by `applied N mechanical repair(s)`: deterministic auto-repairs already applied.
- `⚠ ...`: warnings — likely compatible but worth reviewing.
- `✗ ...`: violations — the script or package still needs correction; exit 1.
- `PASSED` / `FAILED` summary line; exit code 0 pass, 1 violations, 2 usage/input errors.

Auto-repairs (deterministic, listed when applied):
- WordprocessingML structure ordering: `sectPr` placement, property containers (`pPr`/`rPr`/`tcPr`/`tblPr` etc.) restored to schema sequence
- default-template quirks such as `w:zoom` missing `w:percent`, missing `xml:space="preserve"`, colorless solid shading, literal newlines
- out-of-range comment `paraId`/`durableId` values refreshed to valid ids

Hard checks:
- bundled XSD validation for package XML parts, with `mc:Ignorable` extensions filtered for schema compatibility
- `[Content_Types].xml` coverage and entry-point content type
- relationship graph: dangling or nonexistent targets, duplicate relationship IDs
- comment integrity: duplicate comment IDs, commentRange start/end pairing, references to nonexistent comments, commentsExtended/commentsIds/commentsExtensible chain consistency
- duplicate bookmark start/end ID pairing
- CJK runs with explicit fonts but no `w:eastAsia` (warning)

The repair pass does not reorder sequence-sensitive mixed content such as runs, fields, hyperlinks, comments/bookmarks, tracked changes, drawings, `mc:AlternateContent`, or OMML math.

## Hard Rules

1. Creation uses docx-js by default; `python-docx` is acceptable for simple documents (see `references/create.md`).
2. Existing-document edits go through the working-copy workflow (`prep.py` → edit `work/unpacked/` → `opc.py pack` → `verify.py --baseline`); the original file is never rebuilt from scratch unless the user asks for a redesign.
3. Comments go through `comment.py`, tracked changes through `track.py`, revision accept/reject through `revisions.py`; do not hand-craft comment or revision XML.
4. In docx-js creation scripts, follow the standard `run(...)` / `para(...)` full-form pattern in `references/create.md`.
5. Formal documents should include a native table of contents unless inappropriate.
6. Use explicit `BookmarkStart`/`BookmarkEnd` for internal link targets; avoid `Bookmark` when there is more than one target.
7. Use absolute paths in scripts and commands.
8. You may inspect skill scripts and installed packages to understand API behavior. Do not patch the skill directory, runtime domain code, or global npm package files as a task fix; fix your generation/edit script, install dependencies in the script/project directory, or use a compatible API pattern.
9. After every create/edit operation, the artifact must pass the validation gate: `docx.py build` runs it automatically; for anything produced outside `build` (python-docx output, edits repacked via `opc.py pack`), run `python3 {skill_path}/scripts/docx.py validate` before delivery. `validate` and `verify.py` are the same engine — no ordering question, no second gate.
10. A validation `✗` violation (FAILED, exit non-zero) is a blocker. Do not claim it is harmless; fix it or report the document as not cleanly validated. `⚠` warnings may be disclosed separately.
11. When LibreOffice is available, convert the `.docx` to PDF and render every page for visual QA (see `references/create.md`). If no renderer is available, disclose that visual QA could not be run; never call structure-only validation a rendered visual pass.

## Delivery Checklist

- The final `.docx` exists at the requested path.
- `python3 {skill_path}/scripts/docx.py build` or `python3 {skill_path}/scripts/docx.py validate` passes (both run the verify engine; if the gate could not run — e.g. no `lxml` — that is disclosed).
- No placeholders remain.
- Output filename matches the topic and the user's language; do not deliver `output.docx`.
- Headers/footers and page numbers are present for formal documents unless inappropriate.
- Formal documents include a TOC unless inappropriate.
- Citations, if used, are real and attached to precise claims with native footnotes.
- When a renderer is available, the rendered contact sheet and every low-content warning have been reviewed.
