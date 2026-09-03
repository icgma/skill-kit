# LaTeX to PDF Route

## Overview

This route is for creating PDFs using native LaTeX (.tex) files compiled with Tectonic.
The goal is to create **professional PDFs that exceed user expectations**.

**Remember**: When user says "make a PDF with LaTeX", they want "professional and high-quality PDF".

---

## Workflow

### Step 1: Environment

This route needs Tectonic (a single binary). If it is not installed:

- macOS/Linux: `curl -fsSL https://drop-sh.fullyjustified.net | sh` (installs `~/tectonic`)
- Windows: `winget install Tectonic`, or the binary from the GitHub releases page

If Tectonic cannot be installed in this environment (no network, no
privileges), fall back to the HTML or ReportLab route and tell the user.

### Step 2: Compile

Run Tectonic directly:

```bash
tectonic -X compile main.tex        # or: ~/tectonic -X compile main.tex
```

Tectonic automatically reruns TeX within a single invocation until
cross-references and the bibliography resolve, and downloads packages on first
use. If references still show `??`, check that label names match exactly.

### Step 3: Pre-Writing Analysis

Before writing .tex code, **must** consider:

1. **Identify scenario type** (formal/functional/presentation/creative)
2. **Determine user needs and goals**

#### Scenario Classification & Strategy

**Formal scenario**: Papers, submissions, journals, conferences, standards
- Strategy: Follow standard academic formatting, focus on correct citation standards

**Functional scenario**: Reports, documentation, specifications, manuals
- Strategy: Prioritize information density and readability, data presentation over visual design

**Presentation scenario**: Proposals, presentations, plans, demos
- Strategy: Overall professional (not too flashy), 1-2 visual highlights (cover, key data pages)

**Creative scenario**: Portfolios, designs, creative, branding
- Strategy: Bold color, font, content, layout choices, break conventional layouts, focus on high-impact moments

### Step 4: Design Principles

When user doesn't specify style, follow this **minimal but high-quality** design system:

1. **Contrast**: Clear contrast between text/background, headings/body
2. **Hierarchy**: Establish visual hierarchy through size, weight, color
3. **Whitespace**: Proper margins and line spacing for breathing room
4. **Consistency**: Unified fonts, colors, spacing

#### Optional Components

**Visual enhancement**: Quote blocks, info boxes, sidebars, comparison cards (use `tcolorbox`)
**Academic/Technical**: Theorem boxes (`amsthm` + `tcolorbox`), timelines, flowcharts (use TikZ)
**System styling**: Headers/footers (`fancyhdr`), chapter title pages (`titlesec`)

**When to use**: Match document type, proactively use appropriate components when content fits

---

## Core Rules

### Rule 1: Compilation Error Handling - Must Fully Fix

Tectonic output contains three types of issues:
1. **Errors** - Block compilation, must fix immediately
2. **Layout Issues** - Affect typesetting quality, must fix
   * Overfull \hbox / Overfull \vbox
   * Underfull \hbox / Underfull \vbox
   * Font shape not available / Missing character
3. **Warnings** - Other warnings, need evaluation and fixing

**Unacceptable behavior**:
- Saying "warnings don't affect output, can ignore"

**Must do**:
- Fix all issues as much as possible, especially layout issues

### Rule 2: Code Length Limit & Splitting Strategy

Model can write approximately **500 lines** of TeX per turn.

**Criteria**:
- If expected PDF is **over 5 pages**
- Or contains **multiple complex components** (3+ tables/figures, long formulas)

**Then must split** into multiple .tex files, combine with `\input{}`.

### Rule 3: Required Packages & Configuration

```latex
\documentclass{article}

% Required base packages
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{geometry}
\usepackage{amsmath}  % Load before hyperref

% hyperref - MUST load last with full config for clickable links
\usepackage[
    colorlinks=true,
    linkcolor=blue,       % TOC, cross-refs
    citecolor=darkgray,   % Citations - gray for academic style
    urlcolor=blue,        % URLs
    bookmarks=true,       % PDF bookmarks
    bookmarksnumbered=true,
    unicode=true
]{hyperref}

% Recommended geometry config
\geometry{a4paper, top=2.5cm, bottom=2.5cm, left=3cm, right=2.5cm}
% For resume: \geometry{a4paper, margin=1.5cm}

% Superscript citations (academic standard)
\usepackage[numbers,super,sort&compress]{natbib}
\bibliographystyle{unsrtnat}

% Add as needed (see "Common Packages List")
\usepackage{tcolorbox}
\usepackage{colortbl}
\usepackage{booktabs}
\usepackage{enumitem}
```

**hyperref must be loaded LAST** (after most other packages) to avoid conflicts.

### Rule 4: LaTeX Syntax Standards

**Prohibited**:
- Emoji (LaTeX doesn't support)
- Markdown `*asterisk*` bold syntax (causes compilation errors)

**Reason**: These are common model mistakes, must avoid.

**Correct approach**:
```latex
\textbf{bold text}
\emph{emphasized text}
```

### Rule 5: Multilingual & Fonts

**Multilingual packages**:
- `babel` + `polyglossia` conflict, cannot use together
- `amsmath` must be loaded before `polyglossia`

**Font related**:
- Tectonic auto-downloads standard LaTeX fonts (Latin Modern, etc.)
- `\setmainfont{}` system fonts need detection: `fc-list :lang=ar`
- Recommended fonts: DejaVu, Noto series (wide coverage)

### Rule 6: Clickable Navigation (All Links Must Work)

**Every reference in the PDF must be clickable.** This is a professional standard.

#### 6.1 Table of Contents (Auto-Clickable)
```latex
\tableofcontents      % Main TOC - each entry clickable
\listoffigures        % List of figures - clickable
\listoftables         % List of tables - clickable
```
TOC is automatically clickable with hyperref. No extra config needed.

#### 6.2 Cross-References
**Labels** (place immediately after the element):
```latex
\section{Introduction}\label{sec:intro}

\begin{figure}[htbp]
    \includegraphics{...}
    \caption{System architecture}\label{fig:arch}
\end{figure}

\begin{table}[htbp]
    \caption{Results}\label{tab:results}
    ...
\end{table}

\begin{equation}\label{eq:main}
    E = mc^2
\end{equation}
```

**References** (all become clickable):
```latex
As discussed in Section~\ref{sec:intro}...
Figure~\ref{fig:arch} shows...
Table~\ref{tab:results} indicates...
From Equation~\eqref{eq:main}...  % Use \eqref for equations (adds parentheses)
See page~\pageref{sec:intro}...
```

**Best practices**:
- Use `~` (non-breaking space) before `\ref` to prevent awkward line breaks
- Use `\eqref{}` for equations (auto-adds parentheses)
- Label naming: `sec:`, `fig:`, `tab:`, `eq:`, `lst:`

#### 6.3 Bibliography Citations

**Superscript style (Recommended for academic)**:
```latex
% natbib with superscript - cleanest approach
\usepackage[numbers,super,sort&compress]{natbib}
\bibliographystyle{unsrtnat}

% Usage:
This has been studied\cite{smith2023}.  % renders as: studied^[1]
Multiple refs\cite{a,b,c}.              % renders as: refs^[1-3]

\bibliography{references}
```

**Bracket style (alternative)**:
```latex
\usepackage[numbers]{natbib}
\bibliographystyle{plainnat}

\cite{smith2023}    % [1]
\citep{smith2023}   % (Smith, 2023)
\citet{smith2023}   % Smith (2023)

\bibliography{references}
```

**With biblatex**:
```latex
\usepackage[backend=biber,style=numeric-comp]{biblatex}
\addbibresource{references.bib}

According to~\cite{smith2023}...

\printbibliography
```

#### 6.4 URLs and External Links
```latex
\url{https://example.com}              % Plain URL
\href{https://example.com}{Click here} % Custom text
\href{mailto:a@b.com}{a@b.com}         % Email
```

#### 6.5 PDF Metadata & Bookmarks
```latex
\hypersetup{
    pdftitle={Document Title},
    pdfauthor={Author Name},
    pdfsubject={Subject},
    pdfkeywords={keyword1, keyword2}
}
```

PDF bookmarks are auto-generated from `\section`, `\chapter`, etc.

#### 6.6 Compilation for Cross-References
Tectonic reruns TeX automatically within a single `compile` invocation, so
cross-references and bibliographies resolve without extra passes. If references
still show `??`, check that label names match exactly.

#### 6.7 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Links show "??" | Label names mismatch (Tectonic already reruns automatically) | Check `\label`/`\ref` spellings match exactly; forcing extra passes with `-r/--reruns <count>` is rarely needed |
| Links not colored | Missing hyperref options | Add `colorlinks=true` |
| TOC not clickable | hyperref not loaded | Ensure hyperref is included |
| Citations show "[?]" | .bib not found or biber not run | Check path, compile again |
| No PDF bookmarks | `bookmarks=false` | Add `bookmarks=true` |

---

## Common Packages List

### Required Configuration Packages
- `hyperref` (see Rule 3)
- `geometry` (see Rule 3)
- `listings` (`\lstset{basicstyle=\ttfamily\small, numbers=left, backgroundcolor=\color{gray!5}}`)
- `enumitem` (`\setlist[itemize]{itemsep=0.3em, leftmargin=1.5em}`)

### Tables
- `booktabs`
- `longtable`
- `multirow`
- `array`
- `colortbl`

### Graphics & Charts
- `tikz`
- `pgfplots`
- `float`
- `wrapfig`
- `subfig` / `subcaption`

### Fonts / CJK
- `fontspec` (XeLaTeX/LuaLaTeX)
- `ctex`

### Math / Academic
- `amsmath`
- `amssymb`
- `amsthm`
- `biblatex`
- `natbib`
- `siunitx`

### Algorithm / Professional
- `algorithm`, `algpseudocode`
- `chemfig`

### Layout Enhancement
- `tcolorbox`
- `fancyhdr`
- `titlesec`
- `tocloft`
- `multicol`
- `setspace`
- `microtype`
- `parskip`
- `adjustbox`
- `marginnote`

### Code
- `listings`
- `minted` (requires Pygments)

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| Tectonic | LaTeX compilation engine (auto-downloads packages) |

---

## Important Notes

### CJK Font Support
Tectonic **automatically downloads** required fonts including CJK fonts. No manual configuration needed.

For Chinese documents, use:
```latex
\usepackage{ctex}  % Tectonic auto-downloads fonts
```

### First Run Download
Tectonic downloads packages on first use. Initial compilation may take longer:
- First run: 1-5 minutes (downloading packages)
- Subsequent runs: 10-30 seconds

### Network Requirements
Tectonic requires network access for first compilation of new packages. If offline:
- Previously used packages are cached in `~/.cache/Tectonic/`
- New packages will fail to download

### Tectonic vs Traditional LaTeX
| Feature | Tectonic | Traditional (pdflatex) |
|---------|----------|------------------------|
| Package management | Automatic | Manual (tlmgr) |
| Multiple runs | Single command | Manual bibtex/biber cycles |
| Cross-references | Automatic | Manual multiple passes |
| Installation | Single binary | Full TeX Live (~4GB) |
