---
name: portfolio-daily-tracker
description: Track and report multi-group stock portfolios with daily snapshots, live Yahoo Finance prices, P&L analytics, and push notifications (Feishu/Telegram). Supports A-shares, HK, US markets. Use when asked about holdings, buy/sell/rebalance positions, generate daily portfolio reports, check drawdown or returns, update fund/cash balances, or run the full snapshot-report-push pipeline.
version: 1.1.0
---

# Portfolio Daily Tracker Skill

## Trigger Conditions

Use this skill when the user mentions:
- Position changes (buy, sell, add, reduce, rebalance)
- Query current holdings, market value, P&L
- Portfolio reports, investment performance
- Specific stock names + quantity changes
- Drawdown, return rate, asset trends
- Fund / cash balance changes

## System Overview

The portfolio tracking system uses a daily snapshot model:
- **Holdings file**: `portfolio/holdings/YYYY-MM-DD.json` — independent holding records per day
- **Snapshot file**: `portfolio/snapshots/YYYY-MM-DD.json` — computed data with prices, market value, P&L
- **Config file**: `portfolio/config.json` — group definitions, API configuration
- **History CSV**: `portfolio/history.csv` — time series summary

Data directory: `$PROJECT_ROOT/engine/portfolio/` (override with `PORTFOLIO_DIR` env var)

## Portfolio Groups

The system supports multiple portfolio groups, each with independent cost basis and positions. For example:
- **Growth**: High-growth stocks + tech + funds + cash
- **Income**: Blue-chips + ETFs + bonds + cash

Note: The same stock can appear in different groups (held in different quantities). When updating, you must specify the group.

## Position Change Operations

When the user says "sold 500 shares of X", "added 200 shares of Y", etc., follow these steps:

### 1. Parse user intent
- Recognize stock name → map to ticker (see Ticker Format table below)
- Recognize action type: buy/add (+quantity), sell/reduce (-quantity)
- Recognize group: if unspecified and stock exists in multiple groups, ask "which group?"
- Recognize quantity and optional cost price

### 2. Execute change
```bash
cd $PROJECT_ROOT/engine
python3 scripts/portfolio_manager.py update <ticker> --qty <new_total> [--cost <price>] [--group <group_name>]
```

Common commands:
```bash
# View current holdings
python3 scripts/portfolio_manager.py show

# Update position quantity (set new total)
python3 scripts/portfolio_manager.py update SHA:603259 --qty 4000 --group Growth

# Add new position
python3 scripts/portfolio_manager.py add NASDAQ:NVDA NVIDIA Growth --qty 10 --cost 120.0 --market us

# Remove position
python3 scripts/portfolio_manager.py remove NASDAQ:META --group Growth

# Show group info
python3 scripts/portfolio_manager.py groups
```

## Fund / Cash Changes

When the user says "fund went up 500", "fund is now worth 160k", "cash changed to -500k":

### Fund update
```bash
# Set fund market value for a group (unit: yuan)
python3 scripts/portfolio_manager.py set-fund --group Growth --value 160000

# Set another group's fund value
python3 scripts/portfolio_manager.py set-fund --group Income --value 5000
```

### Cash update
```bash
# Set group cash (negative = margin/leverage)
python3 scripts/portfolio_manager.py set-cash --group Growth --value -500000

# Set another group's cash
python3 scripts/portfolio_manager.py set-cash --group Income --value 3300
```

### Parse user intent
- "fund is worth 160k" → set-fund --value 160000
- "fund went up 500" → read current fund value, add 500, then set-fund
- "redeemed 10k from fund" → read current fund value, subtract 10000, then set-fund
- "deposited 50k" → read current cash value, add 50000, then set-cash
- "cash changed to -480k" → set-cash --value -480000

### 3. Confirm changes
After changes, read and display today's holding file to confirm the update is correct.

## Query Operations

### View latest snapshot
```bash
# View latest snapshot
ls -t engine/portfolio/snapshots/*.json | head -1 | xargs cat | python3 -m json.tool
```

### Generate/view reports
```bash
# Generate today's snapshot (requires fetching prices)
python3 engine/scripts/portfolio_snapshot.py --date $(date +%Y-%m-%d)

# Generate report
python3 engine/scripts/portfolio_report.py --date $(date +%Y-%m-%d)
```

### View historical data
```bash
# View history CSV
cat engine/portfolio/history.csv

# View a specific date's snapshot
cat engine/portfolio/snapshots/2026-03-08.json | python3 -m json.tool
```

## Ticker Format Table

| Market | Format | Example | Yahoo Code |
|--------|--------|---------|------------|
| Shanghai A-shares | `SHA:XXXXXX` | `SHA:603259` | `603259.SS` |
| Shenzhen A-shares | `SHE:XXXXXX` | `SHE:002050` | `002050.SZ` |
| Shanghai ETF | `SHA:XXXXXX` | `SHA:513050` | `513050.SS` |
| Hong Kong | `HKG:XXXX` | `HKG:0700` | `0700.HK` |
| NASDAQ | `NASDAQ:XXXX` | `NASDAQ:GOOGL` | `GOOGL` |
| NYSE | `NYSE:XXXX` | `NYSE:BRK.B` | `BRK-B` |

> Users often use Chinese names or abbreviations — you need to map them to Ticker format.
> If unsure about a stock's ticker, use `portfolio_manager.py show` to check existing holdings.

## Trading Signal Phrase Recognition

Common expressions users may use:
- "sold / cleared / reduced" → decrease quantity
- "bought / added / initiated" → increase quantity
- "swapped / rebalanced X→Y" → sell X + buy Y
- "cost changed to" → update cost_price
- "redeemed fund / bought fund" → update fund value
- "deposited / transferred in" → update cash

## Full Daily Report Pipeline

When the user asks to generate a daily report, run the full pipeline:

```bash
cd $PROJECT_ROOT/engine

# 1. Fetch latest prices and generate snapshot
python3 scripts/portfolio_snapshot.py --date $(date +%Y-%m-%d)

# 2. Generate Markdown report
python3 scripts/portfolio_report.py --date $(date +%Y-%m-%d)

# 3. (Optional) Push to Feishu/Telegram
# In scripts/portfolio-daily.sh you can use openclaw message send to push
```

## Tools Provided

1. **get_tracker_snapshot** — Get portfolio snapshot including grouped holdings, margin/leverage, and quant metrics
2. **update_holdings** — Update daily holdings, accepts natural language description
3. **run_portfolio_pipeline** — Run the full pipeline: snapshot → report → push

## Example Interactions
```
User: Any changes to my portfolio today?
Agent: [calls get_tracker_snapshot to show current holdings]

User: Sold 500 shares of stock X, cash is now -480k
Agent: [calls update_holdings with changes, then run_portfolio_pipeline]

User: Generate today's daily report
Agent: [calls run_portfolio_pipeline]

User: Growth group fund is now worth 160k
Agent: [runs: portfolio_manager.py set-fund --group Growth --value 160000]
```

## Important Notes

1. **Daily files are immutable**: Modifying today's file does not affect historical files
2. **If no file exists for today**: Automatically copies from the most recent day
3. **Same stock across groups**: Must specify which group to update accurately
4. **Fund and cash**: Use `set-fund` and `set-cash` commands to update
5. **Weekends/holidays**: Can modify holdings, but price snapshots may not change
6. **Margin/leverage**: When cash is negative, margin amount and leverage ratio are auto-calculated
7. **Drawdown**: Calculated from full historical data in history.csv
