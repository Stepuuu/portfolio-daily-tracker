# Alerts and daily routines / 提醒与每日例行任务

Price alerts (行情 › 提醒, `/alerts`) and the three daily routines (the close
snapshot and the pre- and post-market briefs) run **inside the backend process**.
There is nothing extra to start: `make start`, `make backend` or the Docker
`backend` service runs them. They do not run while the backend is stopped, and
nothing is made up afterwards except today's routines. 中文的逐步操作见
[完整使用指南](FULL_GUIDE_CN.md#价格提醒)。

An alert only notifies. Nothing in this project places, prepares or sends an
order, and an alert is not a stop order at your broker.

## Alert rules

| Kind (UI label) | Threshold | Fires when | Re-arms when |
|---|---|---|---|
| `stop_loss` (止损) | a price | last price ≤ threshold | price ≥ threshold × 1.005, or the next trading day |
| `target_price` (目标价) | a price | last price ≥ threshold | price ≤ threshold × 0.995, or the next trading day |
| `day_change_pct` (涨跌幅) | a signed percentage: `-5` = down 5% or more, `5` = up 5% or more | the day's change reaches it | 0.5 percentage points back, or the next trading day |
| `position_weight_max` (仓位上限) | % of the whole portfolio in CNY (cash and funds included) | the position's weight ≥ threshold | 0.5 percentage points below, or the next trading day |

- One rule watches one direction; a move in both directions takes two rules.
- An `account` narrows a weight rule to that account's position; for the other
  kinds it is a label.
- A rule fires at most 3 times per trading day and not again within its cooldown
  (300 seconds by default). A new rule starts armed, so a condition that already
  holds fires once at the first check in session.
- Rules are compared by listing: `HKG:700` and `HKG:00700` are the same rule.
  A duplicate the upgrade disabled (see the upgrade notes in the CHANGELOG)
  blocks nothing: the rule it repeats can still be disabled or edited, and its
  own note can be changed; enabling it again while the kept rule exists is
  refused.
- A new rule on a code no source can quote (a stock code in an index range, such
  as `SHA:000001`) is refused with the instrument's reason; a rule stored before
  stays editable.
- The rules table shows each rule's distance to firing. 已达到 (reached) comes from
  the monitor's own comparison on the exact values, never from a rounded
  distance, and a distance that would round to zero keeps two significant
  digits; 已达到 · 未检查 means the condition holds but the monitor is skipping the
  rule, so it will not fire. When today's fires cannot be refreshed, their counts
  read 不可用 with the reason.
- Known limitation: when one stock held in several accounts adds up to exactly
  the cap of a `position_weight_max` rule (to some twenty decimal places), the
  new-rule dialog's 当前仓位已达到上限 (weight already at the cap) hint and the
  monitor can disagree; a weight
  above or below the cap is unaffected.
- At most 500 rules. Rules you create are saved directly; the assistant can only
  propose one, which you confirm. A trade plan creates its own rules when it is
  activated (shown as 计划 #n, read-only on this page) and disables them when it
  ends.

## The monitor

Every cycle (5 seconds by default) the monitor:

1. reads the enabled rules and looks only at markets in **continuous trading**
   by the calendar. Nights, weekends, holidays, the lunch break and the call
   auctions fetch nothing (the rule shows 休市，不检查);
2. fetches every quote it needs in one request (plus exchange rates and the
   confirmed ledger, read-only, when a weight rule needs a valuation);
3. decides each rule, the most important first (stop-loss, weight, target,
   daily move), and stores the new state and the notification together, so a
   restart never fires twice.

What happens to one rule, in plain words (the first line that applies decides):

| Situation | Result |
|---|---|
| Another backend on the same data holds the monitor lease | this one stands by (待命 · 另一个后端进程在检查) and checks nothing |
| The rule's market is not in continuous trading | not checked |
| The quote request failed, or no rule had a usable quote | a failed cycle; three in a row publish one 行情中断，提醒暂停 notice, and the first good cycle 提醒已恢复 |
| The rule was deleted or disabled meanwhile | nothing |
| The quote cannot be used: none, 不可用, 已过期, no exchange time, or (for a price rule) no trade yet today | skipped: the rule shows 暂停检查 with the reason and since when |
| A weight rule whose valuation is unavailable or only estimated (a stale price or rate anywhere in the portfolio) | skipped (暂停检查); a weight rule held back for 30 minutes is announced once a day |
| The condition is not met, already fired, inside the re-arm band, at the daily cap or in the cooldown | nothing |
| The data behind the change is older than the data behind the rule's last change | held back until newer data arrives |
| Otherwise | fires (a notification), re-arms, or nothing |

Delayed quotes (1 to 20 minutes old) are used; the Yahoo fallback delivers Hong
Kong prices about 15 minutes late (the Tencent fallback before it: not measured
during Hong Kong trading). If the holiday table calls a weekday a
holiday but quotes keep advancing, the monitor treats the session as open for
the rest of the day and says so.

**Notification caps.** At most 20 alert notifications per 10 minutes, of which 5
are kept for stop-losses. Fires beyond the cap are recorded as suppressed and
summarised in one digest per window, stop-losses first.

**One monitor at a time.** A lease row in `desk.sqlite3` names the monitor that
checks. A second backend on the same portfolio directory (or an overlapping
restart) stands by and takes over at most one lease length after the first one
stops renewing.

**Status.** The 提醒 page and 设置 › 自动化与通知 show the monitor line: 运行中,
运行中 · 等待开盘, 运行中 · 没有启用的规则, 运行中 · 部分规则无法检查, 行情不稳定,
行情中断，已暂停, 检查出错, 待命 · 另一个后端进程在检查, 未运行, 已停止, 没有响应,
已关闭 (switched off) or 演示模式不检查提醒. The same is `GET /api/alerts/status`.

The monitor does not start in demo mode (`TRACKER_DEMO_MODE=1`), in
research-only mode (`TRACKER_LAB_ONLY=1`) or with `TRACKER_ALERTS_ENABLED=0`.

## Daily routines

All three run on A-share trading days by the mainland calendar, Asia/Shanghai
time:

| Routine | Due | What it does |
|---|---|---|
| `premarket` 盘前简报 | 08:45 | overnight indices, distance to stops and targets, plans and notes due for review, news and announcements of A-share holdings, recent alerts, pending proposals |
| `close_snapshot` 收盘快照 | 15:40 | runs `engine/scripts/portfolio_daily_update.py --action auto-pipeline` for the day: snapshot, `history.csv` row, Markdown report, and the Feishu push when the portfolio's `config.json` names a `feishu_chat_id` |
| `postmarket` 盘后简报 | 15:45 | the day's trades and their plan check, day profit per position, alerts fired, holdings beyond a stop or target, lesson proposals |

- One run per routine and trading day. The scheduler wakes about once a minute;
  when the backend starts after the due time it runs **today's** routine; a day
  the backend was not running is never run later.
- A failed run is retried 15 minutes and then 30 minutes later. After the last
  retry one notification is sent. A failed close snapshot is retried until 09:00
  the next morning at the latest.
- A manual run: 生成 / 重新生成 on 计划与复盘 › 今日简报, 立即运行 in
  设置 › 自动化与通知, or `POST /api/routines/{kind}/run`. Status:
  `GET /api/routines/status`; history: `GET /api/routines/runs`.
- A brief is stored even when part of it failed, so the page shows what is
  missing; the next attempt replaces it. Every number in it comes from the
  ledger, the desk store and labelled quotes, quantities, prices and fees with
  the ledger's own digits. The optional model commentary is shown apart and
  never supplies a fact.
- Stops and targets: a rule and a plan at the same level of one holding are one
  level and one breach, naming every plan and rule behind it. A post-market count
  of holdings beyond a stop or target that rests on a stale price or a New York
  close not yet final (16:30 New York time) is an estimate and names the
  holdings it could not check.
- Pending (待确认): every proposal and every ledger preview made against the
  current ledger revision that can still be confirmed is counted; a manual
  preview that had expired by the brief's time is not. The ledger page lists at
  most 30 regular proposals and 10 manual previews, so above that the two
  numbers differ.

The close snapshot reports:

- 已跳过 (`skipped`): demo mode, scripted quotes (`TRACKER_QUOTE_SOURCE=fake`),
  switched off (`TRACKER_CLOSE_SNAPSHOT=0`), or no ledger and no holdings file yet;
- 不可用 (`unavailable`): the engine scripts are not installed (the Docker backend
  image), the portfolio directory has no `config.json`, or no holdings were in
  force on that day;
- 等待中 (`pending`, `error_category` `waiting`): the only prices missing are
  closes that are not final yet, by the calendar's one rule
  (`SessionClock.close_settled`). With a Hong Kong holding that is every
  trading day at 15:40: Hong Kong's close is final at 16:10, after its closing
  auction. This is not a failure: no notification, no retry used up. The reason
  says when, for example 等待港股收盘价确定，16:10 后记入; `next_attempt_at` is
  that moment and the snapshot is made at the first scheduler tick at or after
  it (within about a minute). A manual run during a wait runs at once on the
  same row: it completes if the closes can be verified and otherwise waits on,
  still without a retry used up or a notification. A New York holding at 15:40 uses the previous
  night's close, which is final, and does not wait; only a retry that runs after
  New York opens waits for that session's close (16:30 New York time, 13:30 on
  an early-close day). Hong Kong half days (final at 12:10) and Hong Kong
  holidays do not wait. An A-share-only portfolio is made at 15:40. A wait that
  could only end after the 09:00 deadline is a failure (`close_not_final`).
  The engine prices each holding from Yahoo, then, in one request, from Tencent
  for the holdings Yahoo did not price (Today's reading of Tencent,
  `core/market/tencent.py`; reached directly unless `TRACKER_TENCENT_PROXY=1`,
  `tencent_url` in its `config.json` replaces the endpoint; `TRACKER_QUOTE_SOURCE`
  `tencent` or `yahoo` uses that one alone, `eastmoney` Yahoo alone, as the
  snapshot has no reading of Eastmoney's quotes). A holding waiting for its
  market's close to become final is not asked of Tencent: every source waits.
  A Tencent row is booked only as a closing row: stamped on its session's own
  date, at or after that session's closing print (the mainland 15:00, Hong
  Kong 16:08, New York the bell) and, in New York, within a minute of the bell
  (Tencent does not say whether a later price is an after-hours trade). A row
  stamped earlier is never booked, however late the run; an earlier session's
  close is carried forward only from a row Tencent marks suspended; and a wait
  for Yahoo's closing print ends only with Tencent's verified close. Tencent is
  asked only while the run has time for it (at most 6 s, and 30 s left for the
  exchange rates within the pipeline's 120 s), so a slow Yahoo leaves the run
  to end or ask for its retry as before. Each position records the source of
  its price (`price_source`: `yahoo` or `tencent`), and the daily report's
  footer names the sources the day used (数据来源: Yahoo Finance、腾讯; Yahoo
  Finance alone, as before, when every price came from Yahoo).
  Each price is judged by Today's decision table (`core/market/quotes.py`), so
  a price Today calls stale is not taken for the close: Hong Kong still
  stamped 15:55 at 16:10 is before the closing auction's print and waits for
  it: the print reaches the source minutes after it is made (Yahoo's usual
  delay is 15 minutes, so the 16:08 print arrives about 16:23), so the run
  looks again on the clock's five-minute marks (16:15, 16:20, 16:25;
  `next_attempt_at`, the engine's `CHECK_AT`) with the reason
  等待港股收盘价确定，最晚 16:28 记入 (Tencent is asked on each look and its closing
  row is booked if it already has one), and books the close, unmarked, as soon as it
  is verified, at the latest by the print plus the sources' usual delay
  (16:28, the mainland 15:20; the run detail's `book_by`). Meanwhile Today's
  status chip names the next look (今日快照 16:15 后记入); the latest time is only
  in its tooltip, the run's summary. If no
  later time has come by then (no trade in the closing auction, or a source
  that does not restamp its close), that last price of the day is booked as an
  estimate (below) and the run ends ok; so is a New York regular-session price
  stamped more than a minute before the bell (a stalled 14:42). Money is
  computed from the ledger's own figures, every digit, with Today's precision;
- 已完成 (`ok`) with 含估算 in the summary: the snapshot was made, but a figure
  rests on an estimated input, the same inputs Today marks 估算: a holding that
  did not trade in its market's latest session (suspended, or the source has
  no newer price; the two cannot be told apart) booked at its last close, a
  holding that traded but has no time after the closing print booked at the
  day's last price (收盘竞价后没有新的成交时间，按当日最后成交价记入), a close of a year the holiday table
  does not cover, an exchange rate over an hour old or without a time (the
  rate is the one Today takes: Yahoo's first, Eastmoney's offshore rate when
  Yahoo's is stale or has no time, and the latest of them, marked, only when
  neither is verified; only the source `TRACKER_QUOTE_SOURCE` selects when it
  names `yahoo` or `eastmoney`, both when it names `tencent`, which has no
  rates; `eastmoney_url` in the engine's `config.json` replaces Eastmoney's
  endpoint), or
  holdings from the legacy files not yet confirmed into the ledger
  (持仓来自旧版持仓文件，尚未迁移到账本; Today then shows 还没有已确认的持仓 (no
  confirmed holdings yet) with no totals, while the snapshot is still made from those files). The summary names the first reason; the
  snapshot keeps all of them (`summary.estimated`, `summary.estimated_reasons`,
  a position's `estimated_reason`, and `holdings_source`). The post-market
  brief shows this summary on its 收盘快照 line, the daily report opens with
  the reasons, and the tracker (`/tracker`) marks the day on its chart, in its
  daily table and on the day's snapshot. The figures are still used for the
  history, drawdown and month figures; check them before relying on that day.
  Such a day is not made again when the data improves later (a resumed stock,
  a current rate, holdings migrated into the ledger): with the day's snapshot
  saved, a run only completes a missing history row or report. A suspension and
  a source that has not updated cannot be told apart; both are booked at the
  last close and marked;
- 失败 (`failed`) with the reason, for example a held security without a price:
  the snapshot is then not saved, never valued at 0.

The first snapshot, with no earlier snapshot or `history.csv` row to compare
with, has no day change: its daily, capital and market changes are `null`
(empty in `history.csv`, 不可用 in the report, a dash on the tracker), not 0.

## Environment variables

Set them in the process environment or in `.env` at the repository root (read
when the backend starts), then restart the backend.

| Variable | Default | Meaning |
|---|---|---|
| `TRACKER_ALERTS_ENABLED` | on | `0` keeps the alert monitor from starting |
| `TRACKER_ALERT_INTERVAL_SECONDS` | `5` | seconds between monitor cycles, 1 to 300 |
| `TRACKER_PREMARKET` | on | `0` switches the pre-market brief off |
| `TRACKER_PREMARKET_TIME` | `08:45` | due time, `HH:MM` Asia/Shanghai |
| `TRACKER_POSTMARKET` | on | `0` switches the post-market brief off |
| `TRACKER_POSTMARKET_TIME` | `15:45` | due time, `HH:MM` Asia/Shanghai |
| `TRACKER_BRIEF_COMMENTARY` | off | `1` adds the chat model's commentary to briefs (needs a configured model) |
| `TRACKER_CLOSE_SNAPSHOT` | on | `0` switches the automatic close snapshot off (its time, 15:40, is fixed) |
| `TRACKER_QUOTE_SOURCE` | Eastmoney, then Tencent, then Yahoo | `eastmoney`, `tencent` or `yahoo` uses only that source for quotes (rates: see the close snapshot above); `fake` reads scripted quotes from `PORTFOLIO_DIR/fake_quotes.json` |
| `TRACKER_TENCENT_PROXY` | off | `1` reaches Tencent through the proxy in the environment (Today and the close snapshot) |

## Running it

**Locally.** `make start` starts the backend and the web app; the monitor and the
scheduler start with the backend. Keep the backend running during trading hours
if you rely on alerts. `make backend` starts the backend alone.

**Docker.** `docker compose up -d` runs the backend container, which runs the
monitor and the briefs. Two differences:

- the backend image contains only `dashboard/`, so the close snapshot reports
  不可用. Run the pipeline on the host instead, for example from cron after 15:40:
  `cd engine && python3 scripts/portfolio_daily_update.py --action auto-pipeline`
  (it uses the same `engine/portfolio` directory the container mounts);
- `docker-compose.yml` passes only `TRACKER_ALLOWED_HOSTS` and the two API keys
  into the container. To set any variable above, add it to the `environment`
  list of the `backend` service and recreate the container.

**Several processes.** Two backends on the same portfolio directory share the
desk store: one alert monitor checks at a time, and each routine runs once per
day whichever backend claims it first.

## Notifications

Every alert, routine result and setup event is a row in `desk.sqlite3`, shown by
the bell (站内通知) in the header. No external push channel is registered for
alerts in this release; the close snapshot's daily report goes to Feishu only
through the existing report pipeline (`feishu_chat_id` in the portfolio's
`config.json`, see [FEISHU_WORKBENCH.md](FEISHU_WORKBENCH.md)). An extension can
register a delivery channel and be told each time a day's report is finished
([EXTENSIONS.md](EXTENSIONS.md)).
