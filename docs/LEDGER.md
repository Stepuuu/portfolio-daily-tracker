# Transaction ledger / 交易账本

The ledger is the only place holdings and cash come from. It starts when you
confirm an opening balance; nothing is migrated on startup. The local launcher
requires Python 3.10+, Node.js 22.12+ and Bash 4.3+. **Docker is not required**,
including when your machine is already a container instance.

## First use

1. Run `make setup`, then `make start`, and open `http://localhost:3000/setup`
   (on a fresh install the Today page sends you there once).
2. In 账户与期初 (account and opening balance) choose how to start: 从零开始 (enter
   native CNY/HKD/USD cash, 累计净投入 in CNY, fund value and each position's
   quantity and cost), 导入券商文件 (a holdings statement or 交割单, see
   [IMPORT.md](IMPORT.md)) or 迁移旧版持仓 (the v3.2 `holdings/<date>.json` or
   `data/portfolio.json`; values the file does not establish are asked for).
3. Review the before/after panel. Only **确认入账** (confirm) changes account balances.
4. Record later changes with **记一笔** (press `N`): a buy, sell, dividend, cash
   movement, transfer, currency exchange, split or reversal. Review cash, share
   count, cost and the trade-plan check before confirming. An unconfirmed manual
   preview expires after 2 hours.
5. Keep an investment thesis, invalidation conditions and next review date in
   计划与复盘 › 笔记 (`/notes`). New entries preserve previous conclusions. Notes due
   for review appear in the pre-market brief.

`make demo` offers a separate, read-only fictional portfolio with a confirmed
ledger. It deliberately rejects ledger writes. Stop the demo and use `make start`
to create your own ledger.

中文：先核对期初余额，再确认入账。账户里的现金按原币分别记录，累计净投入和基金估值以
人民币记录。AI 仅生成待确认方案；图片识别只返回识别结果，不会入账。任何识别不清的币种、价格或
数量都应先核对，不能直接当作真实成交。确认页面显示的是账务变化，不是最新市值。

## From AI chat to a confirmed entry

After opening balances are confirmed, describe a transaction with an explicit
account, date, ticker, currency, quantity, price and fee. For example:
“Example account: on 2026-09-01, buy 2 NASDAQ:EXAMPLE shares at USD 100,
with a USD 2 fee.” These are fictional values; use your executed trade details.

1. The AI calls `propose_ledger_events`. This saves a preview, not a transaction.
2. The chat and the 待确认 (pending) tray in the header display pending ledger
   proposals from the server, independently of the model's reply. **核对方案**
   opens the exact proposal on `/ledger?proposal=…`.
3. Review native cash, quantities and costs before selecting **确认入账**.
4. The saved result retains the before/after comparison and receipt. Reloading
   its link shows the same result; another confirmation cannot book it twice.
   Expired proposals require a fresh preview, and discarded links show an error.

中文：先核对期初余额，再向 AI 描述已发生的交易。聊天中的待确认提示来自账本，
不依赖 AI 是否在回复里附上链接。点击“核对方案”查看指定方案，确认后才能入账；
结果保留现金、股数和成本的前后对比，刷新后仍可查看。

The model must support tool calls and successfully create a proposal. A text reply
alone is not evidence of a write. If there is no pending proposal, no corresponding
transaction has been prepared. Missing or ambiguous amounts should be clarified.

Confirmed events are stored in `ledger.sqlite3`; holdings are derived from those
events. The Today page values them at live quotes; the daily close snapshot (made
by the backend at 15:40 on A-share trading days, see
[ALERTS_AND_ROUTINES.md](ALERTS_AND_ROUTINES.md)) is a separate saved valuation and
is not silently relabeled as current after a trade. Foreign cash remains in its
original currency; only its reported CNY value changes with FX. See
[accounting conventions](ACCOUNTING.md) for quote/FX behavior.

## Accounting conventions

| Event | Required values in addition to `kind`, `date`, `account` |
|---|---|
| `opening` | `cash_balances`, `contributed_cny`; optional `positions`, `fund_cny`. An API client that leaves out `contributed_cny` books 0; 记一笔, the setup wizard, the legacy preview and the assistant require it |
| `buy`, `sell` | `ticker`, `currency`, `quantity`, `price`; `fee` defaults to zero |
| `deposit`, `withdrawal` | `currency`, `amount`; foreign currency requires `fx_rate` |
| `dividend`, `fee` | `currency`, `amount` |
| `interest` | `currency`, `amount`: interest credited to cash (利息归本, net 逆回购 income) |
| `transfer` | `to_account`, `currency`, `amount`; foreign currency requires `fx_rate` |
| `fx` | `currency`, `amount`, `to_currency`, `received_amount` |
| `fund_value` | `amount`: reconciled aggregate fund value in CNY |
| `fund_buy`, `fund_sell` | `amount`: fund subscription/redemption value in CNY; optional `fee` |
| `split` | `ticker`, `ratio` (new quantity / old quantity) |
| `reverse` | `target_id`, `date`; account is taken from the original event |

Dates use `YYYY-MM-DD`. Tickers include the exchange, for example `NASDAQ:AAPL`,
`HKG:00700` or `SHA:600519`. Money and quantities are stored as decimal strings.
A new event of a listing its account already holds under another spelling (Hong
Kong zero padding, `HKG:700` / `HKG:00700`, or a US class share with a dot or a
dash) is booked under the spelling the account held on the event's date: of two
held spellings, the one typed, else the larger position; a listing the account
did not hold on that date keeps the spelling typed, except an entry missed earlier (dated before the account's
other events of that listing), which takes their spelling when they all use one. Replay never merges spellings: events keep the spelling they
were booked with, so a ledger that holds one listing under two spellings from
before this rule replays as it always did, as two positions. A fill sent again is
still recognised by its external id when only the spelling differs.

- Buy fees enter weighted average cost. Sell fees reduce proceeds and realized
  trading P&L. This is **average-cost accounting**, not FIFO or tax reporting.
- Sells cannot exceed held quantity. Buys require sufficient native cash unless
  financing is explicitly selected. Other negative cash balances appear as warnings
  in the preview; review borrowing and repayment deliberately.
- `fx_rate` means CNY per one unit of foreign currency. Deposits and withdrawals
  change contributions; dividends, interest and fees do not. `interest` is a cash
  credit, for example 利息归本 or the net income of a 逆回购 (reverse repo) pair. Transfers move contributions
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
- The state for a past date replays the events dated on or before it, so a
  reversal dated later is normally not applied yet. When that would be an
  impossible history for an account (for example a buy re-entered on its real,
  earlier date cannot be paid for alongside the mistaken buy it replaces), that
  account's later reversals are applied as well and it shows the corrected
  ledger for that date. Every other account keeps its dated view, and the state
  lists the corrected accounts in `as_of_corrected_accounts` (absent when there
  are none). Accounts share no cash; a transfer counts as a withdrawal from one
  account and a deposit to the other. Only a transfer that a later reversal
  removes ties its two accounts together, so that both or neither show it.
- A back-dated entry is checked differently: booking replays every event that
  still counts, so a reversal counts whatever its date. The 记一笔 form checks
  holdings and cash with `GET /api/ledger/as-of`, which gives that same view
  (`replay_for_entry` in `dashboard/core/ledger.py`): after a sale is reversed,
  the corrected sale booked on the original date has the shares again.
- Historical snapshots are retained. Backdated corrections do not automatically
  regenerate valuations or reports. Snapshot generation currently fetches latest
  quotes; it is not a historical-price backfill service. Do not interpret rerunning
  an old date with latest quotes as a historical valuation.

Daily performance still uses an end-of-period cash-flow approximation. Strict
TWR, XIRR and market/FX P&L attribution remain planned. See [ACCOUNTING.md](ACCOUNTING.md).

## Broker file import

`POST /api/imports/parse` reads a broker export (交割单, 资金对账单 or 持仓 from
同花顺, 东方财富, 通达信 or a broker app: CSV, tab or space separated TXT, XLS, XLSX,
or a web page saved as .xls; UTF-8 or GBK; at most 5 MB and 20,000 rows) and returns
rows with a status. Nothing is booked until you review the preview and confirm it.
The request and response shapes are documented in `dashboard/backend/api/imports.py`.

- Columns are recognised by name (成交日期, 证券代码, 操作 / 业务名称, 成交数量,
  成交价格, 成交金额, 发生金额, fee columns …). Correct a column once and the mapping
  is reused for the same account and header layout. The vendor presets are
  unverified: they were written from documented column names, not from real files.
- Fees are the sum of the fee columns, checked against |发生金额| − 成交金额 when both
  exist, or derived from them. A fee that cannot be read or derived is never set
  to 0: the row needs review.
- 逆回购 pairs become one `interest` event with the net amount. 港股通 trades
  settled in CNY become an `fx` (CNY→HKD) plus the HKD trade. 送股 / 转增 become a
  `split` with the exact ratio from the shares held at that date.
- 新股, 配股, ETF / fund subscriptions, margin trades and unknown business types
  need review; 撤单, 申购配号 and similar rows are skipped and listed; 北交所 and B
  shares are unsupported.
- Imported events use `source = import:{account}` and a stable `external_id`, so
  importing the same file again adds nothing. A row whose id is already booked with
  different values is excluded and explained instead of failing the import. A row
  that matches an entry booked under another id (same account, date, security, kind
  and quantity, or the same cash amount; a 送股 that added the same shares) — entered
  by hand, or imported from an overlapping export without 成交编号 — is marked 可能重复
  and left unchecked.
- A row is bookable only when the file itself establishes the security, the
  direction, the quantity and its unit, the price, the currency of every amount, the
  fee, the account it belongs to, and that it is not booked yet. Anything missing,
  contradictory or ambiguous makes the row 需处理 with the reason; nothing defaults
  to CNY, to the selected account, to a buy or to the first of two columns. Account
  identities (资金账号 / 股东代码) imported into a ledger account, and the ones you
  chose or excluded for it, are remembered: a later file with another or an excluded
  identity needs your choice even when it holds only one.
- An imported trade moves exactly the cash the file states. When the file has no
  成交价格, or one the broker rounded (9,605.00 for 300 shares at 32.017), the price is
  成交金额 ÷ 数量 with as many decimal places as make 数量 × 价格 exactly the 成交金额
  (32.016666666666666666666666667), and the fee is what the 发生金额 leaves; balances
  after an import never carry a sub-cent tail.
- If the file has 资金余额 / 股份余额 columns, the result is compared with them
  (一致, or the difference and the rows that explain it).
- A holdings statement opens a new account (you enter the date, cash and 累计净投入,
  which the file does not contain) or, for an existing account, shows a
  reconciliation table. It never proposes corrective trades.
- Proposals hold at most 1,000 events; larger files are confirmed in batches, the
  next batch is prepared after the previous one is confirmed.
- An import proposal is bound to the import it came from: confirming it is refused
  (409, and the proposal withdrawn) once that import was edited, deleted or expired,
  once the ledger changed, or after an upgrade that changed the import rules (the
  binding includes the version of the rules), so only the preview you see can be
  booked. A session has at most one pending proposal. An import proposal made before
  bindings existed is never confirmed: it is refused and withdrawn, also after its
  import expired. Confirming a withdrawn proposal answers 409.

## CSV template

The project's own CSV template is imported on 账本 › 导入 like a broker file
(statement 本系统模板); download it there with 下载模板 (`GET /api/ledger/template.csv`).
It uses these column names:

```csv
external_id,date,kind,account,ticker,currency,quantity,price,fee,amount,fx_rate,to_account,to_currency,received_amount,ratio,note
example-001,2026-08-03,buy,Example,NASDAQ:EXAMPLE,USD,0.5,100,1,,,,,,,Fictional example
```

Each row needs an `external_id` that is unique in the file, a `currency` (except
a `split`), and a `fee` on a buy or sell. Empty optional columns may be omitted.
Opening balances and reversals use their own preview flows. Rows are previewed,
deduplicated and confirmed as described in [IMPORT.md](IMPORT.md#the-template-本系统模板);
reimporting the same rows adds nothing, and a row whose id is booked with other
values is excluded and explained instead of failing the import.

## Confirmation and recovery

A proposal stores its source revision and before/after state. If the ledger changes
before confirmation, you must create a fresh preview. Retrying the same confirmation
returns its original receipt. SQLite commits events, revision and receipt together.
A failed quote refresh or report is separate from booking; it never reapplies a trade.

Use **备份** (on 流水, or 设置 › 备份与导出) to download a consistent SQLite backup
containing events, pending proposals, receipts and research notes. **导出** exports
transaction events as JSON for inspection, not a full restorable backup. Alerts,
plans, the watchlist, briefs, import sessions and fee profiles live in
`desk.sqlite3`, which 设置 › 备份与导出 downloads separately. The two downloads are
two copies, each of its own moment: book nothing in between and download both one
after the other.

To restore:

1. Close every open page of the app, then stop the backend and any scheduled
   writers. (Restored under an open page, new notifications can reuse numbers
   from before the restore and opening an old one marks new alerts as read; a
   proposal confirmed after the backup shows 已应用 when confirmed again but does
   not take effect, so make that change in settings directly.)
2. Save the existing `ledger.sqlite3` (and `desk.sqlite3` when you restore it too)
   and any SQLite sidecars (`-wal`, `-shm`) elsewhere.
3. Verify the backup with SQLite `PRAGMA integrity_check` (must return `ok`).
4. Replace the file in your configured portfolio directory with the backup.
5. Restart, open the app again and compare account balances, event count and
   research notes. Snapshot JSON and CSV history are separate files; back up the
   portfolio directory too.

The ledger lives in `PORTFOLIO_DIR/ledger.sqlite3`, defaulting to `engine/portfolio`.
Once it exists, the v3.2 manual portfolio is read-only: `/portfolio` redirects to
`/ledger`, `POST /api/settings/cash` and the `/api/portfolio` holdings endpoints
answer 409 (they are removed in the next release), and the engine's
holdings-writing scripts refuse changes. The Feishu card records into the ledger
instead (see [FEISHU_WORKBENCH.md](FEISHU_WORKBENCH.md#with-the-transaction-ledger--启用交易账本后)).
Snapshot generation reads the confirmed ledger; `portfolio_daily_update.py
--action auto-pipeline` works with an active ledger, and `--action notify` sends
the evening card without cloning a holdings file.

## API and AI integrations

- `POST /api/ledger/proposals` with `{"events": [...]}` prepares a preview.
- `GET /api/ledger/proposals/{id}` reads the exact preview and its receipt, if confirmed.
- `POST /api/ledger/proposals/{id}/confirm` confirms the exact preview.
- `GET /api/ledger`, `/events`, `/journal` read balances, history and research.
- `GET /api/ledger/backup.sqlite3` downloads the complete ledger backup.

Dashboard and OpenClaw tool registries expose a proposal tool and read tools. They
provide no confirmation or notification tool. External agents with shell/API access
are outside that boundary: configure them to prepare proposals and leave confirmation
to a person. A person confirms in the web UI, or on the Feishu workbench card
([FEISHU_WORKBENCH.md](FEISHU_WORKBENCH.md#with-the-transaction-ledger--启用交易账本后)):
the card shows the ledger's preview of its own proposal, and its 确认入账并更新日报 button
is the person's confirmation of that exact proposal. Only users on the workbench's
allowlist (`allowed_users`) can press it, on a card sent to them in the bot's private
chat; a forwarded card or another chat is refused. Card proposals (source
`feishu-card`) also appear in the web's 待确认 for 2 hours, marked 在飞书卡片上确认:
they can be discarded there but are confirmed only on the card, whose confirmation also
completes its list and makes the day's report. The backend is
a local single-user service without authentication; see [SECURITY.md](../SECURITY.md)
before remote access.
