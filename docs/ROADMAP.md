# Roadmap: a dependable portfolio and research journal

The direction is a private workspace connecting **records → evidence → decisions →
review**. An AI response must never be the only record of a portfolio change.
This roadmap separates shipped foundations from remaining work. Status reflects
version 3.2.0; the [changelog](../CHANGELOG.md) lists release details.

## 1. Ledger foundation — shipped; further reconciliation planned

The optional SQLite ledger now provides opening migration, native cash, decimal
and fractional quantities, weighted average costs including fees, cash flows,
FX exchanges, transfers, splits, reversals, standard CSV deduplication,
revision-checked previews and idempotent confirmations. Snapshots read the ledger
after explicit initialization. AI tools prepare proposals; users confirm in the UI.

Remaining: broker-specific column mapping, lot-level/FIFO accounting, account
reconciliation statements, scalable history pagination, and automatic regeneration
of historical valuations after backdated corrections. See [ledger conventions](LEDGER.md).

## 2. Trustworthy valuation and performance — further work planned

Store quote source, quote timestamp, market timezone and FX timestamp. Separate
quote staleness from snapshot generation time. Add exchange calendars, historical
price selection and a quote refresh/retry panel. Handle missing data explicitly.

Acceptance: deterministic fixtures cover cash-only accounts, borrowing, foreign cash,
dividends, fees, deposits around valuation boundaries, splits and holidays. Report
TWR and XIRR separately with their assumptions; compare to the same-currency benchmark.
Separate investment P&L from currency P&L and realized from unrealized gains.

## 3. Reproducible stock research — shipped in 3.2; broader methods planned

Shipped: a shared manual/agent workbench with synthetic, CSV, cache and market
datasets; immutable data versions; momentum, mean-reversion and volatility
templates; chronological splits, label-boundary purging, baseline comparisons,
cost assumptions and final holdout evaluation. Agent iterations use validation
evidence before freezing a candidate. Local jobs, checkpoints, cancellation,
retry, schedules and JSON exports preserve the experiment record.

API and official Codex/Claude Code CLI adapters, a JSON CLI, HTTP tools, an MCP
bridge and trusted Python template/provider registration are available. UI
parameters and provider choices follow the registry. Connection checks do not
prove every subscription environment or model is usable. See the
[research guide](RESEARCH.md), [中文指南](RESEARCH_CN.md) and
[extension interfaces](RESEARCH_EXTENSIONS.md).

Remaining: richer point-in-time datasets, rolling/walk-forward evaluation,
uncertainty and sensitivity analysis, stronger controls for repeated historical
holdout use, and realistic market execution constraints. Options chains, implied
volatility surfaces, Greeks and multi-leg options backtesting are not shipped.
Arbitrary generated-code execution would require a separate sandbox design.

## 4. Research that connects to decisions — journal shipped; links planned

An append-only thesis/invalidation/review journal is available. Next, connect
workbench experiments to a security page containing positions, transactions, thesis, sources,
valuation assumptions, catalysts, invalidation conditions and next review date.
Reports should cite source URL and retrieval date; show where evidence is missing.
Keep thesis versions and link decisions to the version known at the time.

Acceptance: an imported or manually created research note can be attached to a
security and revisited alongside its subsequent price and portfolio decisions.
An AI summary links to original evidence. Editing an assumption does not erase the
old conclusion. Research schedules already persist across restarts; scheduled
report publishing is a separate future workflow.

## 5. Daily use and onboarding — research workflow shipped; further work planned

Shipped: a credential-free synthetic experiment through `make lab`, isolated
fictional data through `make demo`, mobile navigation, research drafts and task
links, recovery after polling failures, experiment comparison and JSON export.
The ledger includes transaction previews and explicit confirmation. Settings
reports failed saves and routes balance changes through ledger entries.

Remaining:

- Extend standard CSV previews with broker-specific mapping and per-row diagnostics.
- Improve the existing demo, CSV and opening-balance onboarding with broker templates.
- Search by ticker/name/account; saved filters and watchlists.
- Mobile quick entry; keyboard navigation; English/Chinese locale and red/green preference.
- Weekly review: changes, concentration, thesis updates, unanswered questions.
- Export a self-contained shareable report with privacy controls and an optional
  redacted view. Current JSON exports are private records, not automatic redactions.

Acceptance: a new user can see a fictional portfolio without keys and record their
first verified change without editing JSON. Errors explain what happened and what
can be retried. Browser tests cover mobile, empty, offline and partial-data states.

## 6. Open-source release and maintenance — ongoing

Available: fictional-data screenshots, local setup commands, research and ledger
guides, migration notes, a changelog, CI checks and extension documentation.
Continue improving release walkthroughs, contract tests and compatibility coverage
so fixes can flow between deployments. Keep the public core portable; deployment
configuration and credentials belong outside source control. Multi-tenant
authentication and distributed remote workers require separate design work.

Track installation success, first-record completion, repeat usage and issue
resolution using voluntary feedback. Stars are a secondary signal, not a product
guarantee. Prefer small reproducible releases over adding more integrations before
the accounting loop is trustworthy.
