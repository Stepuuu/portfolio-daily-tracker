# Roadmap: a dependable portfolio and research journal

The direction is a private workspace connecting **records → evidence → decisions →
review**. An AI response must never be the only record of a portfolio change.
This roadmap describes pending work; it is not a list of shipped features.

## 1. Ledger foundation — shipped; further reconciliation planned

The optional SQLite ledger now provides opening migration, native cash, decimal
and fractional quantities, weighted average costs including fees, cash flows,
FX exchanges, transfers, splits, reversals, standard CSV deduplication,
revision-checked previews and idempotent confirmations. Snapshots read the ledger
after explicit initialization. AI tools prepare proposals; users confirm in the UI.

Remaining: broker-specific column mapping, lot-level/FIFO accounting, account
reconciliation statements, scalable history pagination, and automatic regeneration
of historical valuations after backdated corrections. See [ledger conventions](LEDGER.md).

## 2. Trustworthy valuation and performance

Store quote source, quote timestamp, market timezone and FX timestamp. Separate
quote staleness from snapshot generation time. Add exchange calendars, historical
price selection and a quote refresh/retry panel. Handle missing data explicitly.

Acceptance: deterministic fixtures cover cash-only accounts, borrowing, foreign cash,
dividends, fees, deposits around valuation boundaries, splits and holidays. Report
TWR and XIRR separately with their assumptions; compare to the same-currency benchmark.
Separate investment P&L from currency P&L and realized from unrealized gains.

## 3. Research that connects to decisions

An append-only thesis/invalidation/review journal is available. Next, introduce a security page containing positions, transactions, thesis, sources,
valuation assumptions, catalysts, invalidation conditions and next review date.
Reports should cite source URL and retrieval date; show where evidence is missing.
Keep thesis versions and link decisions to the version known at the time.

Acceptance: an imported or manually created research note can be attached to a
security and revisited alongside its subsequent price and portfolio decisions.
An AI summary links to original evidence. Editing an assumption does not erase the
old conclusion. Scheduled work survives restarts without duplicate publishing.

## 4. Daily use and onboarding

- Extend standard CSV previews with broker-specific mapping and per-row diagnostics.
- Improve the existing demo, CSV and opening-balance onboarding with broker templates.
- Search by ticker/name/account; saved filters and watchlists.
- Mobile quick entry; keyboard navigation; English/Chinese locale and red/green preference.
- Weekly review: changes, concentration, thesis updates, unanswered questions.
- Export a self-contained report with privacy controls and an optional redacted view.

Acceptance: a new user can see a fictional portfolio without keys and record their
first verified change without editing JSON. Errors explain what happened and what
can be retried. Browser tests cover mobile, empty, offline and partial-data states.

## 5. Open-source release and maintenance

Ship screenshots from fictional data, a 60-second setup walkthrough, versioned
releases, migration notes, a changelog, and an architecture overview. Keep the
public core portable; machine-specific paths, credentials and personal schedules
belong in private adapters. Add contract tests so private fixes can flow upstream.

Track installation success, first-record completion, repeat usage and issue
resolution using voluntary feedback. Stars are a secondary signal, not a product
guarantee. Prefer small reproducible releases over adding more integrations before
the accounting loop is trustworthy.
