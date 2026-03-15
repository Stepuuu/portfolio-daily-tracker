<div align="center">

# 📊 Portfolio Daily Tracker

**A comprehensive self-hosted investment portfolio tracking & AI trading assistant**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org)
[![ClawHub Skill](https://img.shields.io/badge/ClawHub-portfolio--daily--tracker-orange)](https://clawhub.ai)

[🇨🇳 中文文档](README_CN.md)

</div>

---

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
| 🦞 **OpenClaw Skill** | Published on [ClawHub](https://clawhub.ai) — install with `clawhub install portfolio-daily-tracker` |

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
│   ├── main.py                        # App entry point (CLI mode)
│   ├── config.example.json            # Config template (copy → config.json)
│   ├── config/                        # Config module
│   ├── core/                          # LLM, memory, models, tools
│   ├── providers/                     # LLM & market data providers
│   ├── agents/                        # AI trader agent
│   ├── backtesting/                   # Backtesting engine
│   ├── ui/                            # CLI interface (terminal mode)
│   ├── tools/                         # Browser MCP screenshot tool
│   ├── docs/                          # Agent tools documentation
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
│   ├── skills/SKILL.md                # Comprehensive skill guide
│   └── examples/config.example.json
│
├── v1-google-sheets/            # 📋 V1: Google Sheets version (archived)
├── docker-compose.yml
├── Makefile                     # make setup / make start / make stop
├── start.sh                     # Dev start script
├── stop.sh                      # Stop all services
└── README.md
```

### 🚀 Quick Start

#### One-command setup (Recommended)

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker

make setup    # installs all deps, copies config templates
# Edit dashboard/config.json — add your LLM API key
# Edit engine/portfolio/config.json — set group names & cost basis

make start    # starts backend (:8000) + frontend (:3000)
# Open http://localhost:3000
```

#### Option 1: Engine Only (Portfolio Tracker)

Minimal setup — just Python + `requests`:

```bash
cd engine
pip install requests

cp portfolio/config.example.json portfolio/config.json
# Edit config.json: set proxy, group names, cost basis

cp portfolio/holdings/example.json portfolio/holdings/$(date +%Y-%m-%d).json
# Edit with your actual positions

python3 scripts/portfolio_snapshot.py    # Generate snapshot
python3 scripts/portfolio_report.py      # Generate report
```

#### Option 2: Full Trading Assistant

```bash
cd dashboard
pip install -r requirements.txt

cp config.example.json config.json
# Edit config.json: add your AI API key (OpenAI / Claude / DeepSeek)

# Start backend
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# Start frontend
cd frontend && npm install && npm run dev
# Open http://localhost:3000
```

Or use the start script from the project root:
```bash
./start.sh          # Start both backend + frontend
./start.sh backend  # Backend only
./start.sh engine   # Run snapshot engine once
./stop.sh           # Stop all services
```

#### Option 3: Docker Compose

```bash
docker compose up -d
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
```

#### Option 4: CLI Mode

```bash
cd dashboard && python3 main.py
# Interactive terminal with /portfolio, /add, /import, /refresh, /models
```

### 🦞 OpenClaw Skill (ClawHub)

This project is published as an [OpenClaw](https://openclaw.ai) agent skill on **ClawHub**.

**Install:**
```bash
clawhub install portfolio-daily-tracker
```

The skill enables AI agents to manage your portfolio via natural language — buy/sell positions, generate daily reports, update fund/cash balances, and run the full snapshot→report→push pipeline. See [openclaw/skills/SKILL.md](openclaw/skills/SKILL.md) for full documentation.

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
│  │                                 │  │  Growth  ¥62.4万  +2.3%  │   │
│  │  You: Analyze my portfolio      │  │  Income  ¥76.8万  -0.8%  │   │
│  │  🤖: Moutai up +2.3%, driving   │  ├─────────────────────────┤   │
│  │  Growth group. Ping An under    │  │  💡 Suggestions          │   │
│  │  pressure — watch leverage...   │  │  · Reduce Ping An lever. │   │
│  │                                 │  │  · Hold Moutai at highs  │   │
│  │  You: Calculate Sharpe ratio    │  └─────────────────────────┘   │
│  │  🤖: Current Sharpe 1.23,       │                                  │
│  │  volatility 54.2%, reasonable…  │                                  │
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
| Buy | `Bought 200 shares Ping An` | Increase existing position |
| Buy new stock | `Bought 500 shares Wanrun, code is SHA:688275` | Auto-add a new position |
| Set qty | `Tencent 800 shares` | Set quantity directly |
| Clear | `Cleared Moutai position` / `Wanrun is fully sold` | Remove position |
| New position | `New: Apple ticker:NASDAQ:AAPL qty:50 cost:175.5` | Add new position |
| Cost adjust | `Aggressive cost basis 620000` | Adjust group cost basis |

Multiple changes can be comma/semicolon separated. Supports the Chinese `万` (10k) suffix, and for cash/fund fields it also accepts shorthand decimals like `-44.273` or `15.635` as `-44.273万` / `15.635万`. If a newly added position omits `cost_price`, the parser will try to infer it from same-message cash/fund deltas.

### 📋 V1: Google Sheets Version

The original V1 uses Google Sheets + Apps Script. Suitable for users who don't want to self-host:
- `v1-google-sheets/code.gs` — Apps Script source
- `v1-google-sheets/*.xlsx` — Spreadsheet templates
- `v1-google-sheets/User_Guide_EN.md` — English guide

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

<div align="center">
  <b>📊 Manage your investments with code · Powered by OpenClaw 🦞</b><br>
  <a href="https://github.com/Stepuuu/portfolio-daily-tracker">GitHub</a> ·
  <a href="https://clawhub.ai">ClawHub Skill</a> ·
  MIT License
</div>
