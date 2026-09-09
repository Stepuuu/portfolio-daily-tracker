# Transaction ledger / 交易账本

The ledger is optional until you explicitly confirm an opening balance on `/ledger`.
No account data is migrated on startup. The local launcher requires Python 3.10+,
Node.js 22.12+ and Bash 4.3+. **Docker is not required**, including when your machine
is already a container instance.

## First use

1. Run `make setup`, then `make start`. Open `http://localhost:3000/ledger`.
2. Create an account with native CNY/HKD/USD cash and cumulative contributions in
   CNY. Add opening securities and their native unit costs. For existing tracker
   users, choose an opening date and select **预览已有持仓** (preview existing holdings).
   This reads the latest dated holdings file at or before that date. Check the
   source date, currencies, contributions, fund value, quantities and costs.
3. Review the before/after panel. Only **确认入账** (confirm) changes account balances.
4. Record a buy, sell, cash movement, transfer, currency exchange or split. Review
   cash, share count, cost and realized trading P&L before confirming.
5. Keep an investment thesis, invalidation conditions and next review date in
   **研究复盘**. New entries preserve previous conclusions. Review dates are displayed
   in the journal; they do not schedule notifications.

`make demo` offers a separate, read-only fictional tracker. It deliberately rejects
ledger writes. Stop the demo and use `make start` to create your own ledger.

中文：先核对期初余额，再确认启用。账户里的现金按原币分别记录，累计净投入和基金估值以
人民币记录。AI 仅生成待确认方案；图片识别只返回识别结果。任何识别不清的币种、价格或
数量都应先核对，不能直接当作真实成交。确认页面显示的是账务变化，不是最新市值。

## Accounting conventions

| Event | Required values in addition to `kind`, `date`, `account` |
|---|---|
| `opening` | `cash_balances`, `contributed_cny`; optional `positions`, `fund_cny` |
| `buy`, `sell` | `ticker`, `currency`, `quantity`, `price`; `fee` defaults to zero |
| `deposit`, `withdrawal` | `currency`, `amount`; foreign currency requires `fx_rate` |
| `dividend`, `fee` | `currency`, `amount` |
| `transfer` | `to_account`, `currency`, `amount`; foreign currency requires `fx_rate` |
| `fx` | `currency`, `amount`, `to_currency`, `received_amount` |
| `fund_value` | `amount`: reconciled aggregate fund value in CNY |
| `fund_buy`, `fund_sell` | `amount`: fund subscription/redemption value in CNY; optional `fee` |
| `split` | `ticker`, `ratio` (new quantity / old quantity) |
| `reverse` | `target_id`, `date`; account is taken from the original event |

Dates use `YYYY-MM-DD`. Tickers include the exchange, for example `NASDAQ:AAPL`,
`HKG:00700` or `SHA:600519`. Money and quantities are stored as decimal strings.

- Buy fees enter weighted average cost. Sell fees reduce proceeds and realized
  trading P&L. This is **average-cost accounting**, not FIFO or tax reporting.
- Sells cannot exceed held quantity. Buys require sufficient native cash unless
  financing is explicitly selected. Other negative cash balances appear as warnings
  in the preview; review borrowing and repayment deliberately.
- `fx_rate` means CNY per one unit of foreign currency. Deposits and withdrawals
  change contributions; dividends and fees do not. Transfers move contributions
  between accounts while preserving the portfolio-wide total.
- FX records actual amounts paid and received. Include conversion charges in the
  received amount; do not separately count the same charge twice.
- Funds use an aggregate CNY valuation bucket. Subscriptions move CNY cash into
  fund value; redemptions move value back to cash, with fees recorded separately.
  `fund_value` records a reconciled current total without changing cash or contributions.
  This does not track individual fund shares, automatically fetch NAVs, or calculate
  fund tax lots. Reconcile the value before redeeming an appreciated holding.
  Options, short positions and tax-lot accounting remain unsupported.
- Events replay by date, then confirmation order within a day. Reversals preserve
  the original event and rebuild subsequent balances. If reversal would invalidate
  a later sale or split, preview fails; correct dependent events first.
- Historical snapshots are retained. Backdated corrections do not automatically
  regenerate valuations or reports. Snapshot generation currently fetches latest
  quotes; it is not a historical-price backfill service. Do not interpret rerunning
  an old date with latest quotes as a historical valuation.

Daily performance still uses an end-of-period cash-flow approximation. Strict
TWR, XIRR and market/FX P&L attribution remain planned. See [ACCOUNTING.md](ACCOUNTING.md).

## CSV import

Download the template in `/ledger`. It uses these column names:

```csv
external_id,date,kind,account,ticker,currency,quantity,price,fee,amount,fx_rate,to_account,to_currency,received_amount,ratio,note
example-001,2026-08-03,buy,Example,NASDAQ:EXAMPLE,USD,0.5,100,1,,,,,,,Fictional example
```

Use UTF-8, at most 2 MB and 1,000 rows per preview. Empty optional columns may be
omitted. Each row needs a stable `external_id`. Use a distinct, stable source name
for each broker account. Reimporting the same source and ID with identical fields
is skipped; changed content is rejected. This is a standard template, not automatic
support for arbitrary broker CSV formats. Opening balances and reversals use their
own preview flows.

## Confirmation and recovery

A proposal stores its source revision and before/after state. If the ledger changes
before confirmation, you must create a fresh preview. Retrying the same confirmation
returns its original receipt. SQLite commits events, revision and receipt together.
A failed quote refresh or report is separate from booking; it never reapplies a trade.

Use **备份** to download a consistent SQLite backup containing events, pending
proposals, receipts and research notes. **导出 JSON** exports transaction events for
inspection, not a full restorable backup.

To restore:

1. Stop the backend and any scheduled writers.
2. Save the existing `ledger.sqlite3` and any SQLite sidecars elsewhere.
3. Verify the backup with SQLite `PRAGMA integrity_check` (must return `ok`).
4. Replace `ledger.sqlite3` in your configured portfolio directory with the backup.
5. Restart and compare account balances, event count and research notes. Snapshot
   JSON and CSV history are separate files; back up the portfolio directory too.

The ledger lives in `PORTFOLIO_DIR/ledger.sqlite3`, defaulting to `engine/portfolio`.
After activation, the legacy position-editing page redirects to the ledger and old
holdings-writing scripts reject changes. Direct snapshot generation reads the
confirmed ledger. The legacy automatic update scheduler should be disabled before
activation; generate snapshots separately until it supports ledger proposals.

## API and AI integrations

- `POST /api/ledger/proposals` with `{"events": [...]}` prepares a preview.
- `POST /api/ledger/proposals/{id}/confirm` confirms the exact preview.
- `GET /api/ledger`, `/events`, `/journal` read balances, history and research.
- `GET /api/ledger/backup.sqlite3` downloads the complete ledger backup.

Dashboard and OpenClaw tool registries expose a proposal tool and read tools. They
provide no confirmation or notification tool. External agents with shell/API access
are outside that boundary: configure them to prepare proposals and leave confirmation
to the person using the UI. The backend is a local single-user service without
authentication; see [SECURITY.md](../SECURITY.md) before remote access.
