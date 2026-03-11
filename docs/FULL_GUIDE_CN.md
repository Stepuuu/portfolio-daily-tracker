# Portfolio Daily Tracker — 完整使用指南

> 面向从零开始的外部用户，教你如何安装、配置并使用 portfolio-daily-tracker 的全部功能。

GitHub: https://github.com/Stepuuu/portfolio-daily-tracker

---

## 目录

1. [功能模块概览](#1-功能模块概览)
2. [第一步：克隆与安装](#2-第一步克隆与安装)
3. [第二步：配置 AI API 模型](#3-第二步配置-ai-api-模型)
4. [第三步：配置持仓数据（holdings）](#4-第三步配置持仓数据holdings)
5. [第四步：配置投资组合引擎](#5-第四步配置投资组合引擎)
6. [第五步：启动 Web 界面](#6-第五步启动-web-界面)
7. [第六步：运行快照引擎](#7-第六步运行快照引擎)
8. [第七步（可选）：配置 OpenClaw AI Agent 技能](#8-第七步可选配置-openclaw-ai-agent-技能)
9. [第八步（可选）：飞书 / Telegram 每日推送](#9-第八步可选飞书--telegram-每日推送)
10. [日常使用流程](#10-日常使用流程)
11. [常见问题](#11-常见问题)

---

## 1. 功能模块概览

本项目有三个层次，可以按需使用：

```
层次 1 ── 纯引擎（无界面，无 AI）
          只需 Python + requests
          功能：拉取股价 → 计算市值/盈亏 → 生成日报文本

层次 2 ── Web 面板（有界面，有 AI 对话）
          需要 Python + Node.js + AI API Key
          功能：React 面板 + AI 问答 + 回测 + 7个功能页

层次 3 ── OpenClaw Agent 技能
          需要安装 OpenClaw + 飞书/Telegram Bot
          功能：自然语言对话管理持仓，每日自动推送日报
```

---

## 2. 第一步：克隆与安装

### 前置要求
- Python 3.9+
- Node.js 18+（仅层次 2 需要）
- Git

### 一键安装（推荐）

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker

make setup
```

`make setup` 会自动：
1. 安装 Python 依赖（`dashboard/requirements.txt`）
2. 安装前端 npm 依赖（`dashboard/frontend/`）
3. 复制配置模板（`config.example.json` → `config.json`）
4. 创建数据目录

> ⚠️ 如果提示 `pip` 找不到包，请用 `python3 -m pip install -r dashboard/requirements.txt`

---

## 3. 第二步：配置 AI API 模型

**文件：** `dashboard/config.json`（由 `config.example.json` 复制而来）

AI 功能（对话助手、智能分析）需要至少配置一个 API Key。

### 方案 A：OpenAI

```json
{
  "current_api_group": "openai_official",
  "api_groups": {
    "openai_official": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxxxxxxxxxxxxxxx",
      "provider_type": "openai",
      "models": [
        { "id": "gpt-4o", "name": "GPT-4o", "supports_vision": true },
        { "id": "gpt-4o-mini", "name": "GPT-4o Mini", "supports_vision": true }
      ]
    }
  }
}
```

### 方案 B：Anthropic Claude

```json
{
  "current_api_group": "anthropic_official",
  "api_groups": {
    "anthropic_official": {
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-ant-xxxxxxxxxxxxxxxx",
      "provider_type": "claude",
      "models": [
        { "id": "claude-opus-4-5", "name": "Claude Opus", "supports_vision": true }
      ]
    }
  }
}
```

### 方案 C：DeepSeek（国内推荐，性价比高）

```json
{
  "current_api_group": "deepseek",
  "api_groups": {
    "deepseek": {
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-xxxxxxxxxxxxxxxx",
      "provider_type": "openai",
      "models": [
        { "id": "deepseek-chat", "name": "DeepSeek Chat", "supports_vision": false }
      ]
    }
  }
}
```

### 方案 D：任意 OpenAI 兼容 API（中转站 / 本地 Ollama）

```json
{
  "current_api_group": "my_proxy",
  "api_groups": {
    "my_proxy": {
      "base_url": "https://your-proxy.com/v1",
      "api_key": "sk-xxxxxxxxxxxxxxxx",
      "provider_type": "openai",
      "models": [
        { "id": "gpt-4o", "name": "GPT-4o via proxy", "supports_vision": true }
      ]
    }
  }
}
```

> 💡 **不配置 API Key 也能启动**：Web 界面会正常打开，只有 AI 对话页面无法使用，其他页面（持仓总览、行情、回测）正常工作。

---

## 4. 第三步：配置持仓数据（holdings）

持仓数据是系统的核心，决定了你的资产如何被追踪。

### 文件位置

```
engine/portfolio/holdings/YYYY-MM-DD.json   ← 每天一个文件（以今日日期命名）
```

### 创建今日持仓文件

```bash
cp engine/portfolio/holdings/example.json \
   engine/portfolio/holdings/$(date +%Y-%m-%d).json
```

### 格式详解

```json
{
  "date": "2026-03-11",
  "updated_at": "2026-03-11T16:00:00+08:00",
  "groups": {
    "进攻": {
      "cost_basis": 500000,
      "positions": [
        {
          "name": "贵州茅台",
          "ticker": "SHA:600519",
          "quantity": 10,
          "cost_price": 1680.00
        },
        {
          "name": "腾讯控股",
          "ticker": "HKG:0700",
          "quantity": 200,
          "cost_price": 380.00
        },
        {
          "name": "苹果",
          "ticker": "NASDAQ:AAPL",
          "quantity": 50,
          "cost_price": 180.00
        }
      ],
      "fund": 10000,
      "cash": 50000
    },
    "稳健": {
      "cost_basis": 300000,
      "positions": [
        {
          "name": "沪深300ETF",
          "ticker": "SHA:510300",
          "quantity": 5000,
          "cost_price": 4.20
        }
      ],
      "fund": 0,
      "cash": 80000
    }
  }
}
```

### Ticker 格式规则

| 市场 | 前缀 | 示例 |
|------|------|------|
| 上交所 A 股 | `SHA:` | `SHA:600519`（茅台）|
| 深交所 A 股 | `SHE:` | `SHE:000858`（五粮液）|
| 港股 | `HKG:` | `HKG:0700`（腾讯）|
| 纳斯达克 | `NASDAQ:` | `NASDAQ:AAPL` |
| 纽交所 | `NYSE:` | `NYSE:BRK-B` |

### 字段说明

| 字段 | 说明 |
|------|------|
| `cost_basis` | 该组总成本基数（元），用于计算总体盈亏率 |
| `quantity` | 当前持有股数（更新时直接修改为新总数） |
| `cost_price` | 持仓均价（元/港元/美元） |
| `fund` | 该组持有的基金净值（元） |
| `cash` | 该组现金余额（元） |

---

## 5. 第四步：配置投资组合引擎

**文件：** `engine/portfolio/config.json`

```json
{
  "groups": {
    "进攻": {"cost_basis": 500000, "label": "进攻 (成本50万)"},
    "稳健": {"cost_basis": 300000, "label": "稳健 (成本30万)"}
  },
  "data_dir": "./portfolio",
  "proxy": "",
  "ticker_map": {
    "茅台": "SHA:600519",
    "腾讯": "HKG:0700",
    "苹果": "NASDAQ:AAPL"
  },
  "currency_map": {
    "SHA": "CNY", "SHE": "CNY",
    "HKG": "HKD",
    "NASDAQ": "USD", "NYSE": "USD"
  },
  "fx_tickers": {
    "HKD_CNY": "HKDCNY=X",
    "USD_CNY": "USDCNY=X"
  }
}
```

### 关键配置项

- `groups`：必须与 holdings 文件中的组名 **完全一致**
- `ticker_map`：股票中文名 → ticker 的映射，供 AI 对话时识别（写越多，AI 越准确）
- `proxy`：如果访问 Yahoo Finance 需要代理，填 `"http://127.0.0.1:7890"` 这类格式

---

## 6. 第五步：启动 Web 界面

```bash
make start
```

或手动启动：

```bash
# 后端（终端1）
cd dashboard
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 前端（终端2）
cd dashboard/frontend
npm run dev
```

打开 http://localhost:3000，你会看到 8 个功能页面：

| 页面 | 功能 |
|------|------|
| 💬 Chat | AI 对话助手，问持仓/分析/建议 |
| 📊 Dashboard | 总览：KPI 卡片、持仓饼图、月度盈亏 |
| 📈 Portfolio | 详细持仓表、历史净值曲线 |
| 📉 Market | 实时行情看板 |
| 🔄 Backtest | 策略回测引擎（MA/RSI/自定义） |
| 🧠 Memory | AI 对话记忆管理 |
| 📋 Tracker | 持仓变更历史 |
| ⚙️ Settings | 模型切换、API Key 配置 |

---

## 7. 第六步：运行快照引擎

快照引擎会从 Yahoo Finance / AKShare 拉取实时股价，计算你的完整持仓状态。

```bash
make snapshot
# 或
cd engine && python3 scripts/portfolio_snapshot.py
```

生成结果保存在：
- `engine/portfolio/snapshots/YYYY-MM-DD.json` — 完整快照（含价格、市值、盈亏、Sharpe等）
- `engine/portfolio/history.csv` — 历史时序数据

### 生成日报文本

```bash
cd engine && python3 scripts/portfolio_report.py
```

输出 Markdown 格式的日报，包括总市值、当日涨跌、持仓明细、风险指标。

---

## 8. 第七步（可选）：配置 OpenClaw AI Agent 技能

OpenClaw 是一个可以接收飞书/Telegram 消息、用 AI 自动管理持仓的 Agent 框架。配置后，你可以直接给 Bot 发消息「今天卖了500股茅台」，它会自动更新持仓并生成日报。

### 8.1 安装 OpenClaw

```bash
npm install -g openclaw
openclaw setup
# 按提示选择飞书 or Telegram 作为 channel
```

### 8.2 安装本项目技能

```bash
# 方式1：从 ClawHub 直接安装（推荐）
clawhub install portfolio-daily-tracker

# 方式2：本地安装（调试用）
openclaw skill install ./openclaw/skills/
```

### 8.3 配置技能

技能配置文件由 ClawHub 自动生成，或手动创建：

```bash
cp openclaw/examples/config.example.json \
   ~/.openclaw/skills/portfolio-daily-tracker/config.json
```

编辑该文件，设置：
- `feishu_chat_id`：你的飞书 Bot 用户 ID（`user:ou_xxx`）
- `notification_schedule.notify_time`：每天通知时间（如 `"18:00"`）

### 8.4 配置环境变量

在你的 OpenClaw 配置中，或系统环境中设置：

```bash
# LLM API（必须，用于 Agent 推理）
export OPENAI_API_KEY="sk-xxx"
# 或 export ANTHROPIC_API_KEY="sk-ant-xxx"

# 持仓数据目录（必须指向你的项目路径）
export PORTFOLIO_DIR="/path/to/portfolio-daily-tracker/engine/portfolio"

# 飞书推送（可选）
export FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# Telegram 推送（可选）
export TELEGRAM_BOT_TOKEN="xxx:xxx"
export TELEGRAM_CHAT_ID="xxx"
```

### 8.5 启动 Gateway

```bash
openclaw gateway
```

之后给你的飞书/Telegram Bot 发消息，Agent 就会响应。

### 技能支持的自然语言命令示例

```
"今天卖了500股茅台"              → 自动更新持仓 + 运行快照
"加了200股腾讯，均价380"         → 更新持仓并记录成本
"生成今日日报"                   → 快照 + 生成报告
"现在持仓总市值多少"             → 查询最新快照
"稳健组现金增加5万"              → 更新 cash 字段
"回撤多少了"                     → 读取快照分析最大回撤
```

---

## 9. 第八步（可选）：飞书 / Telegram 每日推送

即使不用 OpenClaw Agent，也可以单独配置自动推送。

### Python 调度器（无需 cron）

```bash
cd engine && python3 scripts/portfolio_scheduler.py
```

默认行为：
- **18:00**：发送通知，询问今日持仓变化
- **19:00**：无论是否有回复，自动运行完整流水线并推送日报

### 手动触发全流程

```bash
cd engine && python3 scripts/portfolio_daily_update.py \
  --action update \
  --date $(date +%Y-%m-%d) \
  --text "今日未变化"
```

---

## 10. 日常使用流程

### 最简流程（纯引擎，每天 3 个命令）

```bash
# 1. 更新今日持仓（手动编辑 JSON 或用 portfolio_manager.py）
vim engine/portfolio/holdings/$(date +%Y-%m-%d).json

# 2. 生成快照
cd engine && python3 scripts/portfolio_snapshot.py

# 3. 生成日报
python3 scripts/portfolio_report.py
```

### Web 面板流程（每天）

```bash
make start          # 启动服务
# 打开 http://localhost:3000
# → Dashboard 看今日概览
# → Chat 问 AI："今日持仓表现如何？有什么风险？"
# → Backtest 测试你想加仓的股票
```

### OpenClaw Agent 流程（全自动）

```
18:00 Bot 发消息："今日有什么变化？"
→ 你回复："卖了500股茅台，加了200股腾讯均价380"
→ Bot 自动更新持仓 + 生成快照 + 推送日报
或
→ 你回复："未变化"
→ Bot 直接生成快照 + 推送日报
```

---

## 11. 常见问题

### Q: 启动后 Web 界面白屏

检查浏览器控制台是否有 `bg-primary-xxx class does not exist` 错误。  
**解决**：确认 `tailwind.config.js` 包含 `primary` 颜色定义。更新到最新版本（`git pull`）即可。

### Q: 股价拉取失败 / Yahoo Finance 超时

A 股/港股/美股价格通过 Yahoo Finance 获取，国内访问可能需要代理。  
在 `engine/portfolio/config.json` 中设置：
```json
{ "proxy": "http://127.0.0.1:7890" }
```

### Q: AI 对话没有反应 / 报错 401

检查 `dashboard/config.json` 中 `current_api_group` 对应的 `api_key` 是否正确填写。

### Q: requirements.txt 安装失败，找不到包

可能是 pip 指向了内部镜像。指定 PyPI：
```bash
python3 -m pip install -r dashboard/requirements.txt -i https://pypi.org/simple/
```

### Q: 持仓数量如何更新？是增量还是绝对值？

**绝对值**。`quantity` 字段填的是**当前总持有量**，不是本次买卖数量。  
例如原持有 1000 股，卖了 200 股，`quantity` 改为 `800`。

### Q: 多个组的成本基数 cost_basis 有什么用？

用于计算该组的整体盈亏率：
```
盈亏率 = (当前该组总市值 - cost_basis) / cost_basis × 100%
```
`cost_basis` 设置为你当初为该组投入的**总本金**。

### Q: fund 字段是什么？

`fund` 是该组持有的货币基金/理财净值（元）。如果你有活期宝、余额宝等货币基金，填在这里，会计入组总市值。

---

## 结构速查

```
portfolio-daily-tracker/
├── engine/                          # 核心引擎（不依赖 Node.js）
│   ├── scripts/
│   │   ├── portfolio_snapshot.py    # 拉价格 + 计算所有指标
│   │   ├── portfolio_report.py      # 生成 Markdown 日报
│   │   ├── portfolio_manager.py     # 持仓管理 CLI
│   │   ├── portfolio_daily_update.py # 全流程一键脚本
│   │   └── portfolio_scheduler.py   # Python 定时调度器
│   └── portfolio/
│       ├── config.json              # ← 你要编辑
│       ├── holdings/
│       │   └── YYYY-MM-DD.json      # ← 你要创建/编辑
│       ├── snapshots/               # 自动生成
│       └── history.csv              # 自动生成
│
├── dashboard/                       # Web 面板（需要 Node.js）
│   ├── config.json                  # ← 你要编辑（填 API Key）
│   ├── backend/                     # FastAPI，端口 :8000
│   └── frontend/                    # React + Vite，端口 :3000
│
├── openclaw/                        # OpenClaw Agent 集成
│   ├── skills/SKILL.md              # Agent 技能说明
│   └── examples/config.example.json
│
├── Makefile                         # make setup / make start / make snapshot
├── start.sh                         # 启动脚本
└── docker-compose.yml               # Docker 一键启动
```

---

*Portfolio Daily Tracker — MIT License — https://github.com/Stepuuu/portfolio-daily-tracker*
