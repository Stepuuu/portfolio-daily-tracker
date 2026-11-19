# Accounting and data conventions

Two valuations exist side by side. The **Today page** (`/api/today`) values the
confirmed ledger at live quotes whenever you look; the **daily snapshot** (made at
15:40 on A-share trading days, or by the engine scripts) is a saved valuation in
`snapshots/` and `history.csv` that the 组合走势 page and the reports read. Both use
the same rules for a position's value; the differences are below.

## Live valuation (Today)

Implemented in `dashboard/core/market/valuation.py` and `today.py`:

- A position is worth quantity × price × currency rate; an account is its
  positions plus cash in every currency plus its CNY fund value. A zero cash
  balance needs no rate.
- A missing price or rate makes that figure `null` (shown as 不可用) and every
  total that contains it `null` too, with the reason. A total is never the sum of
  only the known parts, and an unknown value is never 0. A price in another
  currency than the ledger books the holding in counts as missing (B shares).
- A figure is **verified** when its quote is live, delayed, or a close in a
  market and year the holiday table covers, and its rate is verified. Otherwise
  it keeps its number and is marked estimated (估算), with the reason. A close is
  verified only by a quote observed once that close is final
  (`SessionClock.close_settled` in `dashboard/core/market/calendar.py`): the
  mainland at 15:00, Hong Kong after its closing auction, New York 30 minutes
  after the bell (16:30 New York time, 13:30 on an early-close day), because its
  official closing price is published minutes after the close. Until then a New
  York close is estimated with the reason 收盘价尚未最终确定. A weight computed
  from an estimated total is estimated too.
- Quantities and average costs are used exactly as the ledger holds them; the
  page shows a figure past 8 decimal places as `≈` with 8 places and its full
  value on hover.
- **Day profit** follows the shares: quantity now × price − quantity before ×
  previous close − Σ(bought quantity × price + fee) + Σ(sold quantity × price −
  fee). It covers price moves and trade fees on listed positions; it leaves out
  dividends, standalone fees, currency moves on the previous value and fund
  revaluations. It is measured per instrument against the session its own quote
  belongs to (a US quote at 10:00 in Shanghai is yesterday's New York session).
- **Unrealized profit** (持仓盈亏) is open positions against their weighted average
  cost. **Cumulative profit** (总盈亏) is total assets minus 累计净投入 (the ledger's
  contributed capital: opening capital plus deposits, less withdrawals, across
  transfers).
- Percentages: day profit ÷ (total − day profit); unrealized profit ÷ Σ quantity ×
  average cost × current rate; cumulative profit ÷ contributed capital. Each is
  `null` when a part is unknown or the denominator is not positive.
- Money is rounded to the cent where it is first computed (per row and per cash
  balance); totals are sums of the rounded parts, so displayed totals reconcile.
- Trade dates are exchange-local: a US trade executed at 01:00 Tuesday in
  Shanghai belongs to Monday in New York and must be dated Monday.

## Legacy holdings files

The sections below describe the engine's dated holdings JSON and its snapshots.
With a ledger, the snapshot reads the confirmed ledger instead of a holdings file.

### Multi-currency cash

Existing holdings with a numeric `cash` continue to mean **CNY cash**. New holdings
may provide `cash_balances` as the authoritative native-currency balances:

```json
{
  "cash": 10000,
  "cash_balances": { "CNY": 10000, "USD": 500, "HKD": -2000 },
  "fund": 0,
  "cost_basis": 50000,
  "positions": []
}
```

If both are present, holdings `cash` must equal `cash_balances.CNY` (or zero if
CNY is omitted). A mismatch is rejected instead of silently double-counting.
Negative cash is permitted for borrowing. Only CNY, USD and HKD are currently supported.

In a **snapshot**, `cash` is the converted CNY total for existing reports and charts;
`cash_balances` preserves the originals and `cash_values_cny` records the conversion.
Do not copy snapshot `cash` into holdings while retaining the original balances.

```bash
python3 engine/scripts/portfolio_manager.py set-cash \
  --group "Account" --currency USD --value 500 --date 2026-09-09
```

Use your actual account name. `--currency` defaults to CNY; updating CNY preserves
other currencies. The daily parser recognizes explicitly named USD/美元 and
HKD/港元 cash clauses, but ambiguous language should use the CLI. This release does
not claim a complete conversational transaction ledger or automatic FX trading.
`fund` and account `cost_basis` remain CNY amounts. Cash editing records a balance;
it does **not** infer an external deposit, withdrawal, or transfer.

## Valuation and returns

- Missing, non-positive, NaN or infinite active-position prices refuse valuation.
- Missing required foreign exchange rates refuse valuation in the open edition;
  arbitrary static exchange rates are no longer substituted.
- Account `cost_basis` means contributed capital for the legacy return calculation,
  not the sum of currently held lots' purchase costs. Changing it changes inferred flows.
- Cumulative return percentages (`total_return_pct`, an account's `return_pct`, a
  position's `profit_pct`) divide profit by contributed capital or position cost.
  When that denominator is zero or negative, for example after withdrawing more
  than was paid in, the percentage is `null` in snapshots, empty in `history.csv`
  and shown as "—" in reports. The profit amount is still reported.
- Daily market change is `value change - contributed-capital change`. Buying stock
  with existing cash is an internal exchange, not an external withdrawal.
- The Markdown report's 日变动 row, its 日涨跌 trend column and the snapshot CLI
  summary show this flow-adjusted market change (`market_daily_change`). An
  inferred deposit or withdrawal (`capital_change`) is listed on its own row
  (当日入金 / 当日出金) and is never presented as a gain or loss. `daily_change`
  in snapshots and `history.csv` remains the raw change in total value, flows
  included; history rows without the flow-adjusted column fall back to it.
- Daily returns use a beginning-value denominator and assume external flows occur
  at period end. This is an **approximation**, not exact intraday TWR or XIRR.
- Maximum drawdown uses linked daily returns and retains the worst historical
  decline. `current_drawdown_pct` reports the current decline separately.
- Position-level purchase cost still uses the snapshot FX rate. Historical FX lot
  accounting, realized gains, fees, dividends, splits and taxes need a transaction ledger.
- Monthly return remains market P&L divided by beginning-month assets: the last
  recorded value before the month, or the first recorded value of the month when
  tracking began during it (the snapshot and the tracker API agree); annualized
  metrics assume 252 observations per year. They are not rigorous for irregular or
  mixed-market histories. Missing history is not reconstructed into fictional trades.

The distinction between flows and investment returns is also explained in the
[Portfolio Performance methodology](https://help.portfolio-performance.info/en/concepts/performance/time-weighted/).
This project does not claim that its legacy approximation implements that exact method.

## History, writes and migration

Snapshots fill gaps in CSV history; CSV rows take precedence for overlapping dates.
Inputs are sorted and deduplicated; future and current-date history are excluded
when calculating a new snapshot. Synthetic API snapshots explicitly identify the
date from which their position details were borrowed.

Snapshot/holdings/history writes use temporary files and atomic replacement, which
prevents truncated individual files. Complete read-modify-write operations are
serialized by one advisory lock, `<portfolio>/.portfolio-update.lock`
(`engine/scripts/portfolio_write_lock.py`). The holdings CLI
(`portfolio_manager.py`), the daily update and pipeline CLI
(`portfolio_daily_update.py`), the snapshot CLI (`portfolio_snapshot.py`, except
`--dry-run`, which writes nothing), the dashboard agent's holdings and pipeline
tools and the Feishu workbench all take it, so they wait for each other instead
of losing updates. A snapshot run on its own releases the lock while it
downloads quotes and exchange rates; under the lock again it checks that the
holdings and configuration it priced are unchanged before it writes, downloads
once more if they changed, and saves nothing if they changed a second time.
The snapshot started by the pipeline runs under the lock its
parent holds, but only after checking it: the parent passes the lock-file path
in `PORTFOLIO_WRITE_LOCK_INHERITED` only while it really holds that lock, and the
child ignores a path that is not its own lock file and takes the lock itself if
a non-blocking probe shows that nobody holds it. Pipeline children run in their
own session: on a timeout the child and everything it started (for example the
Python process under `conda run`) are stopped before the lock is released, and
a child keeps the lock taken if its parent dies first. While the Feishu workbench
delivers a daily report the lock is released; a snapshot started then takes the
lock itself, and the workbench records the delivery after that snapshot ends.

The lock is advisory and local to one host. It does not cover files edited by
hand or by programs that do not take it, and **it is not a multi-file transaction
or conflict detection**: an interrupted run can leave, for example, a snapshot
without its `history.csv` row until the snapshot is generated again. Back up the
complete portfolio directory before a migration.

Existing records are not automatically rewritten. Old snapshots can retain old
metric definitions. Do not regenerate an old date using today's quote: the live
snapshot CLI does not yet implement historical quote selection. `--dry-run` does
not persist an inherited holdings file.

Since 4.0.0 the confirmed ledger is the only portfolio store. Once it exists, the
v3.2 manual portfolio (`data/portfolio.json`) and the engine's holdings-writing
commands refuse changes; both legacy files remain readable as migration sources
for the setup wizard. The 组合走势 page (`/tracker`) still reads the saved
snapshots, so it shows a day only after that day's snapshot exists.
