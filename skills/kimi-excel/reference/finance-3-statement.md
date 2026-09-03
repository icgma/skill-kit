# Integrated Three-Statement Model

Standard for building, completing, or updating formula-linked three-statement models in Excel. Everything in the parent `SKILL.md` (gate, formula discipline, citations) still applies.

Two situations:

- **Template completion** — preserve the user's existing structure; apply this file's linkage and validation logic without forcing a rebuild.
- **Full build / rebuild** — use the structure below. Either way the delivered model must be formula-linked, assumption-driven, and balanced.

Assumptions must show business judgment: drivers and forecast inputs should reflect a defensible view of how the company actually makes money, not mechanical rates.

## Workbook structure (full build)

Sheets in this order: `Raw Data` → `Operating Drivers` → `Income Statement` → `Supporting Schedules` → `Balance Sheet` → `Cash Flow`. Default year scope: latest 5 historical periods + 3 forecast years unless the user specifies. No separate `Model Check` tab — the visible `Balance Check` row lives on `Balance Sheet`.

**Build sequence:** Raw Data → driver research → Operating Drivers (with catalysts + assumptions) → Income Statement → Supporting Schedules → Balance Sheet → Cash Flow → balance test. Drivers and schedules must be fully built for **all** model years before the statements are finalized — a partially filled schedule with blank year columns in an active block breaks linkage.

## Modeling rules

- **Three-color rule** (hard convention): blue = hardcoded inputs/assumptions, black = in-sheet formulas and historical data, green = *pure* cross-sheet pull-throughs. Any formula combining references with arithmetic (e.g. `=-('Balance Sheet'!H12-'Balance Sheet'!G12)`) is black, not green.
- **No literals inside formulas.** Every percentage, ratio, day count, tax rate or driver must come from a visible assumption cell — `=Revenue*12%` and `='Operating Drivers'!H13*0.075` are forbidden.
- **Forecast outputs are driver-based**: forecast revenue = prior revenue × (1 + growth assumption); never hardcode an output and back into the driver.
- **Units & signs**: every sheet shows currency and unit (`¥mn`, `USD mn`…); confirm common scale before any arithmetic (never mix millions and thousands); sign treatment per line item stays consistent across historical and forecast.
- **Number formats**: statement values as integers via `#,##0;(#,##0);-` (zero displays as `-` by format, never typed); percentages `0.0%`; KPIs (AR days, ASP, take rate…) may keep one decimal.

## Raw Data

Historical anchor only — IS, BS, CF plus key operating data from filings/releases. **Never any forecast year, under any circumstance.** Copy uploaded data without deleting line structure; match the source exactly (no rounding/reclassifying); use the user's requested years or the latest 5.

## Operating Drivers

Not a one-line growth tab. Unless the user specifies a breakdown, decompose revenue to the deepest defensible driver stack the data supports: product line / segment / geography / channel / unit economics (deliveries × ASP, customers × ARPU, stores × SSS, capacity × utilization × price…). Required blocks in order: `Revenue Bridge` → business/product detail → geography detail → key KPIs → `Growth Catalysts` → `Assumptions`. Business and geography views reconcile to the same total revenue.

- **Catalysts are explicit** — each major line states what changed, why, evidence, duration, cyclical/structural/one-off. `2026 growth = 15%` is not a catalyst.
- **Assumptions table** for every hardcode: `Line Item | <historical years, formula-derived> | <forecast years, blue only when true manual inputs> | Source | Rationale`. Sources like `2024 10-K`, `management guidance`, `historical average`; rationales like `new factory ramp begins 2H25`. No unexplained blue numbers anywhere.
- **Other/minor items** (other assets/liabilities, OCI, other financing): historical direct-links, forecast on one simple visible driver (`% of revenue` preferred, else historical average/flat). Never use them as balancing plugs; don't model the same item on both BS and as a manual CF adjustment; OCI stays in equity, never rolled into retained earnings. Build a separate schedule only when asked or when the item is large enough to distort.

## Statement logic

**Income Statement** — required rows: Revenue, COGS, Gross Profit, SG&A, R&D, D&A (split where available), other operating, Operating Income, interest income/expense, equity income from affiliates, other non-operating, EBT, Tax, NI to company, Minority Interest, Net Income; helper rows: YoY and margins (gray italic). `Gross Profit = Net Revenue − Cost of Revenue` (net of returns/allowances).

**Supporting Schedules** (minimum): PPE/Depreciation, Intangibles/Amortization, Debt/Interest, Equity/SBC when required. Core roll-forwards with mandatory linkage:

- `Ending Gross PPE = Beginning + Capex`; `Net PPE = Gross − Accum. Depreciation` — capex → CFI (negative), depreciation → IS, net PPE ties to BS.
- `Closing Intangibles = Opening + Additions − Amortization − Impairment` — **amortization is mandatory**, never omit it; amortization feeds IS, is added back in CFO, closing balance ties to BS.
- `Ending Debt = Beginning + Borrowings − Repayments`; `Interest = Average Debt × Rate` (or beginning/ending deliberately, to manage circularity) — flows tie to CFF and BS; split current vs long-term debt when material.
- NOL/DTA when losses exist: `Ending NOL = Beginning + Generated − Utilized`; `Taxes = MAX(0, (EBT − Utilized) × Rate)`; DTA ties to BS and ΔDTA is treated consistently on CF.
- Every schedule covers the full required year span; an unavailable historical line shows a labeled `N/A`, not a silent gap. Schedule hardcodes carry visible Source/Rationale.

**Balance Sheet** — built from drivers and schedules, never plugged: `Retained Earnings = Prior + NI to Company − Dividends`; `Ending Cash = Prior + CFO + CFI + CFF`; `Total Assets = Total Liabilities + Total Equity`. Working capital roll-forward: `DSO = AR/Revenue×365`, `DIO = Inventory/COGS×365`, `DPO = AP/COGS×365`; AR/Inventory/AP tie from the schedule. Don't double-count subtotals inside totals.

**Cash Flow** — indirect method, **both historical and forecast years**; historical rows direct-link to Raw Data by year, forecast rows modeled. Structure: NI → non-cash add-backs → ΔWC by line → CFO → investing → CFI → financing → CFF → net change → ending cash. Signs: asset increase = outflow `−(Cur−Prior)`; liability increase = inflow `+(Cur−Prior)`; capex negative; debt issued positive / repaid negative; equity issued positive; dividends negative.

Two classic traps:

- **Equity-method investments**: `Investing_Cash_LT = −(ΔLT_Investments − Equity_Income_Affiliates)` — the equity income is already in NI and is non-cash; skipping this misstates CFI and breaks balance.
- **Circularity** (interest ↔ debt ↔ cash): detect it first; preserve a template's iterative-calculation setup; if iteration is required, enable it explicitly (max iterations 100, max change 0.001) with a visible control; never break core linkages just to remove a circular reference.

## Balance validation (never deliver unbalanced)

`Balance Check` row on the Balance Sheet, bold red, showing only `Check`/`Error` for **every** historical and forecast year: `=IF(ABS(TA−TL&E)<0.1,"Check","Error")` (tolerance scaled to display units). Required tie-outs, all = 0 (or tolerance):

| Check | Formula |
|---|---|
| BS balance | `Total Assets − Total L&E` |
| Cash tie | `CF Ending Cash − BS Cash` (every forecast year) |
| NI link | `IS Net Income − CF Net Income` |
| Retained earnings | `Prior RE + NI to Company − Dividends − Ending RE` |
| Debt | `Schedule Ending Debt − BS Debt` |
| Working capital | `Schedule balances − BS balances` |
| Capex/PPE | `CF Capex ↔ PPE schedule movement` |
| D&A | `CF add-backs − IS/schedule D&A` |
| Equity financing | `Δ Common Stock/APIC − CF Equity Issuance` |
| DTA | `Tax schedule DTA − BS DTA` |

Also: no `#REF!/#DIV/0!/#VALUE!/#N/A/#NAME?`; no cross-sheet forecast reference pointing at a blank cell; negative `Total Assets`/`Cash`/`Inventory`/`AR` anywhere → recheck and justify before delivery.

**When it doesn't balance — never plug.** Compute the exact difference by year and inspect in this order: CF sign directions (equity-method rule first) → BS subtotal double-counting → ending-cash linkage **mapped by year label, not column position** (BS and CF may start in different columns; test `BS 2025E Cash = CF 2025E Ending Cash` year by year) → retained earnings roll-forward → debt signs and schedule ties → WC schedules → equity financing linkage → lease principal direction → D&A add-backs → minority interest. Heuristics: difference = `Cash` → inspect BS↔CF cash mapping; = `Net Income` → retained earnings/minority; = `Capex` → PPE roll-forward and capex sign; = a financing movement → financing flows and tie-outs.

## Format sample (banker style, not a mandate)

Dark navy bars (title/year row `#122B49`, section bars `#16365C`, white bold), visual hierarchy: major subtotals bold flush-left with a black top border across the active width, detail rows one indent, helper/ratio rows gray italic. Fills and merges stop exactly at the last visible year column — no color spill into unused cells. Top-left header cell: blank or a specific label, never the word `Line Item`.
