---
name: caixin-industry-chain
description: 财新上市公司产业链数据：基于财新数据底层的企业库以及完善的产业和产品标签体系，借助大数据挖掘技术，构建了覆盖全球上市公司的产业核心公司图谱。
category: 商业金融
auto_invoke: true
examples: 
---
# 财新产业链

用于回答**财新上市公司产业链**相关数据问题。

取数经 Moonshot **agent-gw → 内部 caixin 数据源**；财新上游凭证保留在服务端，客户端不持有。


## 覆盖范围

- 查询产业链主题与节点信息、节点关联公司、节点上下游关系。
- 查询产业链各环节涉及的股票市场表现、涨跌幅统计。
- 查询产业链各环节相关舆情数据及关联股票的舆情表现。

## 工作流

1. **判断范围**：确认用户问题属于本 skill 覆盖的财新数据范畴；否则说明不覆盖。
2. **调用脚本**：优先运行 `python3 scripts/caixin_tool.py search <关键词...>` 或 `python3 scripts/caixin_tool.py call --api-name <接口中文名> --params-json '<JSON>'`。如果会话里直接可见财新 MCP tools，也可以直接调用它们，但 Web/桌面通用路径以脚本为准。
   - 不确定接口时，先用 `scripts/caixin_tool.py search` 搜索，关键词用空格或逗号分隔（最多 5 个）。
   - 得到接口中文名和参数后，调用 `scripts/caixin_tool.py call`：
     - `caixin_call_name`：使用搜索结果返回的完整接口中文名。
     - `caixin_call_param`：JSON 对象，key 使用搜索结果中的英文参数名。
     - `file_path`：结果 CSV 保存路径（如 `/tmp/caixin_result.csv`）。
3. **处理结果**：成功则基于脚本返回结果回答；失败如实说明原因，**不要反复重试**。
4. **停止边界**：认证失败、权限不足、缺少必填入参、接口返回错误或数据为空时，不要盲目重试，先说明原因并向用户补充询问。

### 分页说明

- 多数接口单次固定返回 5 条；如需翻页，在 `caixin_call_param` 中传入 `"pageNum": "2"`（从 1 开始）。
- 少数接口不支持自动分页（`inject_page_size=false`），其返回条数以服务端默认为准，`pageNum` 翻页可能无效。

### 失败处理

- 返回空结果：说明无匹配数据，如实告知用户。
- 返回错误：转述错误信息，**不要重复调用同一工具或换参数乱试**。
- 认证失败 / 权限不足：停止调用，告知用户插件授权异常。
- Web 和桌面端的默认路径都是 `python3 scripts/caixin_tool.py`；直接可见财新 MCP tools 时可以调用，但不可把“看不到 MCP tools”当作插件不可用。

## 回答约束

只报告工具返回的内容，不杜撰数据、来源或链接。
