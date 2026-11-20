# Changelog

## Unreleased

### Added

- **Extensions** (`TRACKER_EXTENSIONS=pkg.mod,other.mod`): separately installed
  Python modules add API routes, background tasks, chat-assistant tools and
  notification channels, and can be told each time a day's close report is
  finished, again when a late entry makes the day's report anew
  (`on_close_report`, read from the pipeline's checkpoint, once per report
  content, in a thread of its own, so it cannot delay the snapshot). A private
  installation is the released application plus its own package. A module
  that fails is logged and listed by `GET /api/app/status` (`extensions`, only
  when the variable is set) and never takes the API down. Demo mode loads none;
  lab-only mode mounts their routes but starts no background task. Extensions
  run with the backend's privileges. See [docs/EXTENSIONS.md](docs/EXTENSIONS.md).
- **Feishu workbench pages from extensions.** The Feishu workbench imports the modules named in its own
  environment's `TRACKER_EXTENSIONS` and adds the card pages of those that define `workbench_pages()`:
  actions on its own cards, checked like built-in ones (allowlist, the card's nonce and session, a
  `.confirm` only from the page's `_preview`), views within the card budget, a home button, and
  `external` actions for buttons on cards another process sent with the same bot, answered in the callback
  without a nonce; such a page must check itself that the card is one it sent and still current. Built-in
  actions still need their nonce, and built-in names and namespaces cannot be taken. A module that fails
  is logged and left out. Without the variable the workbench is unchanged. See
  [docs/EXTENSIONS.md](docs/EXTENSIONS.md#feishu-workbench-pages).
- `TRACKER_REPORTS_DIR`: the directory of the daily Markdown reports, by default `engine/reports` beside
  the code, as before. The pipeline saves them there, and the close snapshot, the extensions' close reports
  and the Feishu workbench (its 日报 card and the pipeline it starts) read them there, so they can be kept
  inside the portfolio directory and its backups.
- `BACKEND_START_TIMEOUT`: how many seconds `start.sh` waits for the backend's
  `/health` (default 30, as before; 1 to 3600), named in the message printed
  when it gives up. Compose uses it as the backend health check's
  `start_period` (default none, as before).

- **Tencent quotes.** Tencent (`qt.gtimg.cn`) is a third quote source for Today, the watchlist, alerts,
  briefs, plans and the setup check. By default Eastmoney is asked first, then Tencent, then Yahoo, each for
  what the one before could not price (the same fallback rules: a stale price, a previous close or a quote
  without an exchange time asks the next source, and the best answer is kept), and every quote names the
  source it came from (腾讯 on the page). It covers Shanghai and Shenzhen shares, ETFs, B shares, Hong Kong
  and US listings and every index the app knows, with price, previous close, name, currency and the
  exchange's own time. A suspended share or one without a trade yet is valued at its previous close (估算)
  as with Eastmoney; a delisted or never listed share, which Tencent still answers with an old price, has
  none. Like Eastmoney it is reached directly whatever proxy the environment names; `TRACKER_TENCENT_PROXY=1`
  sends it through that proxy; `TRACKER_QUOTE_SOURCE=tencent` uses it alone for quotes. Measured on
  2026-11-19 on mainland hosts where Eastmoney's quote endpoints did not answer: Tencent replied directly in
  about 0.2 s (2 s through the proxy). Its timeout is 3 s (4 s through the proxy), shorter than Eastmoney's,
  and a source that fails is left alone for a while, as before.
- **The daily snapshot prices from Tencent** the holdings Yahoo did not price, in one request, by Today's
  reading of Tencent and Today's close rules, and only from a closing row: stamped on its session's own date,
  at or after the closing print (mainland 15:00, Hong Kong 16:08, New York 16:00) and, in New York, within a
  minute of the bell, since Tencent does not say whether a later price is an after-hours trade. A row stamped
  earlier (mid-session, before the closing auction) is never booked, however late the run; an earlier
  session's close is carried forward only from a row Tencent marks suspended. A holding still waiting for its
  market's close to become final is not asked (every source waits); one waiting for the closing print to reach
  Yahoo is, and only Tencent's verified close ends that wait. The request gets at most 6 s and is skipped when
  less than that and 30 s for the exchange rates is left of the pipeline's 120 s. The script prints which
  holdings it priced from Tencent, each position of the snapshot records its price's source (`price_source`:
  `yahoo` or `tencent`), and the daily report's footer names the sources the day used (数据来源: Yahoo Finance、
  腾讯); a day priced from Yahoo alone gets the same report as before. `tencent_url` in the
  engine's `config.json` replaces the endpoint; `TRACKER_QUOTE_SOURCE=tencent` prices from Tencent alone,
  `yahoo` and `eastmoney` from Yahoo alone, as before.
- The setup check's 行情源 item names the source that answered and the order the sources are asked in, and
  its proxy hint lists `TRACKER_TENCENT_PROXY=1` next to `TRACKER_EASTMONEY_PROXY=1`.

- **Feishu card to ledger.** Once a ledger is active the Feishu workbench's daily
  card records into it instead of being refused. Each list item becomes ledger
  events: buys and sells in every market, 港股通 trades settled in CNY as `fx`
  CNY→HKD plus the HKD trade (as the broker-file importer books them), deposits and
  withdrawals (a foreign one with the CNY principal it moves), 本金更新 as a CNY
  transfer of the difference, 基金估值 as `fund_value` (nothing when it equals the
  ledger's), and "cash is now X" as one
  `interest` (more) or `fee` (less) with a note, which never changes 累计净投入. A
  fee left empty is estimated from the account's fee profile and marked 估算, as in
  记一笔. **核对全部变动** shows the ledger's own proposal (each account's cash, fund
  value, contributions and positions before → after, warnings) and **确认入账并更新日报**
  confirms it; the receipt lists what was booked. See
  [docs/FEISHU_WORKBENCH.md](docs/FEISHU_WORKBENCH.md#with-the-transaction-ledger--启用交易账本后).
- New card fields with a ledger: 港股通人民币成交金额, 允许融资 on a buy, 差额说明
  on a cash balance (required when the same list moves that currency with a
  transfer), and the menu entry 更正数量、成本或本金, which explains how to reverse
  and re-enter in the web app.

### Fixed

- **Brackets and asterisks in Feishu card text are shown as themselves.** The card service escaped the
  `#` of the entities it had just written for `< > * ~ [ ] ( )`, so a name such as "A股(沪)" appeared on the
  card as `A股&#40;沪&#41;`. Each special character is now escaped exactly once.
- **A day's snapshot is never made from the day before's prices.** Run before the day's session had closed
  (the Feishu card's pipeline after an entry dated today, at 02:46), the snapshot booked the mainland's and
  Hong Kong's previous close as the day's, without an estimate mark, and the close pipeline then only
  completed that snapshot, so the day never got its close. Each market's price must now be the close of
  the session the day books (the mainland's and Hong Kong's of that day, New York's of the night before);
  before it is final the snapshot waits, as for any close not final yet, by every route that makes it. A
  market closed that day carries its last close, as before. A snapshot an older version saved that way
  (made before a close it books was final, by its `generated_at`) is made again by the pipeline, with its
  history row, report and push.

- **`--action pipeline` works with a ledger.** It cloned a legacy holdings file first, which the ledger
  refuses, so the command the docs and the auto pipeline's notice give after a late entry failed with a
  traceback on every ledger portfolio. With a ledger it now makes the day from the ledger without cloning,
  as `auto-pipeline` and `notify` do; `--action clone` has nothing to clone and succeeds, and `--action update`
  says that holdings change through a confirmed ledger proposal instead of failing with a traceback. Like
  `auto-pipeline`, `--action pipeline` pushes the report only when `feishu_chat_id` is configured: without it
  the report is saved and the run succeeds, instead of exiting 1 with the push left uncertain.
- **Dates and times are Shanghai's whatever the server's time zone.** The engine scripts' `--date` default
  (snapshot, report, daily update, holdings manager), the shell scripts' dates and `portfolio_scheduler.py`'s
  days and times (`--notify-time` and `--pipeline-time` are now Beijing time) were the server's local date
  and time: on a server set to UTC the default day was the day before for eight hours. The snapshot's
  `generated_at` is stored with its offset (`+08:00`), and the report footer's 生成时间 is shown in Beijing
  time with its zone (`2026-11-20 15:40:12（北京时间）`); an older snapshot's time without a zone is read as
  the local time of the machine, as it was written.

### Changed

- **The Feishu card's ledger preview lists only what the list changes.** It no longer repeats unchanged cash,
  fund value and contributed capital, the accounts the list does not touch, the fund note the item's own line
  already says, an English duplicate of the negative-cash warning, or a negative balance the list does not move.
- **The daily card can start from your usual entry.** An optional `daily_defaults` in the card service's
  `config.json` makes 有变动 the first card's main button and preselects an account and kind for the first item
  of a list (docs/FEISHU_WORKBENCH.md).
- The card stays exactly-once on the ledger: its operations are keyed on the
  proposal, events carry the source `feishu-card` and an external id from the list
  item, a newer preview withdraws the same person's older one, and a preview the
  ledger moved past is withdrawn and made again instead of confirmed. Editing,
  clearing or previewing a list again withdraws its earlier proposal, also when the
  new preview books nothing. Card proposals appear in the web's 待确认 for 2 hours as
  在飞书卡片上确认: they can be discarded there but only the card confirms them (the
  confirm endpoint refuses them), so a confirmed list is always completed and its
  report queued.
- After a card entry the day's snapshot and report are made again from the ledger
  (until 09:00 the next morning), under the portfolio lock and the checkpoint the
  close snapshot uses, so the same holdings are never pushed twice. Like the close
  snapshot, the card pushes the report only when `feishu_chat_id` is configured;
  without it the report is saved and counts as done (日报 says so) instead of
  waiting for a push that never comes. Older back-dated entries do not remake old
  reports.
- `portfolio_daily_update.py --action notify` no longer fails with an active ledger:
  it sends the evening card without cloning a holdings file.
- The companion reads fee profiles and trade plans from the portfolio directory
  beside its ledger. It chooses its portfolio directory once, as the backend does
  (`PORTFOLIO_DIR`, `QR_PORTFOLIO_DIR`, else the project's own), makes it absolute
  and uses it for everything, including the pipeline it runs; it never edits a
  holdings file elsewhere while that directory has an active ledger.
- A confirmation that books nothing (今日无变动, a list already booked) no longer
  makes and pushes a finished day's report again.
- Until the snapshot engine would book the day's closes for the markets the ledger
  holds (the engine's own rule, `core.market.calendar.snapshot_wait`, shared by both:
  New York's session of the night before counts for the day, and a session still
  under way is waited for), a card entry or 今日无变动 is booked but no snapshot is
  made: one made then would record the previous session's prices as the day's close.
  The receipt says which close it waits for and that the report follows after it, and
  日报 shows it as 等待收盘. The
  card service runs the job itself when the time comes (also when the snapshot
  engine says a close is not final yet), once, from its stored state so a restart
  keeps it, until 09:00 the next morning; 重试日报 stays and never runs it twice.

### Refused with a ledger

- Overwriting a quantity, a cost price or only the principal (本金核对, 资金已入账,
  修正历史本金): the ledger has no such events; reverse the entry in 账本 › 流水 and
  enter it again with 记一笔.
- A 港股通 buy beyond the account's CNY cash, unless 允许融资 is chosen (as for any
  buy; the ledger itself books the `fx` whatever the cash).
- A list item whose booking was reversed in 账本 › 流水: the ledger would count it as
  a duplicate and book nothing, so the card no longer shows it as booked; delete it
  and add it again.

### Not changed

- Exchange rates still come from Yahoo, then Eastmoney, also with `TRACKER_QUOTE_SOURCE=tencent`: Tencent
  answers `whUSDCNY` / `whHKDCNY`, but its USD rate was 0.16 % from Yahoo's onshore rate when compared and
  what its fields mean is not documented.
- Daily bars (the stock drawer's chart, the assistant's price history and indicators) still come from
  Eastmoney's history for A shares, ETFs and Hong Kong and from Yahoo for US listings, without a fallback.
- The chat assistant's older quote tools (`get_stock_quote`, `get_market_indices`, `compare_stocks`) keep
  their own provider chain (Eastmoney, Yahoo, akshare).

## 4.0.0 — 2026-11-19

A new daily desk built on the confirmed ledger. Holdings now come only from the
ledger; you fill it with 记一笔 (record an entry), a broker-file import or a
proposal you confirm, never by editing JSON. Back up `ledger.sqlite3` and
`desk.sqlite3` before upgrading and read the upgrade notes and the known
limitations at the end of this entry.

### Added

**Layout and pages**

- Six sections instead of ten flat pages: 今日 (Today) `/`, 行情 (Market)
  `/market` and `/alerts`, 账本 (Ledger) `/ledger`, `/ledger/positions` and
  `/ledger/import`, 计划与复盘 (Plans and review) `/review`, `/plans`, `/tracker`
  and `/notes`, 研究 (Research) `/lab` and `/backtest`, 设置 (Settings)
  `/settings/<section>`, plus the setup wizard at `/setup`. The chat assistant is
  a dock opened from the header (助手), not a page. Phones get a bottom bar:
  今日 · 行情 · 记一笔 · 助手 · 更多.
- A notification bell (站内通知) for alerts, routines and setup events, a tray of
  pending confirmations (待确认), a code and name search (`Ctrl K` / `⌘K` or `/`) and
  keyboard shortcuts (`N` opens 记一笔, `?` lists the shortcuts).
- A shared UI kit with light and dark themes that follows the system (跟随系统) by default; choose
  浅色 or 深色 in 设置 › 关于与帮助 › 外观 (stored in this browser only). Gains are red and losses
  green on every page; warnings use amber.

**今日 (Today)**

- Confirmed ledger holdings valued at live quotes, per account and in total:
  price, day profit, unrealized profit, weight, native cash per currency and the
  CNY total, plus cumulative profit against contributed capital. Every price
  shows its source and exchange time. A value that cannot be computed shows
  不可用 (unavailable), never 0, and a total that contains it is unavailable too.
  A weight computed from an estimated total carries the 估算 mark too.
- Quantities and average costs come exactly as the ledger holds them. On every
  page a figure with more than 8 decimal places reads `≈` with 8 places and shows
  its full value on hover and to screen readers.
- Today's fired alerts (今日提醒), plans whose review date has come (今日事项) and
  pending ledger proposals are listed beside the holdings.

**Quotes and calendars**

- A new quote layer keyed by exchange-qualified tickers (`SHA:600519`,
  `HKG:00700`, `NASDAQ:AAPL`; indices as `IDX:000300`): Eastmoney first, Yahoo
  as fallback, public endpoints with no account. Each quote has a status, shown
  as a chip: 延迟 (delayed), 收盘价 (close), 已过期 hh:mm (stale), 昨收价 (no trade
  yet today, previous close), 时间未知 (no exchange time), 不可用 (no usable
  price); a live price has no chip.
- Only live, delayed and closing prices in a year the holiday table covers are
  verified. Anything valued from another price, or converted at an unverified
  exchange rate, keeps its number with an amber 估算 (estimated) mark.
- A close is verified only by a quote observed once the close is final. New York
  publishes its official close minutes after the bell, so for 30 minutes (until
  16:30 New York time, 13:30 on an early-close day) a New York close is shown as
  收盘价 marked 估算 with the reason 收盘价尚未最终确定; a quote seen earlier is read
  again after that instead of being promoted by the clock. A regular-session price
  stamped more than a minute before the bell is stale, not the close.
- Exchange holiday tables in `dashboard/core/market/holidays.json`: mainland
  2026; Hong Kong 2026 and 2027 with half days (12:00 close); New York 2026 and
  2027 with early closes (13:00). Each year cites its official source. In a
  year without a table the trading day is decided by weekday alone and the app
  says so (交易日历未核实（按工作日推算）); mainland 2027 follows once the
  exchanges publish it.
- B shares (`SHA:900xxx`, `SHE:200xxx`) trade in US or Hong Kong dollars while
  the ledger books mainland listings in CNY: they cannot be booked, and an
  existing B-share holding is shown as unavailable instead of being added to
  the CNY totals at its dollar price.

**Ledger**

- 记一笔 (record an entry), a dialog available everywhere (`N`, or the 记一笔
  button): pick a kind (买入, 卖出, 分红, 入金, 出金, 换汇, 转账, 拆股/送转, 冲销,
  fund events, 期初余额 ...), search the security by code or name, fill the
  price from the current quote or the previous close (按现价 / 昨收), and get the
  fee estimated from the account's fee profile (marked 估算; overwrite it with
  the figure on your trade confirmation). 预览入账 (preview) shows the
  before/after of cash, shares and cost; nothing is booked until 确认入账
  (confirm). An unconfirmed manual preview expires after 2 hours. 再记一笔 keeps
  the account and date.
- 账本 › 流水 (events, with 冲销 to reverse one) and 账本 › 持仓与成本 (positions
  and average cost per account). To correct an event, reverse it and book it
  again; the 记一笔 form checks holdings and cash against the same events booking
  replays, a later reversal included.
- A new entry of a listing the account already holds under another spelling
  (`HKG:700` and `HKG:00700`, or a US class share written with a dot or a dash)
  is booked under the spelling the account held on the entry's date: of two held
  spellings, the one typed, else the larger position; a listing not held on that
  date keeps the spelling typed, except an entry missed earlier (dated before
  the account's other events of that listing), which takes their spelling when
  they all use one.
  The 记一笔 form finds the position under any spelling. Events already booked
  keep the spelling they were booked with, so an older ledger that holds one
  listing under two spellings still shows two rows, valued apart.
- Per-account fee profiles in 设置 › 账户与费率. The A-share preset charges
  commission 0.025% with a ¥5 minimum (免五 switch), the 0.001% transfer fee and
  0.05% stamp duty on sells, by trade date. Hong Kong and US fees are estimated
  only when you enter a complete model for that market; otherwise there is no
  estimate rather than a guess. The rates were written from public rules and are
  not verified against broker statements.
- A new ledger event kind `interest` (利息归本, the net income of a 逆回购 pair).

**Broker file import (账本 › 导入, `/ledger/import`)**

- Reads 交割单, 资金对账单 and 持仓 exports: CSV, tab or space separated TXT,
  XLS, XLSX and web pages saved as `.xls`; UTF-8, GBK/GB18030 or UTF-16; at most
  5 MB and 20,000 rows. The file type comes from its content, not its name.
- Four steps: 选择文件, 核对列, 预览与去重, 确认. Columns are recognised by name and
  the mapping you correct is remembered per account and header layout. Presets
  name 同花顺, 东方财富 and 通达信 formats; they were written from documented
  column names and are **not verified against every broker's real exports**
  (the page says 预设未经你的券商文件验证).
- Every row gets a status: 新, 可能重复, 已导入, 与已入账记录不同, 需处理, 跳过, 不支持,
  已合并. A row is bookable only when the file establishes every fact the
  booking needs; anything missing, ambiguous or contradictory is 需处理 (needs
  review) with the reason, never a default. 北交所 and B shares are 不支持
  (unsupported).
- 逆回购 pairs become one `interest` event, 港股通 trades settled in CNY become an
  `fx` plus the HKD trade, 送股 / 转增 become a `split`. Fees come from the fee
  columns and are checked against the cash amount; a fee that cannot be read is
  never 0.
- A 港股通 trade moves exactly the CNY settlement; a fee stated in HKD is converted
  at the settlement rate (or the rate the CNY amount implies), never taken as CNY.
- A dividend whose gross amount less the tax withheld equals the amount posted
  is booked at the posted amount; one that does not add up asks for the amount
  received.
- A bare `$` in a price or amount is the listing's own dollar (USD for a US
  listing, HKD for Hong Kong); with nothing to tell which, the row needs review.
- Rows of one day without times are put in an order the account can afford:
  the selected account's own 资金余额 chain decides it, and without balances only
  the account's trades move, a sale before the buy it pays for; every other row
  (deposits, dividends, fund and template rows) keeps its place in the file. A
  day with a split or another row that changes the share count of a stock traded
  that day keeps the file's order. A newest-first export is recognised the same way.
- Numbers stored as numbers in a workbook are read exactly, whatever decimal
  comma convention the text cells use.
- Re-importing the same file adds nothing; booked events and the share lines of
  the reconciliation are compared by listing, whatever the spelling. An
  overlapping export without 成交编号 matches an already booked fill with its own
  price first, so only the new fill is booked; two fills with different 成交编号
  are two fills, the numbers compared without leading zeros (a workbook stores
  the text `000123` as the number 123). 资金账号 / 股东代码 columns are checked,
  so rows of another broker account are not booked into this one; the choice is
  remembered per ledger account. The page says 全部已导入 only when nothing but
  imported, skipped or merged rows is left.
- Where the file has balance columns, the result is reconciled against them
  (一致 or the difference and its likely causes). A balance the ledger meets while
  rows that move it are still 需处理, 不支持 or unchecked is 未核对 (not checked),
  with the number of rows left out, never 一致. A holdings statement opens a new
  account or shows a quantity reconciliation for an existing one; it never
  proposes corrective trades. The 累计净投入 it suggests for a new account is
  cash + positions at cost + fund value, labelled
  按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金.
- Large files are confirmed in batches of up to 1,000 events. The uploaded file is
  not stored; the parsed session is deleted after 30 days. The project's own
  16-column CSV template (下载模板) is imported here too. See
  [docs/IMPORT.md](docs/IMPORT.md).

**Setup wizard (`/setup`)**

- Opens once on a fresh install (Today's empty state links to it; 设置 › 关于与帮助
  reopens it). Steps: 欢迎, 账户与期初, 费率, 行情与提醒, AI 助手, 完成. The opening
  balance can start from zero, from a broker file, or from the v3.2 files
  (迁移旧版持仓); every step can be skipped.
- Legacy migration reads `holdings/<date>.json` in the portfolio directory and
  the old manual portfolio `data/portfolio.json`, reads their numbers as exact
  decimals, lists every value it could not read and asks for it (an undated file
  shows the date it asks for), and creates an ordinary ledger proposal that you
  review and confirm. The legacy files are never changed. Every legacy source and
  account not yet in the ledger stays on offer, also after one account was opened;
  a source migrated in full is not offered again, and `data/portfolio.json` is
  refused a second migration under any account name, unless the earlier
  migration was reversed (冲销); then it can be migrated once more.
- An opening needs each account's 累计净投入: the wizard, 记一笔, the ledger page's
  legacy preview and the assistant ask for it instead of booking 0. The suggestion
  the wizard offers (only when every amount is in CNY) is cash + positions at cost
  + fund value, labelled 按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金.
- The AI step saves the chat model (DeepSeek, OpenAI, Anthropic or an
  OpenAI-compatible endpoint) and tests the connection. The key is write-only:
  no page or API returns it. A saved key belongs to the address (scheme, host,
  port) and provider it was saved for; saving another address or provider drops
  it, and a remote address then needs a key typed for it. A key in
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` lets its official address be saved
  without typing one (also through the 兼容接口 preset), and a loopback address
  needs none; the form asks for a key exactly where the API would refuse the
  save. 测试连接 tests only what is saved. An `Authorization` header template
  without a key carries the group's key or is left out, so a local model without
  a key is reachable.

**Watchlist, search and the stock drawer (行情 › 自选, `/market`)**

- A persistent watchlist (up to 200 items) with live quotes, notes and order,
  and an index strip. Search by code or name over your holdings, the watchlist,
  remembered choices and an A-share and ETF name list; a bare `000001` is
  平安银行 `SHE:000001`, never the 上证指数.
- Clicking a holding or watch item opens a drawer with a daily chart, your
  position, its alerts and plans, and shortcuts to 设提醒, 加入自选 and 去回测.
  Prices, changes and closes there, in the watchlist, the index strip and the
  alert rules keep every digit the source gave.

**Alerts (行情 › 提醒, `/alerts`)**

- Rules: 止损 (stop-loss price), 目标价 (target price), 涨跌幅 (daily move, one
  direction per rule) and 仓位上限 (position weight of the whole portfolio).
  Alerts only notify; nothing places or prepares an order.
- A background monitor inside the backend checks the rules during continuous
  trading only, every 5 seconds by default, with all quotes in one request.
  It skips stale, untimed and unavailable quotes (the rule shows 暂停检查 with the
  reason), fires once per crossing and re-arms after a 0.5% recovery or on the
  next trading day, at most 3 times per rule per day and 20 notifications per 10
  minutes (5 kept for stop-losses; the rest go into a digest). Three failed
  cycles in a row publish one 行情中断，提醒暂停 notice.
- One monitor at a time: a second backend on the same data stands by (待命).
  The monitor runs only while the backend runs; in the read-only demo it does not
  run and every rule says 演示模式不检查提醒. See
  [docs/ALERTS_AND_ROUTINES.md](docs/ALERTS_AND_ROUTINES.md).
- The rules page says 已达到 (reached) only when the monitor's own comparison on
  the exact values holds; a distance that would round to zero keeps two
  significant digits. A rule on a code no source can quote (a stock code in an
  index range, such as `SHA:000001`) is refused when created. When today's fires
  cannot be refreshed, their counts read 不可用 with the reason.

**Trade plans (计划与复盘 › 计划, `/plans`)**

- Write down ticker, thesis, invalidation, entry range, stop, target, planned
  weight or quantity and a review date before buying. Draft → 启用中 → 已关闭 or
  已取消. Activating a plan creates its stop, target and weight alerts; closing
  it disables them. The maximum loss to the stop is computed for you.
- An active plan that keeps its entry can move its stop up to breakeven or into
  profit; its maximum loss is then 0 and the plan says how much gain the stop
  locks in. A plan's activation and end count in its own market's trading day (a
  US plan in New York's), and a New York close that has not settled is amber with
  its reason on the plan, as on Today.
- 记一笔 and import previews show a plan check for every buy and sell: no plan,
  outside the entry range, at or below the stop, above the planned quantity or
  weight, sold below the stop or before the target. It warns and never blocks:
  the ledger records what happened.

**Pre- and post-market briefs (计划与复盘 › 今日简报, `/review`)**

- 盘前简报 at 08:45 and 盘后简报 at 15:45 (Asia/Shanghai) on A-share trading days.
  Pre-market: overnight US, Hong Kong and A-share indices, distance of every
  holding and watch item to its stops and targets, plans and notes due for
  review, announcements and news of A-share holdings, recent alerts, pending
  proposals. Post-market: the day's trades and their plan check, day profit per
  position, alerts fired, holdings beyond a stop or target, lesson proposals.
- Every number is computed by code from the ledger and labelled quotes, with its
  source and time. Optional model commentary (`TRACKER_BRIEF_COMMENTARY=1`) is
  shown apart and never supplies a fact. Times and switches are environment
  variables (`TRACKER_PREMARKET_TIME`, `TRACKER_POSTMARKET_TIME`, ...).
- Quantities, prices and fees in the briefs are the ledger's own values. A stop
  and a plan at the same level count as one breach. A count of holdings beyond a
  stop or target that rests on a stale price or a New York close not yet final
  is marked as an estimate and names the holdings it could not check. The
  pending count leaves out manual previews that had expired by the brief's time.

**Automatic close snapshot**

- While the backend runs it makes the daily snapshot, `history.csv` row and
  Markdown report at 15:40 Asia/Shanghai on A-share trading days by running
  `engine/scripts/portfolio_daily_update.py --action auto-pipeline`; no cron or
  separate scheduler is needed. It books closing prices only: a price of a
  session still trading or whose close is not final yet (Hong Kong before 16:10,
  New York before 16:30 New York time) is not booked. When those are the only
  prices missing, the run waits (等待中, for example 等待港股收盘价确定，16:10 后记入):
  no failure, no notification, no retry used up, and the snapshot is made at the
  first scheduler tick after that close is final. If the source still shows a
  price from before Hong Kong's closing print then, the run looks again on the
  clock's five-minute marks (16:15, 16:20, 16:25; 等待港股收盘价确定，最晚 16:28
  记入) and books the close as soon as it is verified; if none is verified by
  16:28 it books the day's last price marked 含估算. A manual run during a wait looks at once and waits on
  when the close still cannot be verified, again without using a retry or
  notifying. The snapshot's exchange rates come from the source Today uses
  (Yahoo, then Eastmoney's offshore rate when Yahoo's is stale or has no time;
  only the one `TRACKER_QUOTE_SOURCE` selects when it names `yahoo` or
  `eastmoney`). Eastmoney is reached without the environment's proxy unless
  `TRACKER_EASTMONEY_PROXY=1` and, after Yahoo, is asked only for the
  currencies the snapshot converts; the engine's `config.json` takes an optional
  `eastmoney_url` for that endpoint.
  Prices are judged by Today's rules and money is computed from the ledger's own
  digits. A snapshot that rests on an estimated input (a holding that did not
  trade in its latest session, a close of a year without a holiday table, an
  exchange rate over an hour old, holdings from the legacy files) is made and
  marked 含估算 with its reasons, and records where its holdings came from
  (`holdings_source`); such a day is not made again later. The first snapshot has
  no day change: it is 不可用, not 0.
  A failed run is retried 15 and 30 minutes later; after the last retry one
  notification is sent and 设置 › 自动化与通知 offers a manual run (立即运行). Only
  today is caught up after a restart. `TRACKER_CLOSE_SNAPSHOT=0` switches it off. The Docker backend image has no engine scripts, so there the
  routine reports 不可用.

**The assistant**

- New read tools: quotes, the watchlist, alert rules, trade plans, the stored
  briefs, confirmed trades, research notes, daily price history and technical
  indicators, stock news and announcements. Market breadth, sector boards and
  the 财联社 flash are off unless `TRACKER_AGENT_BREADTH=1` /
  `TRACKER_AGENT_CLS_FLASH=1`.
- Its view of the portfolio is the Today payload (live quotes with times),
  instead of average costs, and the conversation history it is sent is bounded.
- It can only propose: ledger entries, alert rules, trade plans, watchlist
  additions and lessons are proposals you confirm. Numbers in its tool calls are
  read as exact decimals, and a money amount or quantity that arrives as a
  floating-point number is refused instead of rounded. An opening it proposes
  needs each account's 累计净投入 from you; without it nothing is proposed.
  Lessons and profile changes it
  extracts from a conversation become proposals in 设置 › 交易偏好 instead of
  being written to memory; a lesson you rejected is not proposed again, and a
  sector you turn down (「不喜欢银行股」) is never stored or proposed as preferred.
- Numbers in an answer that appear in none of this turn's data get a footnote
  written by code. When a source of the older quote tools has no price (before
  the open, a suspended stock) the next source is asked; when none has one the
  quote is unavailable, never 0 or −100%, and keeps the first previous close a
  source gave.

**Backtesting (研究 › 回测, `/backtest`)**

- Rebuilt around one run service. 快速回测: pick a security (or 回测这只持仓 from
  Today, 持仓与成本, the stock drawer or a plan), a period (1, 3 or 5 years, or
  custom) and one of five presets: 一直持有（对照）, 均线趋势, 超跌反弹, 突破,
  止损止盈（持仓）.
- Each result compares with holding the same stock under the same capital, lots,
  fees and execution, and with the market index (沪深300, 恒生指数 or the S&P
  500); a comparison that cannot be computed is shown as unavailable, never 0%.
  The headline and caveats are generated by code from the numbers. An AI reading
  of a result is optional and never runs by default.
- Fills at the next open plus 0.05% slippage (never beyond the limit-up or
  limit-down price), A-share T+1, 100-share lots (STAR 200 then 1),
  limit-up/limit-down blocking, a 10% share of the bar's volume per fill, stops
  and targets inside the bar, unfilled buys expiring after 1 bar and sells
  retrying for 20, all listed in the result. A position still open when the data
  ends is listed apart as 期末仍持有 (valued at the last close, not sold) and is
  not counted as a completed trade (完整交易). The 数据 tab shows each cached
  instrument's source, download time and completeness per price adjustment;
  更新到今天 refreshes every adjustment the cache holds and says how far each now
  reaches. Cached bars that stop before the last settled session are downloaded
  again, at most once an hour per series (runs in between reuse that check); if
  the source still has no newer bar the result carries the `data_ends_early`
  caveat and ends at the last bar it has.
- A rule that reads no indicator, such as buy-and-hold, gets no automatic
  warm-up and no in-sample or few-trades caveat. The Hong Kong ETF stamp-duty
  caveat is a warning only for codes in the Hong Kong ETF ranges.
- Dated fee schedules per market (`dashboard/backtesting/broker/fee_schedules.json`):
  A-share stamp duty and transfer fee, Hong Kong stamp duty, trading fee and
  levies, the US SEC fee, each row citing its notice. Commission defaults are
  assumptions you can override; what is not modelled is stated in the result.
- Every saved run keeps a manifest (resolved settings, data hash, engine and fee
  schedule versions) and its daily equity; 历史 can rerun it and says whether
  the data, strategy, settings or engine changed (a custom strategy's fingerprint
  covers the local modules its file imports, also names imported from them, and
  is marked unverifiable when one cannot be resolved). 历史 lists every saved run, 100 at a time
  with 加载更多; a run saved 不复权 reopens 不复权. A run saved by v3.2.1 or earlier
  opens and exports as a read-only 旧版本记录 (legacy record): what it saved, 不可用
  for what it lacks, and no rerun.
- Deliberately not modelled: intraday data, shorting, margin, options, order
  placement of any kind, universe or screening backtests, a Hong Kong board-lot
  table (100 is assumed and stated), dividend tax by holding period and
  optimisers. A rule with parameters is tested in-sample; the result says so.
- Demo mode can backtest the demo's own tickers on labelled synthetic bars,
  never against an index.

**Settings (`/settings/<section>`)**

- Seven sections: 账户与费率, 模型 (the chat model and the research connections),
  数据与行情 (quote sources, daily-bar cache, calendar years), 自动化与通知 (alert
  monitor, daily routines, notification channels), 交易偏好 (the assistant's
  memory and its pending proposals), 备份与导出, 关于与帮助.
- 备份与导出 downloads two files. `ledger.sqlite3`: every confirmed event,
  unconfirmed ledger proposals and research notes. `desk.sqlite3`: alert rules
  and fired alerts, plans and their links to trades, the watchlist, the
  assistant's proposals (including rejected lessons), briefs and routine runs,
  import sessions and column mappings, fee profiles, notifications and the setup
  flags. Neither holds the assistant's memory, model settings and keys, research
  or backtest data, or the legacy holdings and snapshot files.
- 交易偏好 deletes a lesson only while its place still holds the lesson the page
  showed (a second tab cannot delete another one). In research-only mode
  (`make lab`) the chat model cannot be saved and the 保存 button says why.
- The app shell reads the backend's mode again every minute and when the backend
  answers after an outage, so the demo banner and the write controls follow the
  backend running now without a reload.

**Demo**

- `make demo` (`create_demo.py --ledger --refresh`) now fills the ledger, the
  watchlist, trade plans, alert rules with one fired alert, one pending proposal
  and both briefs, all fictional and dated relative to the day the demo is
  created (`--today` picks that day). A demo directory left by an older version
  (v3.2.1's has no ledger) is rebuilt only when everything in it is provably what
  the generator wrote; one with anything else in it (another confirmed event, a
  watchlist row, an extra file), or a directory that is not a generated demo, is
  refused with exit status 2 and a fixed Chinese message, and nothing in it is
  changed. A rebuild is made beside the old directory and swapped in, so an
  interrupted run leaves the old one whole. It tells you to open the home page.
  The demo stays read-only: the alert monitor and the routines do not start in it.

### Changed

- **Holdings come only from the confirmed ledger.** The v3.2 manual portfolio
  is read-only once a ledger exists: `POST /api/settings/cash` and the
  `/api/portfolio` holdings endpoints answer 409, and the CLI's position
  commands are refused. These endpoints will be removed in the next release.
  The engine's dated holdings files and `data/portfolio.json` are import sources
  for the setup wizard only.
- `/` is now 今日 (Today). Chat moved into the assistant dock. The research journal
  moved from the ledger page to 计划与复盘 › 笔记 (`/notes`); 跟踪 is now 计划与复盘 ›
  组合走势 (`/tracker`); the assistant's memory is 设置 › 交易偏好; help is 设置 ›
  关于与帮助.
- 行情 was rebuilt: the hard-coded hot list, the "操作建议" pulse and the banner are
  gone; the watchlist, alerts and the stock drawer replace them.
- The assistant's prompt is discipline-first: numbers only from context or tools
  with their time; text from news, providers and your own stored notes reaches
  the model only as fenced, untrusted data.
- Technical indicators follow the standard definitions: RSI, ATR and ADX use
  Wilder's smoothing, and ATR, ADX and MFI start only once enough bars exist.
  Backtests and the assistant's `get_indicators` give different numbers from
  v3.2.
- The backtest stores both of its databases in `TRACKER_BACKTEST_DIR` when set
  (default `dashboard/data`); the demo uses its own directory.
- The daily pipeline, the scheduler and the cron scripts run their Python
  scripts with the interpreter that started them (`python3`, or `PYTHON` in the
  shell scripts) instead of calling `conda run` on their own. Set
  `CONDA_ENV=<name>` to keep using a conda environment; `CONDA_EXE` still selects
  the conda executable.
- `auto-pipeline` works with an active ledger and without Feishu.
- Pending ledger proposals are shown in chat with a link to the exact preview,
  and a confirmed preview keeps its before/after and receipt after a reload or a
  restart.

### Fixed

- Reading never creates a file and never changes the ledger or the desk store:
  every page read and every assistant read tool leaves them as they were, and
  creates no settings file or backtest database (a test sweeps every read route
  and read tool). A read of a ledger in WAL mode leaves no `-wal` / `-shm` files
  behind.
- A held ticker without a price stops the snapshot instead of being valued at 0.
- `000001` and other bare codes are no longer priced as an index of the same
  number.
- Hong Kong and US positions are no longer added into one yuan total at their
  native prices.
- A Hong Kong quote during the closing auction is not taken as the close; daily
  bars stay provisional until each market's close is final.
- Daily and monthly percentage changes from a negative or zero starting total
  (net liabilities) are unavailable instead of shown with a flipped sign: `null`
  in snapshots and the tracker API, an empty `history.csv` field and a dash in
  reports. Missing tracker percentages show a dash instead of `+0.00%`.
- A streaming chat reply can be stopped and times out when it stalls; streamed
  text stays intact across split network reads.
- An interrupted close snapshot is completed from what it saved, stage by stage,
  instead of starting over; a timed-out pipeline stops everything it started
  before releasing the portfolio lock.
- The 2026 mainland holiday table and the scheduler's time help were corrected.
- SQLite 3.51.0 and 3.51.1 (conda's Python 3.11 shipped 3.51.1 in early 2026)
  deadlock when threads of one process open and close the same database file,
  fixed in 3.51.2; on those two releases the app now serialises opening and
  closing (`dashboard/core/sqlite_guard.py`) and the backend logs one line about
  it at start-up. Other releases are untouched; upgrading SQLite (for example
  `conda update sqlite`) is still advised.

### Security

- A crafted request could make the chat API read an image file outside the
  conversation image folder. Image paths are now validated and served only from
  that folder.
- An assistant reply containing a remote image made the browser load it at
  once, which could send conversation data to a third party. Remote images in
  replies are now shown as a link that loads nothing until clicked.
- The API answers only for `localhost`, `127.0.0.1`, `[::1]`, the Compose service
  name and the names in `TRACKER_ALLOWED_HOSTS`, so a web page cannot reach it
  through a DNS name pointed at your machine. Setup writes also refuse requests
  from a page of another site.
- Text from providers (news, announcements, quote errors) and text stored in the
  app (names, notes, plan reasoning, briefs) is fenced as untrusted data before
  it reaches the model; the model can only propose changes, which you confirm.
  Lessons extracted from a conversation no longer enter memory without your
  confirmation.
- API keys are write-only: the setup API never returns, logs or echoes a key,
  and the model test reports only fixed messages. The settings file is made
  private (mode 0600) before a key is written. Shipped defaults are checked for
  credentials in CI.
- Uploaded workbooks are measured as they are unpacked and refused beyond fixed
  size limits, so a small file cannot expand without bound. The uploaded file is
  never stored.
- Errors that reach the page or the model carry fixed texts, never a provider's
  exception message.

### Removed

- Pages: 仪表盘 (`/dashboard` → `/`), 持仓 (`/portfolio` → `/ledger`), 记忆
  (`/memory` → `/settings/preferences`), 帮助 (`/help` → `/settings/about`), and
  the single Settings page (`/settings/cash` → `/ledger`, `/settings/help` →
  `/settings/about`, `/settings/memory` → `/settings/preferences`). Old links
  redirect with their query and hash.
- The suggestions panel and the market page's "操作建议"; `/api/suggestions` is
  no longer used by any page.
- The ledger page's CSV dialog: the template is imported on 账本 › 导入.
- The in-memory `PriceMonitor`, replaced by the alert monitor.

### Upgrade notes

1. Back up before you update. Either download both backups in 设置 › 备份与导出
   (下载账本备份 gives `ledger-backup.sqlite3`, 下载桌面数据备份 gives
   `desk-backup.sqlite3`; on 3.2 use 备份 on the ledger page, and there is no
   desk store yet), or stop the backend and copy `ledger.sqlite3` and
   `desk.sqlite3` from your portfolio directory (`PORTFOLIO_DIR`, default
   `engine/portfolio`; `/app/portfolio` inside Docker, mounted from
   `engine/portfolio`). Back up the whole directory if you still use the legacy
   holdings and snapshot files. To restore: stop the backend, move the current
   `ledger.sqlite3` / `desk.sqlite3` and their `-wal` / `-shm` files aside, put the
   backups in their place under those names, and start the backend again.
2. Update, then run `make setup` (Python and Node packages changed).
3. Schemas migrate by themselves: `desk.sqlite3` is created on the first write
   and an existing one is upgraded at start-up (outside demo mode);
   `ledger.sqlite3` gains its proposal-binding table on the first write;
   `backtest_history.db` gains its new columns the first time it is opened for a
   write (reading opens a v3.2.1 file as it is, also a read-only copy), and an
   existing `backtesting.db` gains its new tables when it is opened. Reading never
   changes the ledger or the desk store.
4. Start the backend and open `/setup`. Without a ledger, choose 迁移旧版持仓 or
   导入券商文件 to create the opening balance. With a ledger, check fee profiles,
   quotes, alerts and routines there.
5. Indicators changed to their standard definitions (above), so old backtest
   numbers that used RSI, ATR, ADX or MFI differ. A run saved by v3.2 has no
   manifest: it opens and exports as a read-only 旧版本记录, with 不可用 for what it
   lacks, and cannot be rerun (这条记录是旧版本保存的，缺少复现所需的设置（运行清单），无法重跑).
6. Alert rules that repeat each other (for example `HKG:700` and `HKG:00700` at
   the same threshold) are reconciled once by the first writer after the
   upgrade: the oldest stays enabled, the others are disabled with a reason
   naming the rule they repeat, and one notification lists them. A duplicate
   disabled this way blocks no edit of the kept rule and can have its own note
   changed; enabling it again while the kept rule exists is refused.
7. An import preview still pending from before the upgrade cannot be confirmed:
   one made by the 3.2 CSV import is withdrawn (请重新导入), and one made under
   earlier import rules (`INTERPRETATION_VERSION`, now 4, in
   `dashboard/core/importers/__init__.py`) is withdrawn when you confirm it.
   Import the same file again, once (a CSV through 导入券商文件). A B-share
   proposal made before the upgrade is refused at confirmation with the same
   text as a new B-share entry; discard it.
8. If you ran `portfolio_scheduler.py` or a cron job for the daily snapshot, you
   can stop it; keeping it does not make a second snapshot of the same day. Set
   `TRACKER_CLOSE_SNAPSHOT=0` to keep only your own job.
9. If you open the app under a name other than `localhost`, set
   `TRACKER_ALLOWED_HOSTS` (see [SECURITY.md](SECURITY.md)).

### Known limitations (已知限制)

Known in this release and left as they are for now.

**Ledger and 记一笔**

- A fee booked as prefilled is marked 估算 only in the event's note
  (〔费用为估算：按账户费率估算〕); the cost and profit computed from it are not
  marked. If your broker charged another fee, reverse the event and book it with
  the real fee, or import the broker file.
- 昨收 (previous close) has no timestamp of its own; it shows the current quote's
  time.
- An opening balance sent straight to the ledger API without `contributed_cny`
  books 0; 记一笔, the setup wizard, the ledger page's legacy preview and the
  assistant ask for it.
- B shares (`SHA:900xxx`, `SHE:200xxx`) cannot be booked or imported.
- A ledger that already holds one listing under two spellings from before the
  upgrade (such as `HKG:700` and `HKG:00700`) keeps them: two rows, valued apart.
  Entries booked after the upgrade go to the spelling the account held on the
  entry's date.

**Setup and upgrade**

- The 累计净投入 suggestion (cash + positions at cost + fund value) is not marked
  as an estimate once booked, and Today's cumulative profit is computed from it.
  Enter the capital you actually paid in.
- The wizard shows the saved API address as it is (path included, query left
  out). Never put a key in the address; use the API 密钥 field.
- An import preview made before the upgrade and still unconfirmed (a 3.2 CSV
  import's included) is refused once at confirmation; import the same file again.
- A B-share proposal made before the upgrade is refused at confirmation; discard
  it.

**Backup and restore**

- Close every open page before restoring and reopen it afterwards. Restored with a
  page open, new notifications can reuse numbers from before the restore, and
  opening an old notification marks new alerts as read too. A proposal confirmed
  after the backup shows 已应用 when confirmed again after the restore but does
  not take effect; make that change in settings directly.
- The ledger and desk backups are two downloads, each a copy of its own moment;
  a trade booked between them makes them disagree. Book nothing while backing up
  and download both one after the other.

**Import**

- New-share payment and allotment, fund subscriptions and generic cash
  adjustments are booked by hand; 北交所 trades are not supported.
- The 同花顺, 东方财富 and 通达信 presets are not verified against each broker's
  real exports.
- When the ledger has a Hong Kong listing under two spellings (one account holds
  `HKG:700` and another `HKG:00700`, or one account sold out `HKG:00700` and
  bought it back as `HKG:700`), a file's code resolves to the spelling the
  ledger lists first (`HKG:00700`), so a sale imported into an account holding
  `HKG:700` can need review with 卖出 100 股，但账本当时只持有 0 股. Changing the
  code in the file or in the row editor resolves the same way; set the row to
  跳过 and book the sale with 记一笔, which books it under the spelling the
  account held that day.
- In a file without times or 资金余额, a buy listed before the 入金 (deposit) of
  the same day that pays for it needs review for lack of cash; tick 允许融资
  (allow margin) for that row, or move the deposit row above the buy and import
  again.

**Backtest**

- Compared with an index of another market over years the holiday table does not
  cover (`dashboard/core/market/holidays.json`: mainland 2026, Hong Kong and New
  York 2026–2027), the index's missing days cannot be told from holidays, so the
  whole index comparison is unavailable.
- Every A-share ETF is simulated as T+1. Gold, bond, money-market and
  cross-border ETFs (such as 518880, 511010, 513100, 159941) really trade T+0;
  in a backtest shares bought that day cannot be sold that day, and the end
  valuation says shares bought on the last day could not be sold for T+1.
- Results saved by 3.2 open and export (marked as legacy records, missing parts
  不可用) but cannot be rerun.
- Run shortly after the close (15:45) before the source has the day's bar, a
  backtest of the same symbol within the next hour does not download again and
  still ends on the previous session (`data_ends_early`); after that hour, or
  after 更新到今天 in the 数据 tab, it fetches again.

**Quotes, snapshots and briefs**

- For 30 minutes after the New York close a US price shows as the close but is
  marked 估算 (尚未最终确定); a quote read after that is the verified close.
- With a Hong Kong holding the close snapshot shows 等待中 at 15:40 and is made
  once Hong Kong's close is final (16:10). When the source has not yet shown
  the closing print, it looks again every five minutes (16:15, 16:20, 16:25),
  books the close once it is verified, and if none is by 16:28 books the day's
  last price marked 含估算. A mainland-only portfolio is made at 15:40.
- While the snapshot waits, Today's status chip names the next look
  (今日快照 16:15 后记入); the latest booking time (16:28) is only in its tooltip.
- A snapshot day marked 含估算 (a suspended holding, an unverified rate,
  holdings from the legacy files) is not made again when the data improves.
- Alerts and briefs go to the bell only; only the daily close report is pushed
  to Feishu.
- A brief's 待确认 count covers the previews made against the current ledger
  revision; with many (over 30 regular proposals or 10 manual previews) it can
  differ from the ledger page's list.
- A thinly traded holding whose source does not restamp its price after the
  close shows 已过期 (estimated) through the evening, until a quote with a newer
  time arrives. In the midday break the same holds for a price stamped more than
  20 minutes before the morning's end; a source that stopped at a trade within
  those 20 minutes (11:16) shows it through the break as 收盘价 11:16, a verified
  price, and the next source is not asked until the afternoon session.
- Before the legacy files are migrated, Today shows 还没有已确认的持仓 (no
  confirmed holdings yet) while the close snapshot is still made from those
  files, marked 含估算 (`holdings_source: legacy_files`).
- When one stock held in several accounts adds up to exactly the cap of a
  仓位上限 rule (to some twenty decimal places), the new-rule dialog's
  当前仓位已达到上限 (weight already at the cap) hint and the monitor can
  disagree; a weight above or below the cap is unaffected.

**Assistant**

- A percentage in an answer is checked only against the percentages in this
  turn's data, not against the figure it names: a 100% weight keeps 「涨了 100%」
  from being footnoted. Amounts and share counts are likewise checked only by
  their unit class (money, quantity). Rely on the figures the pages show.
- Number checks and preference extraction go by sentence pattern: a made-up 0
  in 「现价为人民币0.00元」 or 「当前价约为0.00元」 can go unfootnoted;
  「银行股我不会买入，我只喜欢科技股」 can propose both sectors and
  「我不想追高所以更喜欢银行股」 neither. Extracted preferences are proposals you
  confirm in 设置 › 交易偏好.

**Pages**

- 组合走势 is still at `/tracker`; there is no lessons page: lessons are in
  设置 › 交易偏好.
- 港股通 is supported in import only, not in 记一笔.
- Positions recognised from a screenshot are shown in the chat only and never
  booked; a cost price is taken only when the image states it, otherwise it is
  empty.


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
