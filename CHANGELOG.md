# Changelog

## 3.2.1 — 2026-09-14

### Feishu capital flows

- Add funds entering or leaving an account to the daily change list, updating
  native-currency cash and CNY contributed capital together. Existing positions
  and their per-share costs are preserved.
- Reconcile account principal separately when cash has already been updated.
  Final cash and principal reconciliations apply after trades and capital flows,
  avoiding duplicate changes when entries share a batch.
- Show contributed capital in account cards and full before/after previews.
  Foreign-currency flows require an explicit CNY capital amount; no current FX
  rate is silently substituted. Existing drafts survive the upgrade.
- Regression checks cover account isolation, duplicate confirmation, interrupted
  writes, and deposits/withdrawals remaining separate from investment returns.

## 3.2.0 — 2026-09-12

### Feishu workbench

- Native private-chat cards for daily updates, account balances, saved valuations,
  report summaries and manual or agent research, including history and cancellation.
- One-click unchanged daily reports and persistent, editable batches of up to 30
  changes across accounts and products. Account-scoped product selection supports
  partial or full sales; cash and fund reconciliations remain separate.
- Final cash balances override trade-calculated cash once per account/currency.
  One complete preview and confirmation saves the batch and queues one report;
  interrupted commits resume without repeating trades or losing the draft.
- Explicit currency units, Decimal validation, fractional shares, stale-preview
  rejection, idempotent receipts and shared locks across legacy writers.
- Durable card delivery, retryable report jobs and research progress updates reuse
  the bot's existing connection. Daily reminders can open the update card directly.
- A Linux process manager and private configuration initializer support local setup
  without Docker. Bot callback configuration and optional menus remain console steps.
- [English / 中文 setup guide](docs/FEISHU_WORKBENCH.md). Card writes support dated
  legacy holdings; activated transaction ledgers keep their existing confirmation UI.

### Stock research workbench

- A shared manual/agent workflow for research questions, immutable datasets,
  registered learning templates, experiment history, comparison and JSON export.
- Synthetic learning data, validated OHLCV CSV imports, read-only cache imports
  and explicit market downloads with source, adjustment and content identities.
- Momentum, mean-reversion and volatility templates using NumPy Ridge regression,
  training-only scaling, chronological splits, label-boundary purging, validation
  selection and final test evaluation. Baseline errors, next-open simulation,
  configurable costs and method limitations accompany results.
- Autonomous planning, bounded experiments, validation feedback, candidate freezing
  and final interpretation with experiment references. Failed experiments remain
  visible; invalid evidence and exhausted budgets fail the run.
- API and official Codex/Claude Code CLI connection adapters. Profiles contain
  environment variable names rather than secret values. Installation/login checks
  are separate from model calls and do not guarantee model or quota availability.
- Persistent SQLite jobs, leases, checkpoints, append-only events, cancellation,
  linked retries and schedules with interval, trigger limit and pause controls.
  The schedule API can refresh cache/market datasets into new immutable versions.
- HTTP tools, a constrained stdio MCP bridge, the `scripts/research.py` JSON CLI,
  and explicit Python template/provider registration. Plugin parameter schemas
  render in the workbench; unregistered generated code is not executed.

### Usability and operation

- Responsive research pages with recoverable drafts, task links, polling recovery,
  visible failures, metrics, curves, learning explanations and collapsible method
  assumptions. Model connections are also available in Settings.
- Settings reports rejected and partial saves; balance editing directs users to
  ledger entries. Research and Settings headers do not fetch account totals.
- `make lab` starts research without portfolio/chat initialization. `make demo`
  isolates fictional portfolio and research storage, keeps portfolio edits disabled
  and permits synthetic manual experiments. Local launch needs no Docker daemon.
- Chinese and English research guides explain methods, CLI use, extensions and
  limits. Research storage remains local, but remote models receive research
  context; exported results are not automatically redacted.

This release supports stock daily-bar research and learning. It does not add
options analytics, live order execution, multi-tenant authentication or an
arbitrary-code sandbox. Read the [research guide](docs/RESEARCH.md),
[中文研究指南](docs/RESEARCH_CN.md) and [extension interfaces](docs/RESEARCH_EXTENSIONS.md).

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

## Historical milestones

V1, V2 and V3 were product stages before formal GitHub Releases. The dates below
come from archived documentation and repository history; they do not establish
separate `v1.0.0`, `v2.0.0` or `v3.0.0` releases. V2's engine and V3's dashboard
were first committed together, so their repository dates coincide.

### V3 — AI trading assistant

**First repository record: 2026-03-08.**

- Combined the portfolio engine with AI chat, a React dashboard, strategy
  backtesting, multi-market data and scheduled reports.
- March follow-up updates added CLI and agent tooling, improved holdings parsing
  and market-data reliability, and introduced streaming chat and market news.

Source: [the V3 introduction](https://github.com/Stepuuu/portfolio-daily-tracker/commit/08c2bfce5c52fe65051e32f7e919285c0922cf4d)
and the [March 2026 commit history](https://github.com/Stepuuu/portfolio-daily-tracker/commits/main/?since=2026-03-01&until=2026-03-31).

### V2 — Self-hosted portfolio engine

**First repository record: 2026-03-08.** A separate V2 release date is not recorded.

- Replaced the spreadsheet workflow with a Python engine and local JSON/CSV
  holdings, snapshots and history.
- Added multi-market quotes, currency conversion, risk and leverage metrics,
  portfolio groups and automated Markdown reports.

Sources: the [engine's V1/V2 comparison](engine/README.md#vs-v1-google-sheets)
and its [first repository commit](https://github.com/Stepuuu/portfolio-daily-tracker/commit/08c2bfce5c52fe65051e32f7e919285c0922cf4d).

### V1 — Google Sheets

**Earliest dated guide entry: 2025-09-25. First repository commit: 2026-01-17.**

- Used Google Sheets and Apps Script for daily portfolio snapshots, Sina/Google
  Finance price updates, asset charts and monthly profit summaries.
- September–October 2025 guide entries document trigger fixes, non-trading-day
  handling and changes to cost inputs and summary layouts.

Sources: the [archived guide's update log](v1-google-sheets/Automation_Mini_App_Guide_EN.md#update-log-translated)
and [V1 user guide](v1-google-sheets/User_Guide_EN.md).
