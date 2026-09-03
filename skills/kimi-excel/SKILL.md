---
name: kimi-excel
description: Create and analyze Excel spreadsheets (.xlsx/.csv). Supports formula-driven analysis, charts, conditional formatting, styled reports, and financial modeling (three-statement models, DCF, comps analysis). Use when the user mentions Excel, spreadsheet, .xlsx, .csv, or asks for data analysis with formulas.
category: 商业金融
---
# Excel Skill

Deliver a real `.xlsx` built with Python + openpyxl/pandas in a workspace script. Do not deliver readme files or side artifacts unless asked.

## Route

| Task | Route | Read |
|------|-------|------|
| Create a workbook (data, formulas, charts, styling) | write an openpyxl script in the workspace, run it, then pass the gate below | this file |
| Read / analyze an existing `.xlsx` / `.csv` | pandas or openpyxl; for *editing* a rich existing workbook, see the round-trip pitfall below first | this file |
| Three-statement model | load `reference/finance-3-statement.md` | that file |
| DCF valuation | load `reference/finance-dcf.md`; extend an existing/3-statement model unless the user wants a simple standalone one | that file |
| LBO or other model-based valuation | start from the 3-statement model unless the user asks for a presentation-only output or provides a completed model | `reference/finance-3-statement.md` |
| Comps / peer valuation | load `reference/finance-comps.md`; standalone by default — do not pair with the model routes unless the user asks for a combined deliverable | that file |
| Pivot table | openpyxl **cannot** create PivotTables — default to a formula-driven summary (`SUMIFS`/`COUNTIFS`); if a native PivotTable is explicitly required, drive a real engine (Windows Excel via `win32com`, LibreOffice via UNO) when available, else say it must be created manually in Excel/WPS | this file |

## The gate — `scripts/xlsx_tools.py`

Every delivered workbook goes through the gate. Invoke via `python3 "{skill_path}/scripts/xlsx_tools.py" <cmd> <file>`; `{skill_path}` is the directory containing this SKILL.md.

**`recalc <file.xlsx>`** — recalculates formulas so cached values exist (openpyxl writes formula strings but never computes them; without caches, WPS/Google Sheets/programmatic reads see empty cells). `.xlsx` only — refusing other extensions is deliberate (recalc-ing a `.csv` would silently overwrite it with binary).

Engine chain and result semantics:

- `status: "success", method: "libreoffice"` — real recalculation happened; the delivered file carries fresh caches. Watch `warnings`: LibreOffice conversion can drop openpyxl-written conditional-formatting extension blocks (x14). If that matters, prefer another engine.
- `status: "success", method: "formulas"` — only the separate `verification_file` was calculated; **deliver the original**, the verification copy strips charts/styles. (This engine path is untested in the managed runtime; do not rely on it.)
- `status: "deferred", method: "calcMode_auto"` — **no calculation happened**. Excel/WPS will recalc on open, but every formula result is unverified: run the independent-QA rule below.

**`verify <file.xlsx>`** — static checks: cached formula errors (`#REF!` etc.), missing caches, forbidden functions, implicit array formulas.

- `pass` — clean. `pass_with_warnings` — caches exist, compatibility warnings remain.
- `unverified` — formula cells lack cached results; **not** proof of correctness. Apply the independent-QA rule.
- `fail` — deterministic error found; fix before delivery. Exit code 0 never upgrades `unverified` to `pass`.

**Independent-QA rule (when results are unverified/deferred):** independently calculate, from the raw inputs, every derived value your final response reports — print each one from the QA command and copy those printed values; omit any value you did not print and check. Never "fix" this by replacing workbook formulas with hardcoded outputs, and never present the workbook as fully validated when the checks could not run.

**Other engines are fair game.** On Windows with Office installed, `win32com` drives real Excel — lossless recalculation that preserves everything LibreOffice may drop; LibreOffice UNO can recalc, build PivotTables, and edit rich files in place. Use whatever the machine offers; the contract is honesty about what actually ran and whether the delivered file changed in ways you didn't intend — not the choice of engine.

## Formula & data discipline

- **Formulas first.** Wherever an Excel formula can express a calculation, it MUST be a formula. Static values only for true inputs, assumptions, externally sourced data, or to break a circular reference. Never compute derived results in Python and paste them in.
- **No circular references** in general workbooks — every calculated cell must resolve to a valid value. (Deliberate iterative setups in financial models: see `reference/finance-3-statement.md`.)
- **No placeholders.** Cells never say "TBD", "Pending", or "Manual calculation required" — complete scenario assumptions in advance so every formula resolves.
- **Zero tolerance for formula errors** (`#VALUE! #DIV/0! #REF! #NAME? #N/A`). Do not rationalize ("it will resolve when opened" — it won't; "0 is expected" — verify the referenced cells actually hold data). A file with any formula error is not deliverable.
- **Off-by-one is the top bug.** Before saving, verify formulas reference data cells, not headers or out-of-range cells.
- **No implicit array formulas.** `=MATCH(TRUE(), A1:A10>0, 0)` works in LibreOffice but yields `#N/A` in desktop Excel (needs CSE). Rewrite with `SUMPRODUCT` or a helper column.
- **Forbidden functions** (unsupported by Excel ≤2019; the file fails there). Use traditional alternatives:

| Forbidden | Alternative |
|---|---|
| `FILTER()` | AutoFilter, SUMIF/COUNTIF, INDEX-MATCH |
| `UNIQUE()` | Remove Duplicates, COUNTIF helper |
| `SORT()`, `SORTBY()` | Data → Sort |
| `XLOOKUP()` | `INDEX()` + `MATCH()` |
| `XMATCH()` | `MATCH()` |
| `SEQUENCE()` | `ROW()` or fill |
| `LET()` | helper cells |
| `LAMBDA()` | named ranges |
| `RANDARRAY()` | `RAND()` fill-down |
| `ARRAYFORMULA()`, `QUERY()`, `IMPORTRANGE()` | Google Sheets only — never use |

- **External data must carry citations**: two plain-text columns `Source Name` | `Source URL` (no `HYPERLINK()`), or a dedicated Sources sheet when per-row citation is impractical. This applies to every fetched value — datasource tools, web search, APIs.
- Lookups: `VLOOKUP` with `FALSE`, locked range `$A$2:$D$100`, wrapped in `IFERROR`; use `INDEX/MATCH` when the lookup column isn't leftmost.

## Pitfalls (the ones that actually bite)

- Formula results must be numeric, not text — a number stored as text silently breaks every downstream `SUM`.
- Units go in headers (`Revenue (¥mn)`), not after numbers in cells; stored values must match the declared unit — never mix millions and thousands in one calculation chain.
- Store raw unscaled values (15000000, not 15 or 1.5); scaling and currency symbols are number-format jobs (`"¥#,##0"`), never something a formula must convert.
- Every table must have content, not just headers.
- Text starting with `=` is parsed as a formula — write such cells as text (prefix or text format) to avoid `#NAME?`.
- Pie charts: `set_categories()` is not optional — without it slices have no labels. After saving, reopen and assert every chart has series (`for ws in wb: for ch in ws._charts: assert ch.series`).
- If the user asks for charts, embed real openpyxl charts — never a "chart data" sheet with instructions to click Insert → Chart. When a workbook holds several prepared datasets, give each one a chart unless the user says otherwise, and explain any dataset left unvisualized. Pass `titles_from_data=True` when adding data so the legend shows headers, not "Series1".
- **openpyxl round-trip is lossy**: opening and re-saving an existing workbook silently drops charts, images, shapes, and other features openpyxl doesn't parse. To edit a rich existing file, drive a real engine (`win32com`/UNO) or disclose the loss.
- Size columns and rows so nothing clips; a too-narrow column shows `###`.

## Style — a sample default, not a mandate

One clean default that works for most business output; adapt freely to the user's house style or the task at hand:

- Hide gridlines (`ws.sheet_view.showGridLines = False`), start content at B2, give the title row extra height, merge cells for titles and grouped headers.
- Keep `wrap_text` off by default; turn it on only when the user asks or long text genuinely needs it.
- Monochrome base (white/black/greys) with a single accent; borders sparingly — section separators and header underlines, not full grids.
- Conditional formatting (data bars, color scales, icon sets) on the 2–4 columns where comparison adds signal.
- Banking font convention for finance models: **blue** = hardcoded inputs/assumptions, **black** = in-sheet formulas, **green** = pure cross-sheet pull-throughs, **red** = external references. A formula mixing cross-sheet refs with arithmetic is black.
- Regional market colors: China red = up / green = down; international green = up / red = down.
- Multi-sheet reports: lead with a cover/summary sheet (title, 3–6 key metrics, sheet index).

## Skill map

```
kimi-excel/
├── SKILL.md                        # routes, gate contract, discipline, pitfalls
├── reference/
│   ├── finance-3-statement.md      # integrated three-statement model standard
│   ├── finance-dcf.md              # DCF valuation standard
│   └── finance-comps.md            # comparable-company analysis standard
└── scripts/
    └── xlsx_tools.py               # recalc / verify gate
```
