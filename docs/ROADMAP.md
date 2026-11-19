# Roadmap

The direction is a private workspace connecting **records → evidence → decisions →
review**, where the confirmed ledger is the one record of the portfolio and an AI
response is never the only record of a change. Status reflects version 4.0.0
(2026-11-19); the [changelog](../CHANGELOG.md) lists release details.

## Done

**Since 4.0.0 (unreleased)**

- **Feishu to ledger.** With an active ledger the Feishu card's daily list becomes
  ledger events: its preview is the ledger's proposal and confirming on the card
  books it. Trades in every market (港股通 as the importer books it), transfers,
  fund values, principal targets and "cash is now X" as one explicit adjustment;
  quantity, cost and principal-only overwrites are refused. Booked once across taps,
  redeliveries, restarts and cards; the day's report is made again after an evening
  entry, sharing the close snapshot's lock, checkpoint and push rule (pushed only
  when `feishu_chat_id` is configured, otherwise saved).

**In 4.0.0**

- **One portfolio store.** Holdings come only from the confirmed ledger. The setup
  wizard (`/setup`) creates the first account and opening balance from scratch,
  from a broker file or from the v3.2 files; the manual portfolio is read-only once
  a ledger exists.
- **Less typing.** 记一笔 with code and name search, the price from the current
  quote, fee estimates from per-account fee profiles and a review step; a broker
  file importer for 交割单, 资金对账单 and 持仓 files with column mapping, row
  statuses, account identities, deduplication, reconciliation and batches.
- **Trustworthy valuation.** Quotes keyed by exchange-qualified tickers with their
  source, exchange time and status; estimated and unavailable values marked, never
  0; exchange holiday tables for the mainland (2026), Hong Kong and New York (2026,
  2027); day profit that follows the shares traded.
- **Daily routines.** Price alerts with a monitor during trading sessions; trade
  plans with their alerts and a check of every trade; pre- and post-market briefs;
  the close snapshot made by the backend at 15:40.
- **Assistant boundaries.** Read tools for quotes, the watchlist, alerts, plans,
  briefs, trades, notes, bars, indicators and news; every write is a proposal you
  confirm; provider and stored text fenced as untrusted data; numbers checked
  against the turn's data.
- **Backtest, round 1.** One run service, five presets, buy-and-hold and index
  comparison, dated fee schedules, T+1, lots and limit rules, code-written
  headline and caveats, a reproducibility manifest and reruns.
- **Interface.** Six sections, a shared UI kit, light and dark themes, a phone
  layout with a bottom bar, settings in seven sections.

**Earlier**

- 3.1: the transaction ledger with previews, confirmations, reversals, native-currency
  cash, standard CSV import and the research journal.
- 3.2: the stock research workbench (manual and agent experiments, immutable
  datasets, schedules, CLI, HTTP and MCP) and the Feishu card workbench.

## Next release

| Item | What it means |
|---|---|
| Corporate actions | Propose A-share cash dividends and 送转 from public data as ledger previews (A-share stocks only; Hong Kong and US withholding stays manual) |
| Screenshot to draft | Turn a broker screenshot into an editable row table that you correct before any preview is made; costs are never inferred from profit or current price, and the flow is off without a vision model |
| Assistant dock | Redesign the chat dock: inline proposal cards and a "生成入账草稿" path into 记一笔 |
| 组合走势 | Rebuild the history page on the new layout (at `/history`, with `/tracker` redirecting), later from daily valuations of the ledger |
| Backtest round 2 | 交易复盘 (replay your own trades from the ledger against buy-and-hold and the index), setup statistics as studies and assistant tools, robustness and out-of-sample checks over small parameter grids, and portfolio and 定投 backtests |
| Cleanup | Remove `POST /api/settings/cash` and the `/api/portfolio` holdings endpoints; add the mainland 2027 holiday table once the exchanges publish it |

## Later

- **Performance measurement.** Time-weighted and money-weighted (XIRR) returns
  reported separately with their assumptions; investment P&L apart from currency
  P&L and realized apart from unrealized; historical price selection so a
  backdated correction can regenerate old valuations.
- **Accounting depth.** Lot-level / FIFO accounting and tax reports; per-fund units
  instead of one CNY fund value; 北交所 and B shares in the ledger.
- **Research to decisions.** A security page with positions, trades, thesis
  versions, sources, catalysts and the next review date; reports that cite source
  URLs and retrieval dates; rolling and walk-forward evaluation in the research lab.
- **Reach.** An English interface, a red/green preference, a shareable report with
  privacy controls.
- **Deployment.** Authenticated multi-user or remote deployment needs a separate
  design; until then the app is a single-user local service (see
  [SECURITY.md](SECURITY.md)).

## Not planned

Broker connections, order placement or paper trading; minute or tick data;
shorting, margin and options in the backtest; strategies written by a model; an
arbitrary-code sandbox.

Contributions are welcome where an item has a clear acceptance check; see
[CONTRIBUTING.md](../CONTRIBUTING.md). Prefer small, reproducible changes over new
integrations.
