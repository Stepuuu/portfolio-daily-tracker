# Engine: Portfolio Snapshot Engine

> This is the **core data engine** of [Portfolio Daily Tracker](../README.md).  
> For the full system (Dashboard + OpenClaw integration), see the root README.

A self-hosted daily portfolio tracking system using local JSON files + Python. Supports **A-shares, Hong Kong, and US stocks** with automatic FX conversion, quantitative risk metrics, and AI Agent interactive daily workflow.

## Features

| Feature | Description |
|---------|-------------|
| Multi-market | A-shares (SHA/SHE), Hong Kong (HKG), US (NASDAQ/NYSE) with auto FX |
| Portfolio groups | Track separate strategies (e.g. offensive vs defensive) |
| Quant metrics | Sharpe ratio, annualized volatility, win rate, profit/loss ratio |
| Monthly returns | Accurate month-over-month returns using last trading day of previous month |
| Max drawdown | Calculated from full history (not just snapshot files) |
| Margin tracking | Negative cash = margin balance, auto-calculates leverage ratio |
| Local data | All data in version-controlled JSON + CSV files |
| Cron automation | Daily snapshot + report + optional push via shell script |
| Pluggable push | Feishu, Telegram, Slack, or any webhook |
| Dashboard ready | Optional FastAPI + React frontend for visualization |

## Architecture

```
portfolio/
├── config.json              # Groups, cost basis, proxy, FX config
├── history.csv              # Time-series summary (auto-generated)
├── holdings/
│   ├── 2025-01-01.json      # Daily holdings (immutable per-day)
│   └── ...
└── snapshots/
    ├── 2025-01-01.json      # Daily snapshot (prices + metrics)
    └── ...

scripts/
├── portfolio_snapshot.py    # Core: fetch prices → calculate → save snapshot
├── portfolio_manager.py     # CLI: update holdings (add/remove/modify positions)
├── portfolio_report.py      # Generate markdown report from snapshot
└── portfolio-daily.sh       # Cron wrapper: snapshot → report → push
```

## Quick Start

### 1. Install dependencies

```bash
pip install requests
```

### 2. Configure

```bash
cp portfolio/config.example.json portfolio/config.json
```

Edit `config.json`:

```json
{
  "groups": {
    "Growth": {"cost_basis": 500000, "label": "Growth strategy"},
    "Income": {"cost_basis": 800000, "label": "Income strategy"}
  },
  "data_dir": "./portfolio",
  "proxy": "",
  "yahoo_base": "https://query1.finance.yahoo.com/v8/finance/chart",
  "fx_tickers": {
    "HKD_CNY": "HKDCNY=X",
    "USD_CNY": "USDCNY=X"
  }
}
```

- Set group names and cost bases to match your accounts
- Set `proxy` if Yahoo Finance is blocked in your region (e.g. `http://127.0.0.1:7890`)

### 3. Create initial holdings

```bash
cp portfolio/holdings/example.json portfolio/holdings/$(date +%Y-%m-%d).json
```

Edit with your positions:

```json
{
  "Growth": {
    "positions": [
      {"name": "Apple", "ticker": "NASDAQ:AAPL", "quantity": 100, "cost_price": 180.0},
      {"name": "Google", "ticker": "NASDAQ:GOOGL", "quantity": 10, "cost_price": 170.0}
    ],
    "fund": 50000,
    "cash": 20000
  },
  "Income": {
    "positions": [
      {"name": "Vanguard Total Bond", "ticker": "NASDAQ:BND", "quantity": 200, "cost_price": 72.0}
    ],
    "fund": 0,
    "cash": 100000
  }
}
```

> **Margin/leverage**: Set `cash` to a negative value (e.g. `-50000`) to indicate margin borrowing. The system will automatically calculate margin amount and leverage ratio.

### 4. Generate snapshot

```bash
python3 scripts/portfolio_snapshot.py
```

Output: `snapshots/YYYY-MM-DD.json` with:
- Per-position market values, P&L, profit %
- Per-group totals with leverage info
- Portfolio summary with daily/monthly changes
- **Quantitative metrics**: Sharpe ratio, annualized volatility, win rate, profit/loss ratio

### 5. Automate with cron

```bash
# Run daily at 16:30 (after market close, adjust for your timezone)
30 16 * * 1-5 cd /path/to/project && bash scripts/portfolio-daily.sh
```

## Snapshot Output Example

```json
{
  "date": "2025-03-06",
  "groups": {
    "Growth": {
      "total_value": 588904.96,
      "positions_value": 917114.96,
      "cash": -484110,
      "profit": 3904.96,
      "return_pct": 0.67
    }
  },
  "summary": {
    "total_value": 1356859.32,
    "total_cost": 1385000.0,
    "total_profit": -28140.68,
    "daily_change": 22040.37,
    "daily_change_pct": 1.65,
    "max_drawdown_pct": -10.59,
    "month_return_pct": -6.52,
    "sharpe_ratio": 1.11,
    "volatility_annual": 57.33,
    "win_rate": 53.5,
    "profit_loss_ratio": 1.15,
    "trading_days": 114
  }
}
```

## CLI: Managing Holdings

```bash
# View current holdings
python3 scripts/portfolio_manager.py show

# Update position
python3 scripts/portfolio_manager.py update SHA:603259 --qty 5000 --group Growth

# Add new position
python3 scripts/portfolio_manager.py add NASDAQ:NVDA NVIDIA Growth --qty 10 --cost 120.0

# Remove position
python3 scripts/portfolio_manager.py remove NASDAQ:META --group Growth

# View groups
python3 scripts/portfolio_manager.py groups
```

## Ticker Format

| Market | Format | Example | Yahoo Symbol |
|--------|--------|---------|-------------|
| Shanghai A-share | `SHA:XXXXXX` | `SHA:603259` | `603259.SS` |
| Shenzhen A-share | `SHE:XXXXXX` | `SHE:002050` | `002050.SZ` |
| Hong Kong | `HKG:XXXX` | `HKG:0700` | `0700.HK` |
| US NASDAQ | `NASDAQ:XXXX` | `NASDAQ:GOOGL` | `GOOGL` |
| US NYSE | `NYSE:XXXX` | `NYSE:BRK.B` | `BRK-B` |
| Shanghai ETF | `SHA:XXXXXX` | `SHA:513050` | `513050.SS` |

## Data Flow

```
holdings/YYYY-MM-DD.json
        │
        ▼
portfolio_snapshot.py  ──→  snapshots/YYYY-MM-DD.json
        │                          │
        ▼                          ├──→ portfolio_report.py ──→ Markdown report
   history.csv                     └──→ [optional] webhook push
        │
        ▼
  Quant metrics (Sharpe, Vol, Win Rate, P/L Ratio)
```

## Optional Integrations

### Web Dashboard

Add a FastAPI + React frontend to visualize your portfolio:

```
backend/api/portfolio_tracker.py  ──→  REST API for dates, snapshots, history
frontend/src/pages/PortfolioTracker.tsx ──→ Dashboard UI
```

Features: KPI cards, per-group pie charts, cost+value trend chart, monthly collapsible daily returns, position tables with daily P&L.

### Messaging Push

Customize `portfolio-daily.sh` to push reports to:
- **Feishu** — POST to webhook URL
- **Telegram** — Bot API
- **Slack/Discord** — Incoming webhook
- **Email** — sendmail/SMTP

### AI Agent Integration

Add a `get_tracker_snapshot` tool to your AI agent so it can answer questions about your portfolio positions, leverage, and performance metrics.

## Requirements

- Python 3.8+
- `requests` (`pip install requests`)
- Network access to Yahoo Finance API

## vs. V1 (Google Sheets)

| Feature | V1 (Google Sheets) | V2 (Self-hosted) |
|---------|-------------------|-------------------|
| Price Source | Sina + Google Finance | Yahoo Finance (all markets) |
| Storage | Google Drive | Local JSON + CSV |
| Quant Metrics | None | Sharpe, Vol, Win Rate, P/L Ratio |
| Margin/Leverage | None | Auto-calculated from negative cash |
| Monthly Returns | Simple calc | Last trading day of prev month |
| Drawdown | Per-session | Full history (CSV-based) |
| Groups | Single sheet | Multi-group with separate returns |
| Automation | Google Apps Script | Cron + Python |
| Dashboard | Built-in (basic) | Optional React UI (rich) |
