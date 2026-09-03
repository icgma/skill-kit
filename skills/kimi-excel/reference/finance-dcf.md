# DCF Valuation

Standard for building, reviewing, or extending discounted-cash-flow models in Excel. Everything in the parent `SKILL.md` (gate, formula discipline, citations) still applies. A DCF is an **extension of a forecast model**, not a disconnected page: link forecast lines (`Revenue`, `EBIT`, `D&A`, `Capex`, working capital, tax rate, cash, debt, diluted shares) from the operating model — never retype, never rebuild a second forecast inside the DCF tab. Extend a three-statement model unless the user provides one or asks for a simple standalone version (see `finance-3-statement.md`).

Assumptions must show business judgment, not mechanical defaults: the forecast linkage, WACC, terminal assumptions and sensitivities should reflect a defensible view of the company and its market.

## Frame

Lock before building: valuation date, currency, units, horizon, tax convention, discounting convention, terminal method, share-count convention, bridge-year balance-sheet date. Defaults: 5-year explicit forecast, mid-year convention, perpetuity growth primary with exit multiple as cross-check, diluted shares.

## Build sequence and formulas

1. **NOPAT → UFCF**: `Taxes on EBIT = EBIT × tax rate` (normalized operating rate, not a distorted one-off effective rate) → `NOPAT = EBIT − Taxes` → `UFCF = NOPAT + D&A − Capex − ΔOperating WC`. UFCF stays unlevered: no interest, issuance, repayment, or dividends. Use cash capex, only operating WC items, and don't mechanically add back SBC unless dilution treatment stays consistent.
2. **WACC**, built visibly from assumptions (never a bare hardcoded rate):
   - `Cost of Equity = Rf + β_levered × ERP` (+ size/country/company premiums only with visible rationale)
   - Peer beta build when needed: `β_unlevered = β_levered / (1 + (1−tax) × D/E)`; take peer median; `β_relevered = β_u × (1 + (1−target tax) × target D/E)`
   - Cost of debt: current borrowing rate / bond yield / spread estimate; fallback `Interest Expense / Average Debt`
   - Market-value weights `E/V`, `D/V`; `WACC = E/V × Ke + D/V × Kd × (1−tax)`
3. **Terminal value**: primary `TV = UFCF_final × (1+g) / (WACC−g)` with `g < WACC` and a sustainable final-year UFCF; cross-check `TV = Terminal Metric × Exit Multiple` with metric and multiple correctly matched (`EBITDA` ↔ `EV/EBITDA`) and explainable by trading context. If TV is an unusually high share of EV, surface that.
4. **Discounting**: mid-year convention by default; show discount periods and factors explicitly; `EV = Σ PV(UFCF) + PV(TV)`.
5. **EV → Equity → Price**: `EV − Net Debt − Preferred − Minority Interest + non-operating assets (when justified) = Equity Value`; `Equity / Diluted Shares = Implied Price`. Match balance-sheet values to the valuation/bridge date; no double counting; net cash flows through naturally.
6. **Sensitivity**: at least one two-way, formula-driven table — `WACC × g`, plus `WACC × Exit Multiple` when an exit multiple is shown. Never paste a pre-calculated sensitivity body.

## Validation before delivery

Forecast lines tie to the model; NOPAT excludes financing; D&A/capex/WC treatment consistent; WACC traceable; `g < WACC`; terminal base matches terminal method; discount factors increase with time; bridge doesn't double count; share count consistent with dilution; sensitivities move the right direction (higher WACC → lower value; higher g/multiple → higher). If base-case equity value or implied price comes out negative, do not pass it through — recheck forecast, signs, net-debt bridge and share count, and only deliver with a confirmed justification. Then pass the parent skill's gate.

## Layout sample (compact banker page, not a mandate)

One `DCF` tab, section order: title box (ticker/date/currency/units/convention/terminal method + a notes box with bridge year and terminal notes) → `NOPAT → UFCF` schedule (subtotals NOPAT/UFCF bold) → WACC build (assumption | value | rationale; blue hardcodes) → terminal value & discounting (both EV outcomes bold) → narrow `EV → Implied Price` bridge (Equity Value and Implied Price bold; keep explicit rows rather than one opaque net-debt line) → sensitivity tables on one baseline with labeled axes. Keep it compact — no slide-style whitespace; section bars stop at the last used column; don't import the comps green summary highlight.

## Text delivery

When answering in chat, summarize: base-case WACC; base-case terminal assumption; enterprise value; equity value; implied share price; key sensitivity takeaways; and the main valuation caveats.
