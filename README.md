<div align="center">

# 📊 Portfolio Daily Tracker

**A comprehensive self-hosted investment portfolio tracking & AI trading assistant**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org)

**Language / 语言**

[🇬🇧 English](#english-documentation) · [🇨🇳 中文文档](#中文文档)

</div>

---

<a id="english-documentation"></a>

## 🇬🇧 English Documentation

### ✨ Features

| Feature | Description |
|---------|-------------|
| 🌍 **Multi-market** | A-shares (SHA/SHE), HK (HKG), US (NASDAQ/NYSE) in one view |
| 💱 **Auto FX** | Real-time HKD/CNY, USD/CNY rates — all assets in CNY |
| 📈 **Quant Metrics** | Sharpe ratio, volatility, max drawdown, win rate, P&L ratio |
| 💰 **Margin Tracking** | Negative cash = margin loan; auto-calculates leverage ratio |
| 🤖 **AI Chat Assistant** | Natural language Q&A about positions, P&L, risk using GPT/Claude/DeepSeek |
| 📊 **Web Dashboard** | React + TailwindCSS with 5 KPI cards, pie charts, equity curve, monthly P&L |
| 📉 **Backtesting** | Strategy backtesting engine with MA cross, RSI, custom strategies |
| 🔔 **Daily Push** | Auto-notify after market close → confirm changes → push report to Feishu/Telegram |
| ⏰ **Auto Scheduling** | Python scheduler (no cron needed) — 18:00 notify, 19:00 failsafe pipeline |

### 📁 Project Structure

```
portfolio-daily-tracker/
├── engine/                      # 🔧 Portfolio Snapshot Engine
│   ├── scripts/
│   │   ├── portfolio_snapshot.py      # Core: fetch quotes → calculate → save
│   │   ├── portfolio_daily_update.py  # Daily: clone → parse changes → pipeline
│   │   ├── portfolio_report.py        # Markdown report generator
│   │   ├── portfolio_manager.py       # Holdings management CLI
│   │   ├── portfolio_scheduler.py     # Python scheduler (cron alternative)
│   │   ├── portfolio-cron.sh          # Cron wrapper script
│   │   └── portfolio-daily.sh         # Traditional daily script
│   └── portfolio/
│       ├── config.example.json        # Config template
│       └── holdings/example.json      # Holdings format example
│
├── dashboard/                   # 📱 Full Trading Assistant Web App
│   ├── main.py                        # App entry point
│   ├── config.example.json            # Config template (copy → config.json)
│   ├── config/                        # Config module
│   ├── core/                          # LLM, memory, models, tools
│   ├── providers/                     # LLM & market data providers
│   ├── agents/                        # AI trader agent
│   ├── backtesting/                   # Backtesting engine
│   ├── backend/                       # FastAPI REST API
│   │   ├── api/                       # Route handlers (chat/market/memory/backtest...)
│   │   ├── services/                  # AgentService
│   │   └── main.py                    # API entry point
│   └── frontend/                      # React + TailwindCSS
│       └── src/
│           ├── pages/                 # Dashboard, Portfolio, Market, Memory, Backtest...
│           ├── components/            # ChatPanel, Layout, Sidebar...
│           └── services/              # API client layer
│
├── openclaw/                    # 🦞 OpenClaw Agent Integration
│   ├── tools/portfolio_tools.py       # Agent tool definitions
│   ├── skills/SKILL.md
│   └── examples/config.example.json
│
├── v1-google-sheets/            # 📋 V1: Google Sheets version (archived)
├── docker-compose.yml
└── README.md
```

### 🚀 Quick Start

#### Option 1: Engine Only (Portfolio Tracker)

Minimal setup — just Python + `requests`:

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker/engine

pip install requests

# Configure
cp portfolio/config.example.json portfolio/config.json
# Edit config.json: set proxy, group names, cost basis

# Initialise holdings
mkdir -p portfolio/holdings
# Create today's holdings file (see format below)

# Generate snapshot
python scripts/portfolio_snapshot.py

# View report
python scripts/portfolio_report.py
```

#### Option 2: Full Trading Assistant (Recommended)

```bash
cd dashboard

# Install Python deps
pip install -r requirements.txt

# Configure
cp config.example.json config.json
# Edit config.json: add your AI API key (OpenAI / Claude / DeepSeek)

# Start backend (port 8000)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# Start frontend (port 3000)
cd frontend && npm install && npm run dev
# Open http://localhost:3000
```

#### Option 3: Docker Compose

```bash
# Configure engine/portfolio/config.json and dashboard/config.json first
docker compose up -d
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
```

### 🏗 Architecture

```
┌──────────────────────────────────────────────────────┐
│                    Data Sources                       │
│  Yahoo Finance  ·  AKShare  ·  holdings/*.json        │
└─────────────────────┬────────────────────────────────┘
                      ▼
┌──────────────────────────────────────────────────────┐
│          portfolio_snapshot.py  (~450 lines)          │
│  fetch_prices() → fetch_fx() → calculate() →          │
│  quant_metrics() → save_snapshot() → sync()           │
└────────────────┬────────────────┬────────────────────┘
                 ▼                ▼
        ┌────────────┐   ┌─────────────────────────────┐
        │ Snapshot   │   │   FastAPI Backend (port 8000)│
        │ JSON + CSV │   │  /api/chat /api/market       │
        └────────────┘   │  /api/memory /api/backtest   │
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │  React Frontend (port 3000)  │
                         │  Chat · Portfolio · Market   │
                         │  Memory · Backtest · Tracker │
                         └─────────────────────────────┘
                                        ▲
                         ┌─────────────────────────────┐
                         │  OpenClaw 🦞 AI Agent        │
                         │  Feishu / Telegram push      │
                         └─────────────────────────────┘
```

### 📅 Daily Workflow

```
18:00  ─ Scheduler fires
       1. Clone previous day's holdings → today.json
       2. Push Feishu message: "Any position changes today?"

       ← User replies: "Sold 500 shares KWEICHOW, cash now -50k"
         or: "No changes"

       3. Agent parses natural language → update holdings JSON
       4. Fetch live prices (Yahoo Finance)
       5. Calculate snapshot (P&L / drawdown / quant metrics)
       6. Generate Markdown daily report
       7. Push report to Feishu/Telegram
       8. Sync Web Dashboard

19:00  ─ Failsafe: auto-run if no user response
```

**Start scheduler:**
```bash
nohup python engine/scripts/portfolio_scheduler.py &
# Default: notify at 18:00, pipeline at 19:00
```

### 🤖 Full AI Trading Assistant

This project ships a **complete multi-page AI trading assistant** — far beyond a simple portfolio tracker. Once you run `docker compose up`, you get a React web app with 8 fully functional pages driven by a FastAPI backend + LLM integration.

```
┌──────────────────────────────────────────────────────────────────────┐
│  💬 Chat  │ 💼 Portfolio │ 📊 Tracker │ 📈 Market │ 🧠 Memory │ 📉 Backtest │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────┐  ┌─────────────────────────┐   │
│  │  💬 AI Assistant                │  │  💼 Live Holdings        │   │
│  │                                 │  │  进攻   ¥62.4万  +2.3%   │   │
│  │  You: 今天持仓分析              │  │  稳健   ¥76.8万  -0.8%   │   │
│  │  🤖: 茅台今日 +2.3%，拉动       │  ├─────────────────────────┤   │
│  │  进攻组上涨。平安压力较大，      │  │  💡 Suggestions          │   │
│  │  建议关注杠杆比例...            │  │  · 平安杠杆偏高，考虑减仓 │   │
│  │                                 │  │  · 茅台突破前高，可持有   │   │
│  │  You: 帮算一下夏普比率          │  └─────────────────────────┘   │
│  │  🤖: 当前夏普 1.23，波动率      │                                  │
│  │  54.2%，处于合理区间...         │                                  │
│  └─────────────────────────────────┘                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

#### Page-by-page capabilities

**💬 Dashboard (AI Chat)**
- Stream-based conversation with GPT-4o / Claude / DeepSeek
- Context-aware: LLM receives live portfolio snapshot on every message
- Live portfolio sidebar — positions, P&L, daily change updated in real time
- AI-generated trade suggestions panel (actionable, not just analysis)
- Conversation history with search

**💼 Portfolio**
- Add / edit / delete positions with inline forms
- Grouped accounts (Aggressive / Conservative or any custom groups)
- Negative cash = margin loan, auto-renders leverage badge
- Cost basis tracking per group, export to JSON

**📊 Tracker**
- 5 KPI cards: Total Assets · Total P&L · Daily P&L · Monthly P&L · Max Drawdown
- Dual pie charts per account group for position distribution
- Equity curve vs. cost baseline (interactive area chart)
- Monthly P&L collapsible bar chart
- Full quant metrics table: Sharpe · Volatility · Win Rate · Profit Ratio · Avg Gain/Loss

**📈 Market**
- Real-time quote search across A-share / HK / US
- Watchlist with price change indicators
- Price alert configuration (notify when crossing threshold)
- Powered by AKShare (A/HK) + Yahoo Finance (US)

**🧠 Memory**
- Persistent user profile: investment style, risk preference, strategy notes
- Auto-extracts key decisions from chat history (LLM-powered summarisation)
- Trade diary: date-stamped notes attached to individual transactions
- Strategy library — save and recall your own rules and rationales

**📉 Backtest**
- Built-in strategies: MA Cross, RSI Mean Reversion, SMA Cross
- Custom strategy plugin support
- Configurable: symbol, date range, initial capital, commission, slippage
- Results: total return, annualised return, Sharpe, max drawdown, trade log
- **AI Reflection**: LLM analyses why the strategy worked/failed and suggests improvements
- Equity curve chart, benchmark comparison

**⚙️ Settings**
- Switch LLM provider and model at runtime (no restart)
- Configure API keys, proxy, data providers
- Feishu / Telegram webhook setup

| Page | Route | Backend endpoint |
|------|-------|------------------|
| Dashboard | `/` | `POST /api/chat` |
| Portfolio | `/portfolio` | `GET/POST /api/portfolio` |
| Tracker | `/tracker` | `GET /api/portfolio/tracker` |
| Market | `/market` | `GET /api/market/quote` |
| Memory | `/memory` | `GET/POST /api/memory` |
| Backtest | `/backtest` | `POST /api/backtest/run` |
| Settings | `/settings` | `GET/POST /api/settings` |

### 📝 Configuration

`engine/portfolio/config.json`:
```json
{
  "groups": {
    "Aggressive": { "cost_basis": 600000 },
    "Conservative": { "cost_basis": 400000 }
  },
  "proxy": "http://127.0.0.1:7890",
  "feishu_chat_id": "your_feishu_chat_id",
  "ticker_map": {
    "Kweichow Moutai": "SHA:600519",
    "Ping An Insurance": "SHA:601318",
    "Tencent": "HKG:0700",
    "Apple": "NASDAQ:AAPL"
  }
}
```

**Holdings format** (`holdings/YYYY-MM-DD.json`):
```json
{
  "date": "2026-03-06",
  "groups": {
    "Aggressive": {
      "cost_basis": 600000,
      "positions": [
        { "name": "Kweichow Moutai", "ticker": "SHA:600519", "quantity": 100, "cost_price": 1580.00 },
        { "name": "Tencent",         "ticker": "HKG:0700",   "quantity": 500, "cost_price": 398.60 }
      ],
      "fund": 50000,
      "cash": -85000
    },
    "Conservative": {
      "cost_basis": 400000,
      "positions": [
        { "name": "Ping An Insurance", "ticker": "SHA:601318", "quantity": 2000, "cost_price": 42.50 }
      ],
      "fund": 80000,
      "cash": 130000
    }
  }
}
```

### Ticker Format

| Market | Format | Example |
|--------|--------|---------|
| A-shares (Shanghai) | `SHA:XXXXXX` | `SHA:600519` |
| A-shares (Shenzhen) | `SHE:XXXXXX` | `SHE:000858` |
| Hong Kong | `HKG:XXXX` | `HKG:0700` |
| US | `NASDAQ:XX` / `NYSE:XX` | `NASDAQ:AAPL` |

### 🔄 Natural Language Updates

| Pattern | Example | Action |
|---------|---------|--------|
| No change | `No changes today` | Keep holdings, run pipeline |
| Cash update | `Aggressive cash now -50k` | Update group cash |
| Fund update | `Fund changed to 20k` | Update fund amount |
| Sell | `Sold 500 shares Moutai` | Reduce position |
| Buy | `Bought 200 shares Ping An` | Increase position |
| Set qty | `Tencent 800 shares` | Set quantity directly |
| Clear | `Cleared Moutai position` | Remove position |
| New position | `New: Apple ticker:NASDAQ:AAPL qty:50 cost:175.5` | Add new position |
| Cost adjust | `Aggressive cost basis 620000` | Adjust group cost basis |

Multiple changes can be comma/semicolon separated. Supports `万` (10k) suffix in Chinese.

### 📋 V1: Google Sheets Version

The original V1 uses Google Sheets + Apps Script. Suitable for users who don't want to self-host:
- `v1-google-sheets/src/code.gs` — Apps Script source
- `v1-google-sheets/template/*.xlsx` — Spreadsheet templates
- `v1-google-sheets/docs/User_Guide_EN.md` — English guide

### 🆚 Version Comparison

| Feature | V1 (Google Sheets) | V2 (Self-hosted) |
|---------|--------------------|-------------------|
| Data Storage | Google Drive | Local JSON/CSV |
| Price Source | Google Finance + Sina | Yahoo Finance + AKShare |
| Multi-market | Partial | ✅ A + HK + US |
| Quant Metrics | ❌ | ✅ Sharpe / Volatility / Drawdown |
| AI Chat | ❌ | ✅ GPT / Claude / DeepSeek |
| Backtesting | ❌ | ✅ Custom strategy engine |
| Web Dashboard | Google Sheets charts | ✅ React + TailwindCSS |
| Push Notifications | ❌ | ✅ Feishu / Telegram / Slack |
| Privacy | Data on Google | ✅ Fully private |
| Docker | ❌ | ✅ One command deploy |

### 📄 License

MIT — see [LICENSE](LICENSE)

---

<a id="中文文档"></a>

## 🇨🇳 中文文档

> [⬆️ 回到顶部 / Back to English](#english-documentation)

### ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🌍 **多市场支持** | A 股 (SHA/SHE)、港股 (HKG)、美股 (NASDAQ/NYSE) 统一管理 |
| 💱 **自动汇率** | 实时 HKD/CNY、USD/CNY，所有资产折算人民币 |
| 📈 **量化指标** | 夏普比率、年化波动率、最大回撤、胜率、盈亏比 |
| 💰 **融资杠杆** | 负现金自动识别融资额，计算杠杆比例 |
| 🤖 **AI 对话助手** | 自然语言问持仓、盈亏、风险，支持 GPT/Claude/DeepSeek |
| 📊 **Web 面板** | React + TailwindCSS，5 KPI · 双饼图 · 净值曲线 · 月度收益 |
| 📉 **策略回测** | 内置双均线/RSI/自定义策略回测引擎 |
| 🔔 **每日推送** | 收盘后自动通知 → 确认变化 → 推送飞书/Telegram 日报 |
| ⏰ **自动调度** | Python 调度器（无需 cron），18:00 通知，19:00 兜底管道 |

### 📁 项目结构

```
portfolio-daily-tracker/
├── engine/                      # 🔧 投资组合快照引擎
│   ├── scripts/
│   │   ├── portfolio_snapshot.py      # 核心：获取行情 → 计算 → 保存
│   │   ├── portfolio_daily_update.py  # 每日：克隆 → 解析变更 → 管道
│   │   ├── portfolio_report.py        # Markdown 报告生成
│   │   ├── portfolio_manager.py       # 持仓管理 CLI
│   │   ├── portfolio_scheduler.py     # Python 调度器（替代 cron）
│   │   ├── portfolio-cron.sh          # Cron 入口脚本
│   │   └── portfolio-daily.sh         # 传统每日脚本
│   └── portfolio/
│       ├── config.example.json        # 配置模板
│       └── holdings/example.json      # 持仓格式示例
│
├── dashboard/                   # 📱 完整交易助手 Web 应用
│   ├── main.py                        # 应用入口
│   ├── config.example.json            # 配置模板（复制为 config.json）
│   ├── config/                        # 配置模块
│   ├── core/                          # LLM、记忆、模型、工具
│   ├── providers/                     # LLM 和行情数据提供者
│   ├── agents/                        # AI 交易 Agent
│   ├── backtesting/                   # 策略回测引擎
│   ├── backend/                       # FastAPI REST API
│   │   ├── api/                       # 路由处理器（对话/行情/记忆/回测等）
│   │   ├── services/                  # AgentService
│   │   └── main.py                    # API 入口
│   └── frontend/                      # React + TailwindCSS
│       └── src/
│           ├── pages/                 # 面板/持仓/行情/记忆/回测 等页面
│           ├── components/            # ChatPanel/Layout/Sidebar 等组件
│           └── services/              # API 客户端层
│
├── openclaw/                    # 🦞 OpenClaw Agent 集成
├── v1-google-sheets/            # 📋 V1：Google Sheets 版本（归档）
├── docker-compose.yml
└── README.md
```

### 🚀 快速开始

#### 方式 1：仅引擎（投资组合追踪器）

最简部署 — 只需 Python + `requests`：

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker/engine

pip install requests

# 配置
cp portfolio/config.example.json portfolio/config.json
# 编辑 config.json：设置 proxy、分组名、成本基础

# 初始化持仓
mkdir -p portfolio/holdings
# 创建今日持仓文件（见格式说明）

# 生成快照
python scripts/portfolio_snapshot.py

# 查看报告
python scripts/portfolio_report.py
```

#### 方式 2：完整交易助手（推荐）

```bash
cd dashboard

# 安装 Python 依赖
pip install -r requirements.txt

# 配置
cp config.example.json config.json
# 编辑 config.json：添加 AI API Key（OpenAI / Claude / DeepSeek）

# 启动后端（端口 8000）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# 启动前端（端口 3000）
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3000
```

#### 方式 3：Docker Compose 一键启动

```bash
# 先配置 engine/portfolio/config.json 和 dashboard/config.json
docker compose up -d
# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs
```

### 🏗 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    数据来源                           │
│  Yahoo Finance  ·  AKShare  ·  holdings/*.json        │
└─────────────────────┬────────────────────────────────┘
                      ▼
┌──────────────────────────────────────────────────────┐
│          portfolio_snapshot.py（~450 行）              │
│  fetch_prices() → fetch_fx() → calculate() →          │
│  quant_metrics() → save_snapshot() → sync()           │
└────────────────┬────────────────┬────────────────────┘
                 ▼                ▼
        ┌────────────┐   ┌─────────────────────────────┐
        │ 快照 JSON  │   │  FastAPI 后端（端口 8000）    │
        │ + CSV      │   │  /api/chat /api/market       │
        └────────────┘   │  /api/memory /api/backtest   │
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │  React 前端（端口 3000）      │
                         │  对话·持仓·行情·记忆·回测     │
                         └─────────────────────────────┘
                                        ▲
                         ┌─────────────────────────────┐
                         │  OpenClaw 🦞 AI Agent        │
                         │  飞书 / Telegram 推送         │
                         └─────────────────────────────┘
```

### 📅 每日交互流程

```
18:00  ─ 调度器触发
       1. 克隆昨日持仓 → 今日 .json
       2. 飞书推送："今日持仓有变化吗？"

       ← 用户回复："卖了100股茅台，进攻现金变为-5万"
         或者："未变化"

       3. Agent 解析自然语言 → 更新持仓 JSON
       4. 获取实时行情（Yahoo Finance）
       5. 计算快照（盈亏 / 回撤 / 量化指标）
       6. 生成 Markdown 日报
       7. 推送飞书/Telegram 日报
       8. 同步 Web Dashboard

19:00  ─ 兜底：如用户未响应则自动运行
```

**启动调度器：**
```bash
nohup python engine/scripts/portfolio_scheduler.py &
# 默认：18:00 通知，19:00 兜底管道
```

### 🤖 完整 AI 交易助手

本项目提供的不只是持仓追踪器 — 而是一个**完整的多页面 AI 交易助手**。`docker compose up` 后即可获得配备 8 个功能页面的 React Web 应用，后端由 FastAPI + LLM 驱动。

```
┌──────────────────────────────────────────────────────────────────────┐
│  💬 对话  │ 💼 持仓 │ 📊 追踪 │ 📈 行情 │ 🧠 记忆 │ 📉 回测         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────┐  ┌─────────────────────────┐   │
│  │  💬 AI 交易助手                 │  │  💼 实时持仓              │   │
│  │                                 │  │  进攻   ¥62.4万  +2.3%   │   │
│  │  你：今天持仓分析一下           │  │  稳健   ¥76.8万  -0.8%   │   │
│  │  🤖：茅台今日 +2.3%，进攻组     │  ├─────────────────────────┤   │
│  │  主要贡献；平安压力较大，        │  │  💡 操作建议              │   │
│  │  建议关注杠杆是否过高...        │  │  · 平安杠杆偏高，考虑减仓 │   │
│  │                                 │  │  · 茅台突破前高，可持有   │   │
│  │  你：帮我算一下夏普比率         │  └─────────────────────────┘   │
│  │  🤖：当前夏普 1.23，波动率      │                                  │
│  │  54.2%，处于合理区间...         │                                  │
│  └─────────────────────────────────┘                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

#### 各页面详细功能

**💬 Dashboard（AI 对话）**
- 流式对话，支持 GPT-4o / Claude / DeepSeek 任意切换
- 上下文感知：每次对话自动携带最新持仓快照给 LLM
- 实时持仓侧边栏 — 仓位、盈亏、今日涨跌实时刷新
- AI 智能操作建议面板（可操作的具体建议，非泛泛分析）
- 对话历史记录与搜索

**💼 Portfolio（持仓管理）**
- 内联表单添加 / 编辑 / 删除持仓
- 多账户分组（进攻 / 稳健 或自定义任意分组）
- 负现金自动识别为融资，显示杠杆徽章
- 成本基础按组追踪，支持导出 JSON

**📊 Tracker（投资组合追踪）**
- 5 KPI 卡：总资产 · 总盈亏 · 今日盈亏 · 本月盈亏 · 最大回撤
- 分组双饼图展示持仓权重分布
- 资产净值曲线 vs 成本基线（交互式面积图）
- 月度盈亏可折叠柱状图
- 完整量化指标：夏普 · 波动率 · 胜率 · 盈亏比 · 平均盈亏

**📈 Market（实时行情）**
- 跨市场行情搜索：A 股 / 港股 / 美股
- 自选股监控列表，实时涨跌展示
- 价格预警设置（突破阈值自动通知）
- AKShare（沪深港）+ Yahoo Finance（美股）双数据源

**🧠 Memory（智能记忆）**
- 持久化用户画像：投资风格、风险偏好、策略备注
- 自动从对话中提炼关键决策（LLM 驱动摘要）
- 交易日记：带日期戳的交易备注
- 策略库：保存并回溯自己的交易规则与理由

**📉 Backtest（策略回测）**
- 内置策略：双均线交叉、RSI 均值回归、SMA 穿越
- 支持自定义策略插件
- 可配置：标的代码、时间范围、初始资金、手续费、滑点
- 结果指标：总收益率、年化收益、夏普比率、最大回撤、交易日志
- **AI 策略反思**：LLM 分析策略成败原因并给出改进建议
- 净值曲线图 + 基准对比

**⚙️ Settings（设置）**
- 运行时切换 LLM 提供商和模型（无需重启）
- 配置 API Key、代理地址、数据提供商
- 飞书 / Telegram Webhook 配置

| 页面 | 路由 | 后端接口 |
|------|------|----------|
| 对话 | `/` | `POST /api/chat` |
| 持仓 | `/portfolio` | `GET/POST /api/portfolio` |
| 追踪 | `/tracker` | `GET /api/portfolio/tracker` |
| 行情 | `/market` | `GET /api/market/quote` |
| 记忆 | `/memory` | `GET/POST /api/memory` |
| 回测 | `/backtest` | `POST /api/backtest/run` |
| 设置 | `/settings` | `GET/POST /api/settings` |

### 📝 配置说明

`engine/portfolio/config.json`：
```json
{
  "groups": {
    "进攻": { "cost_basis": 600000 },
    "稳健": { "cost_basis": 400000 }
  },
  "proxy": "http://127.0.0.1:7890",
  "feishu_chat_id": "your_feishu_chat_id",
  "ticker_map": {
    "贵州茅台": "SHA:600519",
    "中国平安": "SHA:601318",
    "腾讯控股": "HKG:0700",
    "苹果":     "NASDAQ:AAPL"
  }
}
```

**持仓格式**（`holdings/YYYY-MM-DD.json`）：
```json
{
  "date": "2026-03-06",
  "groups": {
    "进攻": {
      "cost_basis": 600000,
      "positions": [
        { "name": "贵州茅台", "ticker": "SHA:600519", "quantity": 100,  "cost_price": 1580.00 },
        { "name": "腾讯控股", "ticker": "HKG:0700",   "quantity": 500,  "cost_price": 398.60 }
      ],
      "fund": 50000,
      "cash": -85000
    },
    "稳健": {
      "cost_basis": 400000,
      "positions": [
        { "name": "中国平安", "ticker": "SHA:601318", "quantity": 2000, "cost_price": 42.50 }
      ],
      "fund": 80000,
      "cash": 130000
    }
  }
}
```

### Ticker 格式

| 市场 | 格式 | 示例 |
|------|------|------|
| A 股（沪） | `SHA:XXXXXX` | `SHA:600519` |
| A 股（深） | `SHE:XXXXXX` | `SHE:000858` |
| 港股 | `HKG:XXXX` | `HKG:0700` |
| 美股 | `NASDAQ:XX` / `NYSE:XX` | `NASDAQ:AAPL` |

### 🔄 自然语言更新

| 指令模式 | 示例 | 说明 |
|---------|------|------|
| 未变化 | `持仓未变化` | 保持原样，直接生成快照 |
| 现金变更 | `进攻现金变为-5万` | 更新指定组现金 |
| 基金变更 | `基金变为8万` | 更新基金金额 |
| 卖出 | `卖了100股茅台` | 减少持仓数量 |
| 买入 | `买了200股中国平安` | 增加持仓数量 |
| 直接设置 | `腾讯控股 800股` | 直接设定数量 |
| 清仓 | `清仓茅台` | 移除整个持仓 |
| 新增 | `新增 苹果 ticker:NASDAQ:AAPL 数量:50 成本:175.5` | 添加新股票 |
| 成本调整 | `进攻成本调整为620000` | 修改组成本基础 |

多条变更用逗号 `,` 或分号 `；` 分隔，数字支持 `万` 后缀。

### 📋 V1：Google Sheets 版本

V1 基于 Google Sheets + Apps Script，适合不想自托管的用户：
- `v1-google-sheets/src/code.gs` — Apps Script 源码
- `v1-google-sheets/template/*.xlsx` — Excel 模板
- `v1-google-sheets/docs/User_Guide_CN.md` — 中文使用指南

### 🆚 版本对比

| 维度 | V1（Google Sheets） | V2（自托管） |
|------|---------------------|-------------|
| 数据存储 | Google Drive | 本地 JSON/CSV |
| 行情来源 | Google Finance + 新浪 | Yahoo Finance + AKShare |
| 多市场 | 部分支持 | ✅ A 股 + 港股 + 美股 |
| 量化指标 | ❌ | ✅ 夏普 / 波动率 / 回撤 |
| AI 对话 | ❌ | ✅ GPT / Claude / DeepSeek |
| 策略回测 | ❌ | ✅ 自定义策略引擎 |
| Web 面板 | Google Sheets 图表 | ✅ React + TailwindCSS |
| 消息推送 | ❌ | ✅ 飞书 / Telegram / Slack |
| 数据隐私 | 数据在 Google | ✅ 完全私有 |
| Docker | ❌ | ✅ 一键部署 |

### 📄 许可证

MIT — 详见 [LICENSE](LICENSE)

---

<div align="center">
  <b>📊 用代码管理你的投资 · OpenClaw 🦞 驱动</b><br>
  <a href="https://github.com/Stepuuu/portfolio-daily-tracker">GitHub</a> ·
  MIT License
</div>
