# Comparable-Company Analysis (Comps)

Standard for building, reviewing, or reformatting public-company comps and implied valuation outputs in Excel. Everything in the parent `SKILL.md` (gate, formula discipline, citations) still applies.

## Frame first

Lock these before writing anything: target company, audience, valuation date, reporting basis (`FY` / `CY` / `LTM` / `NTM`), currency, units, and **one explicit Year Set** (e.g. `2025A / 2026E / 2027E`). The Year Set is a hard constraint across database, comps table, analytics, and valuation bridge. A user-requested year (`2026`, `FY26`, `26E`…) overrides any default; never silently fall back to more familiar years, and never feed a column labeled `2026` with 2023 data. If a requested year is unavailable for some peers, keep the labels and show `--` / `NM` with a coverage note.

## Construction order (database first)

1. **DATABASE block** — a broad peer universe (ticker, name, market data, capital structure, operating metrics), placed below the main output. This is the source of truth.
2. **Selected comps table** — 6–10 names chosen from the database. Pull every raw field (`Current Price`, `Share Count`, `EV`, `Market Cap`, `Revenue`, `EBITDA`, `Net Income`) by direct reference or lookup formula. Never retype values the database already holds.
3. **Analytical blocks** — growth, margins, trading multiples, computed *by formula* from the pulled raw data. Metric set follows the sector (SaaS → recurring-revenue growth + `EV/Revenue`; banks/REITs/energy each have their own sensible set); do not force one universal set.
4. **Summary rows** — `Mean` / `Median` over the *selected* peers only, excluding `NM` and blanks (also show `Min`/`Max` when useful).
5. **Valuation bridge** — `Calculating Implied Share Price`: selected-peer multiples × target metrics (blue-font target inputs) → implied EV → equity value → implied share price. For `P/E`, skip EV and go straight to price (`P/E × EPS`). The bridge references the summary rows, not the database directly.

## Core formulas

- `Market Cap = Price × Diluted Shares`
- `EV = Market Cap + Debt + Preferred + Minority Interest − Cash`
- `Revenue Growth = Cur / Prior − 1`; `EBITDA Margin = EBITDA / Revenue`; `Profit Margin = NI / Revenue`
- `Implied EV = Multiple × Target Metric`; `Implied Equity = Implied EV − Debt − Preferred − MI + Cash`; `Implied Price = Implied Equity / Diluted Shares`

Defaults unless told otherwise: diluted shares, median as the anchor multiple (mean as cross-check), most recently completed full-year basis.

## Meaningfulness

Show `NM` instead of forcing a number when EBITDA ≤ 0 (`EV/EBITDA`), EPS ≤ 0 (`P/E`), revenue = 0, or the capital-structure data makes EV unreliable. `--` for intentionally unused bridge cells. Never leave a visible Excel error in the presentation area.

## Peer selection

Prioritize, in order: business-model similarity → end-market overlap → margin structure → growth profile → geography/regulation → scale/liquidity. Operating comparability beats brand recognition. Keep broken-comparability names in the database but out of the selected set, and say why when an obvious name is excluded. Keep loss-makers only when economically relevant.

## Quality checks before delivery

1. Every selected comp traces to a database row; raw fields are references/lookups, not retyped.
2. EV is not below Market Cap without a clear net-cash explanation.
3. Displayed multiples reconcile to the raw numbers; summary stats use only selected peers and exclude `NM`/blanks.
4. Every header year matches the locked Year Set *and* the period actually feeding the formulas.
5. Implied range is directionally plausible vs. the current share price.
6. Units, signs, currencies consistent; data limitations (thin coverage, outliers) are surfaced in the output.
7. All tie-outs still pass the parent skill's gate (`recalc` + `verify`).

## Format sample (a banking-style default, not a mandate)

One sheet: title box (title + `Date` `yyyy/m/d` + `Currency`) → selected comps → bridge → `DATABASE` divider (deep navy bar, white text). Three-layer header for the multiples block (`Trading Multiples` umbrella spanning `EV/Revenue`, `EV/EBITDA`, `P/E`; group labels merged across their year columns; thin black bracket under each group). Multiples use custom number format `0.0"x"` (stays numeric). `Mean`/`Median` rows highlighted (e.g. `rgb(228,238,220)`) across the full table width — do **not** carry that highlight into the bridge's Min/Mean/Median/Max rows. Heatmaps only on the analytical blocks (growth/margins; inverse scale for multiples), never on the database. Blue font for hardcoded inputs (`rgb(79,113,190)` is the house shade), black for formulas.

## Text delivery

When answering in chat rather than shipping a workbook, summarize: the selected peers and why they fit; mean/median trading multiples; the implied valuation and share-price range; the locked Year Set; and any caveats that materially affect interpretation (especially incomplete requested-year coverage).
