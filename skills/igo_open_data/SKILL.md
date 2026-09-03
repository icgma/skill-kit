---
name: igo_open_data
description: Query official statistics from WHO, Eurostat, ECB, UNICEF, OECD, FAO, and UNSD, plus FRED macroeconomic time series. Each source exposes search and query APIs.
category: 商业金融
---
# igo_open_data

Use this skill to answer questions that require official statistical data from intergovernmental organizations (IGOs) or U.S./global macroeconomic time series from FRED.

## Supported sources

All sources are queried through `agent-gw`:

- `who` — WHO Global Health Observatory (GHO): health indicators by country/year
- `eurostat` — European statistics: population, GDP, prices, etc.
- `ecb` — European Central Bank SDMX: exchange rates, inflation, interest rates, monetary aggregates
- `fred` — Federal Reserve Economic Data: U.S. and global macroeconomic time series
- `unicef` — UNICEF SDMX: child mortality, vaccination, nutrition, etc.
- `oecd` — OECD Data Explorer: composite leading indicators, GDP, CPI, etc.
- `fao` — FAOSTAT: agriculture, food, nutrition bulk datasets
- `unsd` — UN Statistics Division UNdata: official statistics by DataMart

## Setup

Check whether the agent-gw Python SDK is available, and install it only if the check fails:

```bash
python3 -c "import agent_gw" || python3 -m pip install "$(curl -s https://cdn.kimi.com/agentgw/pysdk/manifest.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["latest"]["url"])')"
```

The SDK uses `KIMI_API_KEY` or `~/.kimi/agent-gw.json` for authentication.

## Workflow

1. Run `python3 scripts/igo_open_data_tool.py describe` to get the aggregated Markdown docs for all supported sources.
2. Read the returned docs carefully. They contain each source's rules, supported parameters, defaults, and allowed values.
3. Select the source and API that best matches the user's question:
   - Use `<source>_search` for catalogue/discovery (e.g. `who_search`, `eurostat_search`).
   - Use `<source>_query` for actual data retrieval (e.g. `who_query`, `fred_query`).
4. Build `params` exactly as documented. Pay attention to required parameters such as `filepath` and source-specific identifiers (e.g. `code`, `dataflow`, `id`).
5. Call the API with `call_data_source_tool`. The script sends `{"data_source_name": <source>, "api_name": <api>, "params": {...}}`:
   ```bash
   python3 scripts/igo_open_data_tool.py call \
     --data-source <source> \
     --api-name <api> \
     --params-json '{"required":"value"}'
   ```
6. If the call fails, explain the failure reason from the response.
7. If the call succeeds, read `resp.result.assistant` for any summary. The script also writes CSV files from the response to the `filepath` you provided; use those files to answer the question.

## Common use cases

- Global health indicators (life expectancy, mortality, vaccination) → `who`
- European economic and demographic statistics → `eurostat`
- Euro exchange rates, inflation, interest rates → `ecb`
- U.S. macroeconomic time series (GDP, unemployment, CPI, rates) → `fred`
- Child-focused indicators (mortality, vaccination, nutrition) → `unicef`
- OECD economic indicators (CLI, GDP, CPI) → `oecd`
- Agriculture, food production, prices → `fao`
- UN official statistics by DataMart → `unsd`

## Script

```bash
python3 scripts/igo_open_data_tool.py describe
python3 scripts/igo_open_data_tool.py call --data-source who --api-name who_search --params-json '{"keyword":"life expectancy"}'
python3 scripts/igo_open_data_tool.py call --data-source who --api-name who_query --params-json '{"code":"WHOSIS_000001","country":"CHN","year":2020,"filepath":"/tmp/who.csv"}'
```

## Notes

- The `filepath` parameter is required by most `*_query` APIs; provide a writable path with `.csv` extension.
- Country codes follow each source's documentation (usually ISO3 for WHO/UNICEF, name or code for FAO/UNSD).
- Do not invent data or URLs when a call fails or returns no result.
