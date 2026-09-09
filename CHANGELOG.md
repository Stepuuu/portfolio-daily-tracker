# Changelog

## 3.1.0 — 2026-09-09

### Transaction ledger and research journal

- Explicit opening migration; old holdings and snapshots are preserved.
- Aggregate CNY fund subscriptions, redemptions and reconciled valuation updates.
- Decimal native cash and fractional shares; buy/sell fees, cash flows, transfers, FX, splits and reversals.
- Before/after previews, stale-preview conflicts and idempotent confirmation receipts.
- Standard CSV import with source/transaction-ID deduplication and conflict detection.
- SQLite backup, JSON transaction export, and append-only research notes with review dates.
- Dashboard and OpenClaw tool registries expose proposals, with confirmation in the UI. Screenshot recognition no longer changes account balances.
- After ledger activation, snapshots use confirmed holdings and legacy balance editing is blocked.

### Reliability and onboarding

- Native CNY/HKD/USD cash balances, currency-aware CLI updates and cash disclosure.
- Refuse invalid active-position quotes and missing required FX instead of zeroing
  holdings or substituting arbitrary exchange rates.
- Correct cash-flow handling for internal purchases, historical ordering, maximum
  drawdown and weighted cross-account cost/available-quantity synchronization.
- Atomically replace individual portfolio files; dry-run no longer persists inherited holdings.
- Validate tracker dates and strategy filenames. Disable Python strategy upload by default.
- Mark synthetic history and reused market data; separate tracker totals from the manual portfolio.
- Add offline demo, regression tests, CI definitions, mobile sidebar and lazy-loaded routes.
- Repair backend Docker build context, persist application data, default to loopback
  bindings, align development ports and preserve streamed responses through nginx.
- Scope stop commands to this checkout's recorded launcher processes.
- Update frontend dependencies; require Node.js 22.12+ and retain lockfiles.

Existing holdings and snapshots are not automatically migrated. Read the
[ledger guide](docs/LEDGER.md) before activating an existing account and
[accounting conventions](docs/ACCOUNTING.md) before interpreting performance.
Weighted average cost is supported; tax lots/FIFO, strict TWR/XIRR and authenticated
multi-user deployment remain on the [roadmap](docs/ROADMAP.md).
