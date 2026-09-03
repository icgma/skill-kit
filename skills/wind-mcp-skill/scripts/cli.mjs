#!/usr/bin/env node
// wind-mcp-skill CLI: thin JSON-envelope wrapper around Wind MCP servers
import { readFileSync, writeFileSync, existsSync, mkdirSync, copyFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, dirname, basename, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const SKILL_VERSION = '1.9.7';

// 本地 registry: 工具选择可在任何网络调用前失败
const SERVERS = {
  stock_data: {
    label: 'Wind A股/港股/美股 股票（选股筛选 + 档案/财务/股本/事件/技术/风险 + 行情/K线/分钟）',
  },
  fund_data: {
    label: 'Wind 基金（基金筛选 + 档案/财务/持仓/业绩/持有人/公司 + 行情/K线/分钟）',
  },
  index_data: {
    label: 'Wind 指数/板块（档案/基本面/技术 + 行情/K线/分钟）',
  },
  bond_data: {
    label: 'Wind 债券（基本档案/发债主体/行情估值/主体财务）',
  },
  financial_docs: {
    label: 'Wind 金融文档 RAG（公告 / 新闻）',
  },
  economic_data: {
    label: 'Wind EDB 宏观/行业经济指标',
  },
  analytics_data: {
    label: 'Wind 通用分析数据（NL → Wind 数据）',
  },
};

const PORTAL_URL = 'https://aifinmarket.wind.com.cn/#/user/overview';

const SKILL_DIR = dirname(dirname(fileURLToPath(
  import.meta.url)));

const UPDATE_CHECK_PATH = join(SKILL_DIR, 'scripts', 'update-check.mjs');
const TOOL_MANIFEST_PATH = join(SKILL_DIR, 'references', 'tool-manifest.json');
const ERROR_CODES_PATH = join(SKILL_DIR, 'references', 'error-codes.json');
const NORMALIZATION_RULES_PATH = join(SKILL_DIR, 'references', 'normalization-rules.json');
const SKILL_NAME = basename(SKILL_DIR);

// ───── 取数后端：经 wind_tool.py 复用（替代直连 mcp.wind.com.cn）─────
// 两种链路，由是否拿到 data-source key 决定（见 runWindTool）：
//   1) datasource 模式（默认目标）：直连 big-py data-source 服务（含 MR 448 全量
//      Wind 接口：news/公告/fund/index/bond）。base_url 与 key 从环境变量读，
//      绝不硬编码密钥；默认 base_url 指向 test 环境。
//   2) agent-gw 模式（回退）：无 data-source key 时退回 Moonshot agent-gw，
//      凭证 / base_url 由 wind_tool.py 解析（KIMI_API_KEY / ~/.kimi/agent-gw.json）。
// 插件根目录 = SKILL_DIR/../..（skills/wind-mcp-skill -> 插件根）。
// wind_tool.py 路径多候选解析：部署态 skill 可能被单独安装到 /app/skills/... ，
// 此时 ../../scripts 会指向不存在的 /app/scripts；故优先用与 cli.mjs 同目录的
// 随 skill 打包副本，再回退 monorepo 的插件根/scripts。取第一个真实存在的。
const WIND_TOOL_PY = (() => {
  if (process.env.WIND_TOOL_PY) return process.env.WIND_TOOL_PY;
  const candidates = [
    join(SKILL_DIR, 'scripts', 'wind_tool.py'),             // 随 skill 走（部署态首选）
    join(SKILL_DIR, '..', '..', 'scripts', 'wind_tool.py'), // monorepo：插件根/scripts
    join(SKILL_DIR, '..', 'scripts', 'wind_tool.py'),
  ];
  return candidates.find((p) => existsSync(p)) || candidates[0];
})();
const PYTHON_BIN = process.env.WIND_PYTHON || 'python3';

// data-source 直连配置：有 key 才走 datasource 模式，否则回退 agent-gw。
const WIND_DS_ENV = process.env.WIND_DS_ENV || 'test';
const WIND_DS_BASE_URL = process.env.WIND_DS_BASE_URL
  || process.env.TEST_BASE_URL
  || 'http://data-source-test.mse.msh.work';
const WIND_DS_API_KEY = process.env.WIND_DS_API_KEY
  || process.env.TEST_API_KEY
  || process.env.DATA_SOURCE_API_KEY
  || '';

const CALL_EXAMPLES = [
  `cli.mjs call stock_data search_stocks '{"question":"筛选沪深市场市值超500亿且连续5日上涨的股票"}'`,
  `cli.mjs call stock_data search_stocks '{"question":"筛选港股中市值超1000亿港元的科技股"}'`,
  `cli.mjs call fund_data search_funds '{"question":"筛选股票型基金中近一年收益率超20%的产品"}'`,
  `cli.mjs call stock_data get_stock_basicinfo '{"question":"600519.SH公司基本档案"}'`,
  `cli.mjs call stock_data get_stock_price_indicators '{"windcode":"600519.SH","indexes":"中文简称,最新成交价,涨跌幅"}'`,
  `cli.mjs call fund_data get_fund_kline '{"windcode":"588200.SH","begin_date":"20260401","end_date":"20260430"}'`,
  `cli.mjs call stock_data get_stock_quote '{"windcode":"AAPL.O"}'`,
  `cli.mjs call index_data get_index_kline '{"windcode":"000300.SH","begin_date":"20260401","end_date":"20260430"}'`,
  `cli.mjs call financial_docs get_financial_news '{"query":"市场政策新闻","top_k":3}'`,
  `cli.mjs call economic_data search_economic_indicator '{"question":"中国GDP相关指标"}'`,
  `cli.mjs call economic_data query_economic_indicator_data '{"question":"中国GDP不变价当季同比","beginDate":"2024-01-01","endDate":"2026-08-31"}'`,
  `cli.mjs call analytics_data get_financial_data '{"question":"查询中国A股市场过去一年的平均成交量"}'`,
];

// ───── 自动更新 ─────
// 每天首次使用 skill 时异步执行一次 npx skills update，不阻塞主流程。

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function normalizePath(value) {
  const normalized = resolve(value).replace(/\\/g, '/');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function updateScope() {
  const globalRoot = normalizePath(join(homedir(), '.agents', 'skills'));
  const skillDir = normalizePath(SKILL_DIR);
  return skillDir.startsWith(globalRoot + '/') ? 'global' : 'project';
}

function updateStateFile() {
  return join(SKILL_DIR, 'scripts', 'update-state.json');
}

function readUpdateState() {
  try {
    const stateFile = updateStateFile();
    if (!existsSync(stateFile)) return null;
    return JSON.parse(readFileSync(stateFile, 'utf8'));
  } catch {
    return null;
  }
}

function writeUpdateStatePatch(patch) {
  const stateFile = updateStateFile();
  mkdirSync(dirname(stateFile), { recursive: true });
  const state = { ...(readUpdateState() || {}), ...patch };
  writeFileSync(stateFile, JSON.stringify(state, null, 2) + '\n');
}

function alreadyUpdatedToday() {
  try {
    const state = readUpdateState();
    return state && state.date === todayKey() && state.status === 'success';
  } catch {
    return false;
  }
}

function markSkillUsed() {
  writeUpdateStatePatch({
    lastUsedAt: new Date().toISOString(),
    lastUsedPid: process.pid,
  });
}

function triggerUpdateCheck() {
  try {
    if (!existsSync(UPDATE_CHECK_PATH)) return;
    if (alreadyUpdatedToday()) return;
    markSkillUsed();
    const tmpDir = join(homedir(), '.cache', 'wind-allskill');
    mkdirSync(tmpDir, { recursive: true });
    const runnerPath = join(tmpDir, `update-check-${SKILL_NAME}-${process.pid}.mjs`);
    copyFileSync(UPDATE_CHECK_PATH, runnerPath);
    const child = spawn('node', [runnerPath, SKILL_DIR], { detached: true, stdio: 'ignore', windowsHide: true });
    child.on('error', () => {});
    child.unref();
  } catch {}
}

export { triggerUpdateCheck };

// section: 工具函数

// call 成功: 完整透传 MCP result, 不抽取; agent 自行 parse content[0].text
function writeRawCallSuccess(result) {
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}

function writePlainSuccess(data) {
  process.stdout.write(JSON.stringify(data, null, 2) + '\n');
}

// 失败 envelope { ok:false, error:{code, agent_action} }; update 信号走 stderr 不进 stdout
function writeErrorEnvelope(code, detail) {
  const envelope = {
    ok: false,
    error: {
      code,
      agent_action: buildAgentAction(code, detail),
    },
  };
  process.stdout.write(JSON.stringify(envelope, null, 2) + '\n');
}

function die(code, detail = null, exitCode = 1) {
  writeErrorEnvelope(code, detail);
  process.exit(exitCode);
}

function exitWithUsage(usage, exitCode = 0) {
  die('USAGE_ERROR', `USAGE:\n${usage}`, exitCode);
}

function maskKey(key) {
  if (!key || key.length < 8) return '***';
  return key.slice(0, 4) + '***' + key.slice(-4);
}

// dotenv 解析: 兼容注释 / 引号 / export 前缀
function parseDotenv(content) {
  const env = {};
  for (const rawLine of content.split('\n')) {
    let line = rawLine.replace(/^﻿/, '').trim();
    if (!line || line.startsWith('#')) continue;
    if (line.startsWith('export ')) line = line.slice(7).trim();
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    } else {
      const hashIdx = val.indexOf(' #');
      if (hashIdx >= 0) val = val.slice(0, hashIdx).trim();
    }
    env[key] = val;
  }
  return env;
}

function getServer(server_type) {
  const server = SERVERS[server_type];
  if (!server) {
    die('ROUTE_ERROR', `未知 server_type: ${server_type}. 可用: ${Object.keys(SERVERS).join(' / ')}`);
  }
  return server;
}

function loadToolManifest() {
  try {
    // tool-manifest.json is the authority for legal server_type + tool_name combinations.
    const manifest = JSON.parse(readFileSync(TOOL_MANIFEST_PATH, 'utf8'));
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
      throw new Error('manifest 顶层必须是对象');
    }
    for (const [serverType, tools] of Object.entries(manifest)) {
      if (!SERVERS[serverType]) {
        throw new Error(`manifest 包含未知 server_type: ${serverType}`);
      }
      if (!Array.isArray(tools) || tools.some(tool => typeof tool !== 'string' || !tool)) {
        throw new Error(`manifest 中 ${serverType} 的工具清单必须是非空字符串数组`);
      }
    }
    for (const serverType of Object.keys(SERVERS)) {
      if (!Array.isArray(manifest[serverType])) {
        throw new Error(`manifest 缺少 server_type: ${serverType}`);
      }
    }
    return manifest;
  } catch (err) {
    die('UNKNOWN', `工具清单读取失败: ${err.message}`);
  }
}

function validateToolSelection(server_type, toolName) {
  getServer(server_type);
  const manifest = loadToolManifest();
  const tools = manifest[server_type];
  if (!tools.includes(toolName)) {
    die('ROUTE_ERROR', `工具名 "${toolName}" 不属于 server_type "${server_type}"。`);
  }
}

const BASIC_TEXT_KEYS = ['question', 'query', 'metricIdsStr', 'windcode', 'indexes', 'freq', 'magnitude', 'currency'];
const BASIC_NO_WHITESPACE_KEYS = ['query', 'metricIdsStr'];
const BASIC_DATE_KEYS = ['begin_date', 'end_date', 'beginDate', 'endDate', 'date', 'tradeDate'];
const PRICE_INDICATOR_TOOLS = new Set(['get_stock_price_indicators', 'get_fund_price_indicators', 'get_index_price_indicators']);
const KLINE_TOOLS = new Set(['get_stock_kline', 'get_fund_kline', 'get_index_kline']);
const QUOTE_TOOLS = new Set(['get_stock_quote', 'get_fund_quote', 'get_index_quote']);
function readNormalizationRules() {
  const rules = JSON.parse(readFileSync(NORMALIZATION_RULES_PATH, 'utf8'));
  return {
    klinePeriods: new Set(rules.kline_periods || []),
    periodAliases: new Map(Object.entries(rules.period_aliases || {})),
    indicatorAliases: new Map(Object.entries(rules.indicator_aliases || {})),
    indexCodeAliases: new Map(Object.entries(rules.index_code_aliases || {})),
    legacyToolAliases: new Map(Object.entries(rules.legacy_tool_aliases || {})),
    toolByDomain: rules.tool_by_domain || {},
  };
}

const NORMALIZATION_RULES = readNormalizationRules();
const KLINE_PERIODS = NORMALIZATION_RULES.klinePeriods;
const PERIOD_ALIASES = NORMALIZATION_RULES.periodAliases;
const INDICATOR_ALIASES = NORMALIZATION_RULES.indicatorAliases;
const INDEX_CODE_ALIASES = NORMALIZATION_RULES.indexCodeAliases;
const LEGACY_TOOL_ALIASES = NORMALIZATION_RULES.legacyToolAliases;
const TOOL_BY_DOMAIN = NORMALIZATION_RULES.toolByDomain;

function isValidBasicDate(value) {
  const s = String(value || '').trim();
  // 兼容 Wind 通用工具的 yyyyMMdd 与 EDB 宏观指标的 yyyy-MM-dd
  let y, m, d;
  if (/^\d{8}$/.test(s)) {
    y = Number(s.slice(0, 4));
    m = Number(s.slice(4, 6));
    d = Number(s.slice(6, 8));
  } else if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    y = Number(s.slice(0, 4));
    m = Number(s.slice(5, 7));
    d = Number(s.slice(8, 10));
  } else {
    return false;
  }
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

function normalizeIndicatorKey(value) {
  return String(value || '').trim().replace(/\s+/g, '').replace(/[（]/g, '(').replace(/[）]/g, ')').toLowerCase();
}

function normalizeIndexes(indexes) {
  if (typeof indexes !== 'string') return indexes;
  return indexes.split(',').map((item) => INDICATOR_ALIASES.get(normalizeIndicatorKey(item)) || item.trim()).filter(Boolean).join(',');
}

function looksLikeFundCode(code) {
  return /^5\d{5}\.SH$/.test(code) || /^1[56]\d{4}\.SZ$/.test(code) || /^\d{6}\.OF$/.test(code);
}

function looksLikeIndexCode(code) {
  return /^(\d{6})\.(CSI|WI|MI|HI|GI)$/.test(code) ||
    /^(000300|000905|000852|000016|000001)\.SH$/.test(code) ||
    /^(399001|399006|399300)\.SZ$/.test(code) ||
    /^[A-Z]{2,10}\.(HI|GI)$/.test(code);
}

function normalizeWindcode(windcode) {
  if (typeof windcode !== 'string') return windcode;
  const raw = windcode.trim();
  const alias = INDEX_CODE_ALIASES.get(raw.toUpperCase());
  if (alias) return alias;
  const upper = raw.toUpperCase();
  if (/^\d{4}\.HK$/.test(upper)) return `0${upper}`;
  if (looksLikeIndexCode(upper)) return upper;
  if (/^\d{6}$/.test(upper)) {
    if (/^9\d{5}$/.test(upper)) return `${upper}.BJ`;
    if (/^5\d{5}$/.test(upper)) return `${upper}.SH`;
    if (/^1[56]\d{4}$/.test(upper)) return `${upper}.SZ`;
    if (/^(000300|000905|000852|000016|000001)$/.test(upper)) return `${upper}.SH`;
    if (/^399\d{3}$/.test(upper)) return `${upper}.SZ`;
    if (/^[036]\d{5}$/.test(upper)) return `${upper}.${upper.startsWith('6') ? 'SH' : 'SZ'}`;
  }
  if (/^5\d{5}\.SZ$/.test(upper)) return upper.replace(/\.SZ$/, '.SH');
  if (/^1[56]\d{4}\.SH$/.test(upper)) return upper.replace(/\.SH$/, '.SZ');
  if (/^[03]\d{5}\.SH$/.test(upper)) return upper.replace(/\.SH$/, '.SZ');
  if (/^6\d{5}\.SZ$/.test(upper)) return upper.replace(/\.SZ$/, '.SH');
  if (/^9\d{5}\.(SH|SZ)$/.test(upper)) return upper.replace(/\.(SH|SZ)$/, '.BJ');
  if (/^[A-Z]{1,5}$/.test(upper)) return `${upper}.O`;
  return upper;
}

function toolFamily(toolName) {
  if (PRICE_INDICATOR_TOOLS.has(toolName)) return 'price';
  if (KLINE_TOOLS.has(toolName)) return 'kline';
  if (QUOTE_TOOLS.has(toolName)) return 'quote';
  return null;
}

function inferServerTypeFromWindcode(currentServerType, windcode) {
  if (typeof windcode !== 'string') return currentServerType;
  if (looksLikeFundCode(windcode)) return 'fund_data';
  if (looksLikeIndexCode(windcode)) return 'index_data';
  if (/^\d{4,5}\.HK$/.test(windcode) || /^[A-Z]{1,5}\.(O|N|A|HK|SH|SZ|BJ)$/.test(windcode) || /^\d{6}\.(SH|SZ|BJ)$/.test(windcode)) {
    return 'stock_data';
  }
  return currentServerType;
}

function normalizeCall(server_type, toolName, args) {
  const legacyTool = LEGACY_TOOL_ALIASES.get(toolName);
  if (legacyTool) [server_type, toolName] = legacyTool;
  const normalizedArgs = { ...args };
  if (typeof normalizedArgs.indexes === 'string') normalizedArgs.indexes = normalizeIndexes(normalizedArgs.indexes);
  if (typeof normalizedArgs.windcode === 'string') normalizedArgs.windcode = normalizeWindcode(normalizedArgs.windcode);
  if (typeof normalizedArgs.period === 'string') {
    const key = normalizedArgs.period.trim().toLowerCase();
    normalizedArgs.period = PERIOD_ALIASES.get(key) || normalizedArgs.period.trim();
  }
  const family = toolFamily(toolName);
  if (family && typeof normalizedArgs.windcode === 'string') {
    server_type = inferServerTypeFromWindcode(server_type, normalizedArgs.windcode);
    toolName = TOOL_BY_DOMAIN[family]?.[server_type] || toolName;
  }
  return { server_type, toolName, args: normalizedArgs };
}

function validateBasicParams(params) {
  const errors = [];
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    return ['params 必须是 JSON object'];
  }

  for (const key of BASIC_TEXT_KEYS) {
    if (!(key in params)) continue;
    if (typeof params[key] !== 'string') {
      errors.push(`字段 '${key}' 必须是字符串`);
    } else if (params[key].trim().length === 0) {
      errors.push(`字段 '${key}' 不能为空或全空白`);
    }
  }

  for (const key of BASIC_NO_WHITESPACE_KEYS) {
    if (typeof params[key] === 'string' && /\s/.test(params[key])) {
      errors.push(`字段 '${key}' 不得含空格或其它空白字符`);
    }
  }

  if (typeof params.windcode === 'string' && params.windcode.includes(',')) {
    errors.push("字段 'windcode' 只允许单个标的，禁止逗号拼接多代码");
  }

  for (const key of BASIC_DATE_KEYS) {
    if (!(key in params)) continue;
    if (typeof params[key] === 'string' && !isValidBasicDate(params[key])) {
      errors.push(`字段 '${key}' 日期格式错误，要求 yyyyMMdd 或 yyyy-MM-dd`);
    }
  }

  return errors;
}

function validateToolParams(toolName, params) {
  const errors = [];
  if (KLINE_TOOLS.has(toolName)) {
    for (const key of ['windcode', 'begin_date', 'end_date']) {
      if (!(key in params)) errors.push(`K 线工具缺少必填字段 '${key}'`);
    }
    if ('period' in params && !KLINE_PERIODS.has(String(params.period))) {
      errors.push(`字段 'period' 只能是 ${Array.from(KLINE_PERIODS).join('/')}，日 K 请传 '10'`);
    }
    for (const key of ['aftime', 'issusp']) {
      if (key in params && !new Set(['0', '1']).has(String(params[key]))) {
        errors.push(`字段 '${key}' 只能是 '0' 或 '1'`);
      }
    }
  }
  return errors;
}

// ───── 认证 ─────
// 客户端不持有 WIND_API_KEY。取数全程经 agent gateway（见 runWindTool）；
// Wind 凭证由网关服务端持有与注入，本插件不读取、不接收、不存储任何 Wind key。

// section: 错误码 — message 来自 HTTP / JSON-RPC / 工具内嵌 JSON, 统一映射成稳定 code

const ERROR_PATTERNS = [
  ['TEMPORARILY_UNAVAILABLE', /temporarily_unavailable/i, '后端偶发不可用。'],
  ['INVALID_PARAM_VALUE', /invalid_param_value/i, '后端参数值错误。'],
  ['INVALID_PARAM_NAME', /invalid_param_name/i, '后端参数名错误。'],
  ['QUOTA_ERROR', /单日请求次数超限|daily.*limit|余额不足|请先充值|insufficient.*balance|请求过于频繁|qps.*limit|too.*frequent/i, '额度/限流错误。等待额度刷新、换备用 Key 或充值后原样重试。'],
  ['AUTH_ERROR', /密钥无效|key.*invalid|unauthorized|认证失败|auth.*fail/i, '认证/权限错误。按 Key 机制修复后原样重试。'],
  ['NO_RESULTS', /未获取到数据|"NO_RESULTS"|no\s*results?|not\s*found|empty\s*result/i, '未获取到匹配数据。先在不改变用户意图的前提下调整关键词或参数。'],
  ['PARAM_VALIDATION_ERROR', /参数验证失败|参数.*(错误|非法|无效)|字段.*(不存在|不识别|不支持|非法)|invalid\s*(param|argument|field)|missing\s*(param|argument|field|required)/i, '后端参数验证失败。先按 SKILL.md 工具表核对字段名、必填项、日期格式和枚举值后重试。'],
  ['NETWORK_ERROR', /服务.*暂不可用|服务.*不可用|service\s+unavailable|temporarily\s+unavailable/i, '网络/后端错误。先核对参数再稍后重试。'],
  ['TOOL_RUNTIME_ERROR', /TOOL_ERROR|tool.*error|工具.*(执行|运行).*错误|runtime.*error/i, '后端工具运行错误。保留后端原文，先检查请求是否过大或口径是否受支持；不要直接切换工具绕过。'],
];

function inferErrorCode(msg) {
  if (!msg) return 'UNKNOWN';
  for (const [code, pat] of ERROR_PATTERNS) {
    if (pat.test(msg)) return code;
  }
  return 'UNKNOWN';
}

// agent_action = 诊断 + 行动 一体的 NL 处方; 唯一总表在 references/error-codes.json
function loadAgentActions() {
  const fallback = {
    UNKNOWN: '未知错误。不要盲目重试；先查看当前错误详情，能定位本地问题（参数 / 配置 / 网络）则修正后重试一次，无法定位则保留原文告知用户并停止。',
  };
  try {
    const doc = JSON.parse(readFileSync(ERROR_CODES_PATH, 'utf8'));
    const codes = doc && typeof doc.codes === 'object' ? doc.codes : null;
    if (!codes) return fallback;
    return {
      ...fallback,
      ...Object.fromEntries(
        Object.entries(codes).filter(([, action]) => typeof action === 'string' && action.trim()),
      ),
    };
  } catch {
    return fallback;
  }
}

const AGENT_ACTIONS = loadAgentActions();

// detail 只保留短诊断，避免后端长文本淹没 agent_action。
function buildAgentAction(code, detail) {
  const template = AGENT_ACTIONS[code] || AGENT_ACTIONS.UNKNOWN;
  if (code === 'USAGE_ERROR') return template;
  if (detail && typeof detail === 'string' && detail.trim()) {
    const d = detail.trim().slice(0, 500);
    return `[${d}] ${template}`;
  }
  return template;
}

// ───── 后端契约适配层 ─────
// 前端工具名/参数（驼峰、按数据域分的 quote/kline/snapshot）与后端 datasource 的
// 实际契约不一致。后端：tool key 带 wind_ 前缀、参数 snake_case、所有工具必传
// file_path、行情/K线/快照统一走 wind_get_price、日期用 YYYY-MM-DD。
// 后端契约映射见 references/ 下各领域契约文件（stock.md / fund.md / index.md 等）。
const API_NAME_MAP = {
  // stock_data
  search_stocks: 'wind_search_stocks',
  get_stock_price_indicators: 'wind_get_stock_price_indicators',
  get_stock_kline: 'wind_get_price',
  get_stock_quote: 'wind_get_stock_quote',
  get_stock_basicinfo: 'wind_get_stock_info',
  get_stock_fundamentals: 'wind_get_stock_financial_index',
  get_stock_equity_holders: 'wind_get_stock_equity_holders',
  get_stock_events: 'wind_get_stock_events',
  get_stock_technicals: 'wind_get_stock_technicals',
  get_risk_metrics: 'wind_get_risk_metrics',
  // fund_data
  search_funds: 'wind_search_funds',
  get_fund_price_indicators: 'wind_get_fund_price_indicators',
  get_fund_kline: 'wind_get_fund_price',
  get_fund_quote: 'wind_get_fund_quote',
  get_fund_info: 'wind_get_fund_info',
  get_fund_financials: 'wind_get_fund_financials',
  get_fund_holdings: 'wind_get_fund_holdings',
  get_fund_performance: 'wind_get_fund_performance',
  get_fund_holders: 'wind_get_fund_holders',
  get_fund_company_info: 'wind_get_fund_company_info',
  // index_data
  get_index_price_indicators: 'wind_get_index_price_indicators',
  get_index_kline: 'wind_get_index_price',
  get_index_quote: 'wind_get_index_quote',
  get_index_basicinfo: 'wind_get_index_basicinfo',
  get_index_fundamentals: 'wind_get_index_fundamentals',
  get_index_technicals: 'wind_get_index_technicals',
  // bond_data
  get_bond_basicinfo: 'wind_get_bond_basicinfo',
  get_bond_issuer_info: 'wind_get_bond_issuer_info',
  get_bond_market_data: 'wind_get_bond_market_data',
  get_bond_financial_data: 'wind_get_bond_financial_data',
  // financial_docs / analytics
  get_company_announcements: 'wind_get_company_announcements',
  get_financial_news: 'wind_get_financial_news',
  get_financial_data: 'wind_get_financial_data',
  // economic_data (EDB 宏观/行业指标)
  search_economic_indicator: 'wind_search_economic_indicator',
  query_economic_indicator_data: 'wind_query_economic_indicator_data',
};

// 行情三类各有独立后端工具（live describe 证实，非 collapse）：
//   K线   → wind_get_price / wind_get_fund_price / wind_get_index_price（要 start_date）
//   快照  → wind_get_stock_quote / _fund_quote / _index_quote（要 begin/end，无 frequency）
//   指标  → wind_get_stock_price_indicators / …（要 indexes，无日期）
// KLINE_TOOLS / QUOTE_TOOLS 已在上方定义（校验用），这里补 INDICATOR_TOOLS。
const INDICATOR_TOOLS = new Set([
  'get_stock_price_indicators', 'get_fund_price_indicators', 'get_index_price_indicators',
]);

// 前端 period 码 → 后端 frequency；前端 aftype → 后端 price_adj。
const PERIOD_TO_FREQ = {
  '1': '1min', '3': '5min', '4': '10min', '5': '15min', '6': '30min',
  '7': '60min', '8': '120min', '9': '240min',
  '10': 'D', '11': 'W', '12': 'M', '13': 'Y', '14': 'Q', '15': 'HY',
};
const ADJ_TO_CODE = { '0': 'F', '1': 'B' };

// yyyyMMdd → YYYY-MM-DD；已是带横杠或为空则原样返回。
function toDashDate(s) {
  const m = /^(\d{4})(\d{2})(\d{2})$/.exec(String(s ?? '').trim());
  return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
}

// 后端所有工具必传 file_path（CSV 落盘路径），前端自动注入，用户无需关心。
function backendCsvPath(apiName) {
  const dir = join(homedir(), '.cache', 'wind-allskill', 'csv');
  try { mkdirSync(dir, { recursive: true }); } catch {}
  return join(dir, `${apiName}_${process.pid}_${Date.now()}.csv`);
}

// 把前端 (toolName, args) 适配成后端 (apiName, params)。
// 从任意文本/入参里提取一个 Wind 代码（A股 600519.SH / 场内基金 588200.SH /
// 港股 00700.HK / 美股 AAPL.O 等）。用于 basicinfo/fundamentals：前端历史契约把
// 代码塞在 question 里，后端却要结构化 ticker。
function extractWindCode(text) {
  if (!text) return undefined;
  const m = /([0-9A-Za-z]{1,6}\.(?:SH|SZ|BJ|OF|HK|O|N))/.exec(String(text));
  return m ? m[1] : undefined;
}

function adaptForBackend(toolName, rawArgs) {
  const apiName = API_NAME_MAP[toolName] || `wind_${toolName}`;
  const a = { ...(rawArgs || {}) };
  let p;
  if (KLINE_TOOLS.has(toolName)) {
    // K线 → wind_get_price / wind_get_fund_price / wind_get_index_price。
    // start_date 后端必填；无 begin_date 时兜底今天。
    const today = new Date().toISOString().slice(0, 10);
    p = {
      ticker: a.windcode ?? a.ticker,
      start_date: toDashDate(a.begin_date) || today,
      end_date: toDashDate(a.end_date) || today,
      frequency: PERIOD_TO_FREQ[String(a.period)] || 'D',
      price_adj: ADJ_TO_CODE[String(a.aftype)] || 'F',
      fields: a.indexes || 'price',
    };
  } else if (QUOTE_TOOLS.has(toolName)) {
    // 快照 → wind_get_stock_quote / _fund_quote / _index_quote：ticker(+begin/end)，无日期必填。
    p = { ticker: a.windcode ?? a.ticker };
    if (a.begin) p.begin = a.begin;
    if (a.end) p.end = a.end;
  } else if (INDICATOR_TOOLS.has(toolName)) {
    // 指标 → wind_get_*_price_indicators：ticker + indexes(必填)。
    p = { ticker: a.windcode ?? a.ticker, indexes: a.indexes };
  } else if (toolName === 'get_stock_basicinfo') {
    // 后端 wind_get_stock_info 是结构化：ticker(必填) + 可选 fields(默认 stock_info)。
    p = { ticker: a.windcode ?? a.ticker ?? extractWindCode(a.question) ?? a.question };
    if (a.fields ?? a.indexes) p.fields = a.fields ?? a.indexes;
    if (a.trade_date) p.trade_date = toDashDate(a.trade_date);
  } else if (toolName === 'get_stock_fundamentals') {
    // 后端 wind_get_stock_financial_index 是结构化：ticker + indicators(必填)。
    // indicators 无则用后端预设 'financial_index'（返回一组常用财务指标）。
    p = {
      ticker: a.windcode ?? a.ticker ?? extractWindCode(a.question) ?? a.question,
      indicators: a.indicators ?? a.indexes ?? 'financial_index',
    };
    if (a.report_date) p.report_date = toDashDate(a.report_date);
    if (a.trade_date) p.trade_date = toDashDate(a.trade_date);
  } else if (toolName === 'query_economic_indicator_data') {
    // 宏观经济指标取数：契约使用 camelCase beginDate/endDate，后端使用 snake_case。
    p = { question: a.question };
    if (a.beginDate || a.endDate) {
      p.begin_date = toDashDate(a.beginDate);
      p.end_date = toDashDate(a.endDate);
    }
    if (a.observation != null) p.observation = String(a.observation);
    if (a.lang != null) p.lang = a.lang;
  } else {
    // question / query / ticker 类：直接透传，仅把 windcode 收敛为 ticker。
    p = { ...a };
    if (p.windcode != null && p.ticker == null) { p.ticker = p.windcode; delete p.windcode; }
  }
  if (p.file_path == null) p.file_path = backendCsvPath(apiName);
  for (const k of Object.keys(p)) if (p[k] === undefined || p[k] === null) delete p[k];
  return { apiName, params: p };
}

// section: 命令

// 通过 agent-gw 取数：spawn wind_tool.py，把网关返回文本包回 MCP result 信封，
// 保证调用方（各 Wind skill）原有的 content[0].text 解析路径不变。
function runWindTool(apiName, args, { timeoutMs = 600_000 } = {}) {
  return new Promise((resolve) => {
    // 有 data-source key → datasource 模式直连 big-py（MR 448 全量接口）；
    // 否则不传 --env，wind_tool.py 自动回退 agent-gw（保持原行为）。
    const dsArgs = WIND_DS_API_KEY
      ? ['--env', WIND_DS_ENV, '--base-url', WIND_DS_BASE_URL, '--api-key', WIND_DS_API_KEY]
      : [];
    let child;
    try {
      child = spawn(PYTHON_BIN, [
        WIND_TOOL_PY, 'call',
        '--data-source', 'wind',
        ...dsArgs,
        '--api-name', apiName,
        '--params-json', JSON.stringify(args ?? {}),
      ], { signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) {
      resolve({ status: -1, out: '', err: e.message });
      return;
    }
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', (e) => resolve({ status: -1, out, err: err || e.message }));
    child.on('close', (code) => resolve({ status: code, out, err }));
  });
}

async function gatewayCall(server_type, toolName, args) {
  // 前端工具名/参数 → 后端真实 api_name + snake_case 参数 + file_path。
  const { apiName, params } = adaptForBackend(toolName, args);
  const { status, out, err } = await runWindTool(apiName, params);
  if (status !== 0) {
    const detail = (err || out || '').trim() || `wind_tool.py 异常退出 (code=${status})`;
    // 先按"运行时/凭证"显式签名归类（优先于 Wind 后端语义匹配，
    // 否则 python traceback 里的 "NotFound" 会被 inferErrorCode 误判成 NO_RESULTS）。
    let code;
    if (/KIMI[ _]?API[ _]?KEY|Missing KIMI|agent-gw\.json|unauthorized|\b401\b|\b403\b|Forbidden|credential|Invalid api token|Missing Authorization|Missing datasource config/i.test(detail)) {
      code = 'AUTH_ERROR';        // 网关 / datasource 凭证缺失 / 无效
    } else if (/Missing dependency|ModuleNotFoundError|No module named|Traceback|ENOENT|command not found|SystemExit/i.test(detail)) {
      code = 'NETWORK_ERROR';     // 本地运行时 / 依赖 / 进程问题（detail 内含完整 traceback）
    } else {
      const inferred = inferErrorCode(detail);  // 命中 Wind 后端语义错误（NO_RESULTS / 参数 / 限流等）
      code = inferred === 'UNKNOWN' ? 'NETWORK_ERROR' : inferred;
    }
    const via = WIND_DS_API_KEY ? `datasource ${WIND_DS_BASE_URL}` : 'agent-gw';
    die(code, `${detail} (server=${server_type}, tool=${toolName}, api_name=${apiName}, via ${via})`);
  }
  return { content: [{ type: 'text', text: out.trim() }], isError: false };
}

async function cmdCall(server_type, toolName, paramsJson) {
  if (!server_type || !toolName || !paramsJson) {
    exitWithUsage(
      `用法：call <server_type> <tool_name> '<params_json>'\n` +
      `可用 server_type: ${Object.keys(SERVERS).join(' / ')}\n` +
      `典型：\n  ${CALL_EXAMPLES.join('\n  ')}`,
      1,
    );
  }

  let args;
  try {
    args = JSON.parse(paramsJson);
  } catch (e) {
    die('INVALID_PARAMS_JSON', `params JSON 解析失败：${e.message} | 原文：${paramsJson.slice(0, 200)}`);
  }

  ({ server_type, toolName, args } = normalizeCall(server_type, toolName, args));
  validateToolSelection(server_type, toolName);

  const validationErrors = validateBasicParams(args);
  validationErrors.push(...validateToolParams(toolName, args));
  if (validationErrors.length > 0) {
    die('PARAM_VALIDATION_ERROR', validationErrors.join('；'));
  }

  // 取数经 agent-gw（见 gatewayCall）；route/param 校验已在上方完成。
  const result = await gatewayCall(server_type, toolName, args);
  return {
    server_type,
    tool: toolName,
    result,
  };
}

// 诊断: 输出自动更新状态
async function cmdDiagnose() {
  let updateState = null;
  try {
    const stateFile = updateStateFile();
    if (existsSync(stateFile)) {
      updateState = JSON.parse(readFileSync(stateFile, 'utf8'));
    }
  } catch {
    updateState = { status: 'unreadable' };
  }
  return {
    platform: process.platform,
    node_pid: process.pid,
    update_scope: updateScope(),
    update_state_file: updateStateFile(),
    update_state: updateState,
    next_update_needed: !alreadyUpdatedToday(),
  };
}

// section: 主入口 — IS_MAIN guard 让单元测试 import 不副作用
const IS_MAIN = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (IS_MAIN) runMain();

function runMain() {
const [cmd, ...args] = process.argv.slice(2);

const USAGE =
  `wind-mcp-skill\n` +
  `访问万得 Wind 金融数据（按数据域分类调用）\n\n` +
  `用法:\n` +
  `  cli.mjs call <server_type> <tool_name> '<params_json>'\n` +
  `  # 本插件不接收/不存储任何 Wind 账号或密钥。\n\n` +
  `可用 server_type:\n` +
  Object.entries(SERVERS).map(([k, v]) => `  ${k.padEnd(20)}${v.label}`).join('\n') + '\n\n' +
  `典型:\n` +
  `  ${CALL_EXAMPLES.join('\n  ')}`;

const commands = {
  call: () => cmdCall(args[0], args[1], args[2]),
  diagnose: () => cmdDiagnose(),
};

if (!cmd) {
  // help: 直接输出 USAGE 纯文本
  process.stdout.write(USAGE + '\n');
  process.exit(0);
}

if (!commands[cmd]) {
  die('USAGE_ERROR', `未知命令: ${cmd}\nUSAGE:\n${USAGE}`);
}

commands[cmd]()
  .then((data) => {
    if (cmd === 'call') {
      // call: 透传 result 内容 (parse JSON if applicable, else raw text)
      writeRawCallSuccess(data?.result);
      setTimeout(triggerUpdateCheck, 0);
    } else {
      // diagnose: 直接输出结构化数据 (无 envelope 包裹)
      writePlainSuccess(data);
    }
  })
  .catch((err) => {
    die('UNKNOWN', `执行失败: ${err.message || err}${err.stack ? ' | stack: ' + err.stack.slice(0, 300) : ''}`);
  });
}
