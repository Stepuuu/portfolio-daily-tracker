---
name: portfolio-daily-tracker
description: Track and report multi-group stock portfolios with daily snapshots, live Yahoo Finance prices, P&L analytics, and push notifications (Feishu/Telegram). Supports A-shares, HK, US markets. Use when asked about holdings, buy/sell/rebalance positions, generate daily portfolio reports, check drawdown or returns, update fund/cash balances, or run the full snapshot-report-push pipeline.
version: 1.3.0
setup: scripts/setup.sh
env:
  OPENAI_API_KEY:
    description: OpenAI API key for AI chat features
    required: false
  FEISHU_WEBHOOK:
    description: Feishu/Lark webhook URL for push notifications
    required: false
  TELEGRAM_BOT_TOKEN:
    description: Telegram bot token for push notifications
    required: false
  PORTFOLIO_DIR:
    description: Override default portfolio data directory path
    required: false
requires:
  - python3 >= 3.10
  - pip packages: yfinance, pandas, requests, fastapi, uvicorn
  - Engine scripts installed via setup.sh (clones repo with portfolio_manager.py, portfolio_snapshot.py, portfolio_report.py)
---

# Portfolio Daily Tracker Skill

Use Python 3.10+ and the engine scripts installed by `scripts/setup.sh`.

## Read and propose workflow

1. Read the latest dated snapshot with `get_tracker_snapshot` for valuations.
2. Read `/api/ledger` for confirmed native cash and quantities if the ledger is active.
3. For an explicit trade, request any missing account, date, currency, quantity,
   price and fee. Do not infer a price from market quotes or a screenshot.
4. Call `propose_ledger_events` using the transaction schema in `docs/LEDGER.md`.
5. Tell the user nothing has been booked and direct them to `/ledger` to review and
   confirm. Never call the confirmation API or legacy holdings-writing scripts on
   their behalf as part of proposal generation.
6. After confirmation, snapshots can be regenerated independently. Report delivery
   requires an explicit user-configured destination and authorization.

`get_all_tools()` now exposes read and proposal tools. The previous direct update
and pipeline tools are no longer registered. Existing external agents should update
their instructions before enabling the ledger. Existing schedulers using legacy
holdings mutation must be disabled before migration.

## Installation

```bash
bash scripts/setup.sh [target_dir]
```

For a first look at the complete app, run `make setup` and `make demo` in the cloned
repository. The demo is read-only and uses fictional data. For a writable ledger,
use `make start`, then explicitly confirm opening balances on `/ledger`.
