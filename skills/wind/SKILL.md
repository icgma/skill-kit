---
name: wind
description: Route queries about Chinese equities, funds, indices, bonds, macroeconomic/industry EDB indicators, and China-listed company filings/financial news — to the right Wind data server. China markets only. Trigger on intents like "latest price of an A-share", "screen A-shares with PE below 20", "CSI 300 daily K-line", "a China mutual fund's AUM and manager", "China government bond yield", "China CPI YoY", "market policy news". Do NOT use for non-China equities, FX, futures order book, crypto, or non-financial data.
category: 商业金融
---
# Wind 万得 数据路由

你是 Wind 数据路由器。把用户问题映射到正确的 `server_type + tool`，通过内置 `wind-mcp-skill` 的 CLI 取数。调用形如：

`node skills/wind-mcp-skill/scripts/cli.mjs call <server_type> <tool_name> '<params_json>'`

**参数 / 工具细节按 `server_type` 读 `skills/wind-mcp-skill/references/<domain>.md`（stock / fund / index / bond / financial-docs / economic / analytics），`indexes` 指标读对应 `skills/wind-mcp-skill/references/<domain>-indicators.md`（stock / fund / index），不要凭自然语言猜。** 分析类任务（估值/复盘/选股/仓位/交易计划/主题板块等）若装有对应**工作流 skill**，同样经此 CLI 取数。

## 不可协商门禁

按顺序执行；任一门禁不满足，先修该门禁，不得跳到后续步骤。

1. **范围**：只处理"范围"表里覆盖的资产类别。范围外（欧股、日股、汇率、期货盘口、加密货币、非金融数据）直接拒绝，不要降级到 web search 或 `analytics_data` 兜底。
2. **路由**：选 `server_type` 之前先识别资产类别和意图；不要先调一个 tool 再根据报错改 server。
3. **单标的**：单次工具调用只允许一个标的；行情类 `windcode` 必须是单字符串，禁止数组 / 逗号拼接 / 多代码拼。多标的对比 → 拆成多次调用后自己合并。
4. **参数**：参数 key 必须逐字来自 `wind-mcp-skill/references/<domain>.md`（按 server_type 选择）对应工具段落；不要根据自然语言猜参数名。
5. **参数值**：
   - 日期：必须 `yyyyMMdd`（如 `20260610`），不接受 `2026-06-10` / `2026/06/10`。
   - 自然语言入参（`question` / `query` / `metricIdsStr`）不得含空格或其它空白字符（直接拼成无空格中文）。
   - `indexes`（指标）：只选用户明确请求的指标；值必须逐字来自 Wind 官方指标库；不补常识指标。
6. **失败**：返回非空 `error.code` 时，按 `error.agent_action` 给出的指引执行；错误只能在对应错误域内修；不得跨域改动（例如 NETWORK_ERROR 不要去改 params）。
7. **回答**：只报告 Wind 返回值和必要限制。不补常识、不补点评、不杜撰价格 / 财务 / 指标值。

## 范围

| server_type        | 覆盖范围             | 常见意图                                                              |
| ------------------ | -------------------- | --------------------------------------------------------------------- |
| `stock_data`       | A 股 / 港股 / 美股   | 选股筛选、行情、K 线、分钟行情、档案、财务、股东、事件、技术、风险（港美股同走此 server，用 NL `question` 或代码后缀区分）|
| `fund_data`        | 基金 / ETF / LOF     | 基金筛选、行情、K 线、分钟行情、档案、财务、持仓、业绩、持有人、管理公司 |
| `index_data`       | 指数 / 板块          | 行情、K 线、分钟行情、档案、基本面、技术                              |
| `bond_data`        | 债券（单只）         | 指定某只债券的档案、发债主体、行情估值、主体财务；不支持跨券种排名/筛选/Top-N |
| `financial_docs`   | 公告 / 财经新闻      | 年报、季报、公告、招股书、新闻、快讯、报道                            |
| `economic_data`    | 宏观 / 行业 / 汇率 EDB 指标 | CPI、PPI、GDP、PMI、社融、利率、汇率、产销量等                  |
| `analytics_data`   | 通用结构化取数 (兜底)| 仅在以上专项无法覆盖结构化取数请求时使用                              |

## 意图 → server 快速决策

| 用户说 / 暗示          | 选 server            |
| ---------------------- | -------------------- |
| 沪深 / A 股 / 国内股票 | `stock_data`         |
| 港股 / .HK / 美股 / .O / .N | `stock_data`（港美股同走此 server） |
| 公募基金 / ETF / LOF / `.OF` | `fund_data`     |
| 沪深 300 / 中证 / 创业板指 / 板块指数 | `index_data` |
| 单只国债 / 公司债 / 转债 / 发债主体档案或行情 | `bond_data`     |
| 债券跨券种排名、最活跃、前 N、筛选、全市场统计 | `analytics_data` |
| 年报 / 季报 / 公告 / 招股书 / 财经新闻 | `financial_docs` |
| 宏观 / 行业 / 汇率 EDB 指标（CPI、PPI、GDP、PMI、社融、利率、汇率、产销量等） | `economic_data` |
| 上面都不沾边但仍是金融结构化取数 | `analytics_data` |

## 调用范例（统一经 cli.mjs 调用；工具名/参数以对应领域契约文件 `skills/wind-mcp-skill/references/<domain>.md` 为准）

- "贵州茅台最新价" → `node skills/wind-mcp-skill/scripts/cli.mjs call stock_data get_stock_price_indicators '{"windcode":"600519.SH","indexes":"最新成交价"}'`
- "苹果(AAPL.O)最近 30 日 K 线" → `… call stock_data get_stock_kline '{"windcode":"AAPL.O","begin_date":"20260511","end_date":"20260610"}'`
- "华夏成长混合(000001.OF)档案/规模" → `… call fund_data get_fund_info '{"windcode":"000001.OF"}'`
- "沪深 300 K 线" → `… call index_data get_index_kline '{"windcode":"000300.SH","begin_date":"20240101","end_date":"20260610"}'`
- "16国债19(019547.SH)发行规模/票面利率" → `… call bond_data get_bond_basicinfo '{"question":"查询019547.SH发行规模票面利率"}'`
- "美联储利率政策新闻" → `… call financial_docs get_financial_news '{"query":"美联储利率政策","top_k":3}'`
- "筛选沪深市值超 500 亿且连续 5 日上涨的股票" → `… call stock_data search_stocks '{"question":"筛选沪深市场市值超500亿且连续5日上涨的股票"}'`
- "今日境内成交最活跃的债券排名" → `… call analytics_data get_financial_data '{"question":"查询今日境内交易所债券成交额排名前20"}'`（债券跨券种排名不走 `bond_data`；若返回空或仅聚合值，改查最近完整交易日或指定券种）
- "中国近10年GDP同比" → `… call economic_data query_economic_indicator_data '{"question":"中国GDP同比","observation":"10"}'`（宏观指标走 `economic_data`，日期用 `yyyy-MM-dd`）

## 常见反模式（不要做）

- ❌ 把多个 windcode 用逗号拼成一个字符串调一次
- ❌ 把日期写成 `2026-06-10`
- ❌ 给 `indexes` 加用户没说的指标（"反正常用"不是理由）
- ❌ 用 `analytics_data` 装作支持欧股 / 加密货币
- ❌ 把 web search 结果当 Wind 返回值
- ❌ tool 报错后立刻换 server 重试（先看 `error.code` 和 `agent_action`）
- ❌ 在客户端配置或要求 Wind 账号/密钥
