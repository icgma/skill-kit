---
name: kimi-pdf
description: Create and process PDF files. Creation covers reports, papers, and documents with charts, tables, math formulas, and code listings. Processing covers text/table extraction, form filling, page operations (merge, split, rotate, crop), and Office-to-PDF conversion. Use when the user mentions PDF, .pdf, asks to extract/merge/fill PDF files, or explicitly requests LaTeX (.tex).
category: 办公文档
---
## Route Selection

| Route | Trigger | Route File |
|-------|---------|------------|
| **HTML** (preferred for creation) | PDF creation when Node.js + Playwright + Chromium are available | `routes/html.md` |
| **ReportLab** (fallback) | PDF creation without a browser stack; simple or one-off PDFs | `routes/reportlab.md` |
| **LaTeX** | User explicitly requests LaTeX, .tex, or Tectonic | `routes/latex.md` |
| **md2pdf** | Swarm-assembled Markdown (`*.agent.final.md` / `*_sec{NN}.md`) with standard footnotes | `routes/md2pdf.md` |
| **Process** | Work with existing PDFs (extract, merge, fill forms, Office→PDF) | `routes/process.md` |

**Creation route choice:** prefer the **HTML** route (Playwright + Paged.js) when the
environment already has Node.js, Playwright, and Chromium — it gives the
highest-fidelity layout. If they are not available, use **ReportLab**; do not
install the browser stack just for one PDF. Use **LaTeX** only when the user
explicitly asks for it.

**Output path:** write to the path the user requested. If its directory is not
writable (e.g. a container path like `/mnt/...` that does not exist here), fall
back to `./outputs/` under the current working directory, create it, and report
the final path. Never fail just because a hardcoded absolute directory is missing.

**Markdown handoffs:**
- Swarm-assembled Markdown with standard footnotes (`[^id]` + `[^id]: Title. Date. URL`) → the `md2pdf` route (gate below).
- Markdown with platform citation markers (`[^123^]`) plus a `citation.jsonl`: treat the Markdown as canonical content and convert through a creation route (HTML preferred). Match each numeric marker to the JSONL object with the same `id`; render citations in whatever form fits the document; preserve unresolved markers and warn — never invent or silently drop a source. If no JSONL is supplied but `/mnt/agents/.store/citation.jsonl` exists, use it.
- Any other Markdown → a creation route (HTML preferred, else ReportLab): preserve the source content, and do not use the swarm md2pdf converter for this path.

**When to use `md2pdf` (gate):** only for a multi-section Markdown report
assembled by a swarm writing skill — i.e. there is a `*.agent.final.md` or
several `*_sec{NN}.md` files produced by sub-agents, with standard Markdown
footnotes (`[^id]` + `[^id]: Title. Date. URL`) for citations.

### MANDATORY: Read Route File Before Implementation

**Before implementation, you MUST:**
1. Determine the route (HTML / ReportLab / LaTeX / md2pdf / Process)
2. **Read the route file** (`routes/html.md`, `routes/reportlab.md`, `routes/latex.md`, `routes/md2pdf.md`, or `routes/process.md`)
3. Only then proceed with implementation

This file (SKILL.md) contains constraints and principles. Route files contain **how-to details**.

`{skill_path}` in route files means the skill root — the directory containing
this SKILL.md. Resolve it from this file's own location.

### Decision Rules

| User Says | Route |
|-----------|-------|
| "Create a PDF", "Make a report", "Write a paper" | HTML if the browser stack is present, else ReportLab |
| "Convert this regular Markdown file to PDF" | HTML if available, else ReportLab |
| "Convert this swarm-assembled `*.agent.final.md` to PDF" | md2pdf |
| "Use LaTeX", "Compile .tex", "Use Tectonic" | LaTeX |
| "Extract text from PDF", "Merge these PDFs", "Fill this form" | Process |
| "Convert this DOCX/PPTX/XLSX to PDF" | Process (`convert`, needs LibreOffice) |

---

## Dependencies

| Route | Libraries |
|-------|-----------|
| **HTML** | Node.js + Playwright + Chromium — use only when already present; do not install on the spot |
| **ReportLab** | `pip install reportlab matplotlib` |
| **LaTeX** | Tectonic single binary; install per `routes/latex.md` only when this route is chosen |
| **md2pdf** | `pip install markdown2 xhtml2pdf` (pure Python, reuses ReportLab) |
| **Process** | `pip install pikepdf pdfplumber`; Office→PDF also needs LibreOffice |
| **Visual QA** | Managed `pypdfium2` + `Pillow` |

---

## Core Constraints (Must Follow)

### 1. Output Language
**Output language must match user's query language.**
- User writes in Chinese → PDF content in Chinese
- User writes in English → PDF content in English
- User explicitly specifies language → Follow user's specification

### 2. Word Count and Page Constraints

| User Request | Execution Standard |
|--------------|-------------------|
| Specific word count (e.g., "3000 words") | Within ±20% |
| Specific page count (e.g., "5 pages") | Exactly equal, last page may be partial |
| Word count range (e.g., "2000-3000 words") | Must fall within range |
| No explicit requirement | Infer by document type; prefer thorough over superficial |
| Minimum specified (e.g., "more than 5000 words") | No more than 2x |

**Prohibited**:
- Padding pages with excessive bullet lists
- Exceeding twice the user's requested length

**Special case — Resume/CV**: 1 page unless user specifies otherwise.

### 3. Citation Standards

#### Search Before Writing
**DO NOT fabricate information.** When content involves statistics, policies, research, or anything you are not certain about — **search first**.

#### Citations Must Be Real
All citations must have correct author/institution names, accurate titles, and verifiable year/source. **Fabricating references is prohibited.**

#### Citation Format

| Document Language | Format |
|-------------------|--------|
| Chinese | GB/T 7714 |
| English | APA |
| Mixed | Chinese refs → GB/T 7714, English refs → APA |

### 4. Outline Adherence

- **User provided outline**: Follow strictly; do not add/remove/reorder sections
- **No outline**: Standard structure by document type:
  - Academic: Introduction → Methods → Results → Discussion → Conclusion
  - Business: Executive Summary → Analysis → Recommendations
  - Technical: Overview → Principles → Usage → Examples → FAQ

### 5. Mandatory Visual QA for Created PDFs

After every PDF creation or layout-changing edit, render every page and create a contact sheet:

```bash
python3 "{skill_path}/scripts/pdf.py" inspect /absolute/path/final.pdf -o /absolute/path/pdf-qa
```

Review the contact sheet and every reported `low_content_page`. A warning may be intentional for a cover or divider, but it must be inspected rather than ignored. Fix unexplained blank/near-blank pages, orphan lines, clipping, overlaps, and broken glyphs, then rerun `inspect`. Do not claim visual success from file-open, page-count, or structural checks alone.

---

## Tech Stack

| Library | Purpose | License |
|---------|---------|---------|
| reportlab | PDF creation (layouts, tables, charts, fonts) | BSD |
| matplotlib | Chart images for PDF insertion | PSF |
| Playwright | Browser automation for HTML → PDF | Apache-2.0 |
| Paged.js | CSS Paged Media polyfill | MIT |
| KaTeX | Math formula rendering (HTML route) | MIT |
| Mermaid | Diagram rendering (HTML route) | MIT |
| Tectonic | LaTeX → PDF compilation | MIT |
| pikepdf | Form filling, page operations, metadata | MPL-2.0 |
| pdfplumber | Text and table extraction | MIT |
| LibreOffice | Office → PDF conversion | MPL-2.0 |
| pypdfium2 | Render every page for visual QA | Apache-2.0 / BSD-3-Clause |
| Pillow | Page metrics and contact-sheet assembly | MIT-CMU |
