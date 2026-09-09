# Accounting and data conventions

## Multi-currency cash

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
- Daily market change is `value change - contributed-capital change`. Buying stock
  with existing cash is an internal exchange, not an external withdrawal.
- Daily returns use a beginning-value denominator and assume external flows occur
  at period end. This is an **approximation**, not exact intraday TWR or XIRR.
- Maximum drawdown uses linked daily returns and retains the worst historical
  decline. `current_drawdown_pct` reports the current decline separately.
- Position-level purchase cost still uses the snapshot FX rate. Historical FX lot
  accounting, realized gains, fees, dividends, splits and taxes need a transaction ledger.
- Monthly return remains market P&L divided by beginning-month assets; annualized
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

Snapshot/holdings/history writes use temporary files and atomic replacement.
This prevents truncated individual files; **it does not provide multi-file transactions,
conflict detection, or concurrent-writer locking**. Keep one writer until the ledger
milestone is complete. Back up the complete portfolio directory before a migration.

Existing records are not automatically rewritten. Old snapshots can retain old
metric definitions. Do not regenerate an old date using today's quote: the live
snapshot CLI does not yet implement historical quote selection. `--dry-run` does
not persist an inherited holdings file.

The dashboard's manual portfolio and engine holdings are still separate stores.
The tracker view uses engine snapshots. Editing the manual portfolio does not
currently constitute an engine transaction; unifying this is the top roadmap item.
