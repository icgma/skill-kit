# wind-mcp-skill

> **访问万得 Wind 金融数据** · A股 / 港股 / 美股 / 基金 / 指数 / 债券 / 公告 / 新闻

---

## 这是什么
 
通过 MCP 协议访问万得 Wind 金融数据库，给 AI Agent 提供：

- A股 / 港股 / 美股股票筛选 + 行情（最新价 / K 线 / 分钟）+ 财务基本面（档案 / 财报 / 股本 / 事件 / 技术指标 / 风险）
- ETF / 公募基金筛选 + 行情 + 全维数据（档案 / 财务 / 持仓 / 业绩 / 持有人 / 管理公司）
- 指数 / 板块行情 + 档案 / 基本面（成份股加权 PE / PB / PS）/ 技术指标
- 债券基本档案 / 发债主体 / 行情估值（久期 / 凸性 / 利差）/ 主体财务
- 上市公司公告 + 财经新闻 RAG
- 自然语言通用查询入口（仅在专项能力无法覆盖时兜底，覆盖更广泛的 Wind 数据库）

**不包含**：欧股 / 日股 / 其它非中概非美股、汇率 / 期货盘口、加密货币、非金融数据。

---

## 安装

```bash
# 全局（推荐 — 跨项目 + 跨 AI agent 共享）

# GitHub
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -g -y

# Gitee 镜像（国内）
npx skills add https://gitee.com/wind_info/wind-skills.git --skill wind-mcp-skill -g -y
```

> 想限制在当前项目内用，把命令的 `-g` 去掉即可。`-g` 会按 skills 工具支持的客户端进行全局安装 / 链接（如 Claude Code / Cursor / OpenClaw / Hermes 等）。

---

## 凭证（无需 WIND_API_KEY）

本 skill **不需要也不接收 `WIND_API_KEY`**。取数全程经 agent gateway / data-source 服务，Wind 凭证由网关**服务端**持有与注入；客户端只持网关 token（`KIMI_API_KEY` 或 `~/.kimi/agent-gw.json`，datasource 模式用 `TEST_API_KEY` / `DATA_SOURCE_API_KEY`）。

装好后直接向 AI 提一个 wind 数据问题即可。若返回 `AUTH_ERROR`，那是**网关凭证/部署**问题（非用户缺 Key），按 stdout JSON 里的 `error.agent_action` 处理，不要尝试配置任何 Wind Key。

---

## 使用注意

- `analytics_data` 只是兜底入口；公告 / 新闻、行情、财务基本面等明确意图应优先走对应 `server_type`。
- `references/tool-manifest.json` 是 CLI 校验 `server_type + tool_name` 的权威清单；错误组合会在真正调用后端前被本地拒绝。
- Windows PowerShell 5.x 中 JSON 转义容易导致 `INVALID_PARAMS_JSON`。如果遇到该错误，请优先看 [SKILL.md](./SKILL.md) 里的 Shell 转义说明。
- K 线工具必须同时传 `begin_date` / `end_date`；分钟级行情工具字段名是 `begin` / `end`。
- 行情类 `indexes` 建议从对应领域的指标词典复制表内字段名：[references/stock-indicators.md](./references/stock-indicators.md) / [fund-indicators.md](./references/fund-indicators.md) / [index-indicators.md](./references/index-indicators.md)（按 server_type 选择）。
- 单次工具调用只支持单标的；多标的对比需要拆成多次调用。
- Codex 沙箱中调用 Wind 后端联网时，需要使用 `require_escalated`。

---

## 升级

```bash
# 装到全局(默认推荐)
npx skills update wind-mcp-skill -g -y

# 装到当前项目(不带 -g)
npx skills update wind-mcp-skill -y
```

call 命令调用时会触发后台自动更新检查。每天首次使用时异步执行一次
`npx skills update wind-mcp-skill -y`；如果当前 skill 位于全局
`~/.agents/skills` 下，则自动追加 `-g`。执行结果写入当前 skill 根目录的
`update-state.json`；当天后续调用不会再次执行，且不会阻塞正常取数。

---

## 目录结构

```
wind-mcp-skill/
├── SKILL.md                     # AI 加载的核心守则（数据范围 / 使用方法 / 工具表 / 注意事项 / 使用技巧 / 出错怎么办）
├── references/
│   ├── stock.md                   # stock_data 领域工具契约（参数 / 场景 / 示例）
│   ├── fund.md                    # fund_data 领域工具契约
│   ├── index.md                   # index_data 领域工具契约
│   ├── bond.md                    # bond_data 领域工具契约
│   ├── financial-docs.md          # financial_docs 领域工具契约
│   ├── analytics.md               # analytics_data 领域工具契约（兜底）
│   ├── stock-indicators.md        # 股票行情字段 indexes 中文清单（按类别分组）
│   ├── fund-indicators.md         # 基金行情字段 indexes 中文清单
│   ├── index-indicators.md        # 指数行情字段 indexes 中文清单
│   └── tool-manifest.json         # CLI 前置校验的 server_type / tool_name 权威清单
├── scripts/
│   ├── cli.mjs                  # MCP 调用主入口
│   └── update-check.mjs         # 每日一次后台自动更新
└── README.md
```

详细的工具列表 / 入参 schema / 字段说明见 [SKILL.md](./SKILL.md)。
