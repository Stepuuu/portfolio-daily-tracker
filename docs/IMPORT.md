# Broker file import / 券商文件导入

账本 › 导入 (`/ledger/import`) turns a broker export into ledger proposals. Nothing
is booked until you check the preview and press 确认入账 (confirm). The rules below
are those of `dashboard/core/importers/`; the request and response shapes are in
`dashboard/backend/api/imports.py`. 中文的逐步操作见
[完整使用指南](FULL_GUIDE_CN.md#导入券商文件)。

## What it reads

| | |
|---|---|
| Statements | 交割单 / 对账单 (trades and cash rows), 持仓 (a holdings statement), and the project's own CSV template (本系统模板) |
| File types | CSV; tab, comma, semicolon, pipe or space separated TXT; XLS; XLSX; a web page saved as `.xls`. The type comes from the first bytes, not the file name: many broker ".xls" files are GBK text |
| Encodings | UTF-8 (with or without BOM), GBK / GB18030, UTF-16 with BOM |
| Limits | 5 MB, 20,000 data rows, 100 columns, 500 characters per cell. An `.xlsx` is also measured while it is unpacked and refused beyond fixed limits |
| Sheets | Hidden sheets are ignored. When more than one visible sheet holds data, the page asks which one to import |

The header row is the row among the first 60 with the most known column names;
title lines above it (for example 交割单 2026-11-01 至 2026-11-14) are kept and a
row dated outside the period they state needs review.

### Presets

A preset only names the format the header looks like (识别为 …). The columns are
read by the same synonym mapper whatever the preset.

| Preset | Statement |
|---|---|
| 同花顺交割单 | transactions |
| 东方财富交割单 | transactions |
| 通达信交割单 | transactions |
| 资金对账单 | transactions |
| 同花顺持仓 | holdings |
| 通达信持仓 | holdings |
| 本系统模板 | template |
| 通用表格 | anything else, mapped by column names |

**The presets are not verified against real exports of every broker.** They
were written from documented column names, and the page says
预设未经你的券商文件验证. If your broker's file is read wrongly, correct the
column mapping in step 2; the corrected mapping is saved for that ledger account
and header layout (units in headers such as `(元)` or `(手)` are part of the
layout) and applied automatically next time.

## The four steps

1. **选择文件**: choose the ledger account (or a new account name) and the file.
   The statement kind is detected; you can change it.
2. **核对列**: check which column feeds which field (date, code, action, quantity,
   price, amount, cash change, fee columns ...). Two columns that could feed the
   same field and disagree (two 成交价格 columns, for example) must be settled here.
   If the file has 资金账号 / 股东代码 columns with more than one value, choose which
   belong to this ledger account.
3. **预览与去重**: every row with its status, the counts per status, the rows that
   will not be booked and why, and the reconciliation (对账). Fix 需处理 rows with
   the row editor, or leave them out.
4. **确认**: 生成入账预览 creates one ordinary ledger proposal; review its
   before/after and press 确认入账. A file with more than 1,000 events is confirmed
   in batches (第 1/3 批 …); the next batch is prepared after the previous one is
   confirmed.

## Row statuses

| Status | Meaning | Booked? |
|---|---|---|
| 新 | bookable, not in the ledger yet | yes, checked by default |
| 可能重复 | the ledger already has an entry of the same account, date, security, kind and quantity (or the same currency and cash amount) under another id, for example typed by hand or imported from an overlapping export without 成交编号 | only if you check it |
| 已导入 | the same row is already booked | no |
| 与已入账记录不同 | the same id is booked with different values; the differing fields are listed | no |
| 需处理 | something the booking needs is missing, ambiguous or contradictory; the row says what (`needs`) | not until fixed |
| 跳过 | 撤单, 废单, 申购配号, 指定交易, 转托管, 冻结 and rows you set to skip | no, listed |
| 不支持 | 北交所 and B shares (the ledger books mainland listings in CNY only) | no, listed |
| 已合并 | the lending leg of a 逆回购, folded into its 购回 row | via the other row |

A row is bookable only when the file itself establishes the security, the
direction, the quantity and its unit, the price, the currency of every amount,
the fee, the account it belongs to, and that it is not booked yet. Nothing
defaults to CNY, to the selected account, to a buy, or to the first of two
columns. Typical 需处理 cases: a correction row (冲正, 红冲, 冲销, 撤销), 新股 /
配股 / ETF 申赎 / 场外基金 / 融资融券 rows, a fee that cannot be read or derived, a
short numeric code that could be Hong Kong or an A-share code whose leading zeros
were lost, a US symbol listed on both NASDAQ and NYSE, an unsupported currency.

How rows become events:

- 证券买入 / 证券卖出 (also 担保品 and 港股通) → `buy` / `sell`. A 港股通 trade settled
  in CNY becomes an `fx` (CNY → HKD) plus the HKD trade and moves exactly the CNY
  settlement (发生金额). A fee the file states in HKD is converted at the
  settlement rate (from the file, or implied by the CNY amount), never taken as
  CNY; a fee that does not fit the settlement needs review.
- 红利入账 → `dividend`; 股息红利差异扣税 → `fee`; 银行转证券 / 证券转银行 → `deposit` /
  `withdrawal`; 利息归本 / 结息 → `interest`. A dividend or interest row that
  states the gross amount (成交金额), the tax or fee withheld and the amount posted
  (发生金额), where gross − withheld = posted, is booked at the posted amount; when
  they do not add up, the row asks for the amount actually received.
- 逆回购 (204xxx / 1318xx): the two legs become one `interest` event with the net
  cash, only when the pairing is unambiguous.
- 送股 / 转增 → `split`, with the ratio computed from the shares held at that date.
- Fees are the sum of the fee columns, checked against |发生金额| − 成交金额 when
  both exist, or derived from them. An imported trade moves exactly the cash the
  file states (see [LEDGER.md](LEDGER.md#broker-file-import)).
- Currency: a bare `$` on a price or trade amount is the listing's own dollar
  (`$190` of a NASDAQ listing is USD, of a Hong Kong listing HKD); a `$` that
  nothing else explains needs review.
- Order within a day: the file's order is made chronological. Rows without times
  keep their order when the selected account's own 资金余额 column confirms it
  (another 资金账号's balances do not count); without balances, only the account's
  trades move, a sale before the buy it pays for, so a day the account can afford
  is never refused for the order of its trades. Every other row (deposits,
  dividends, fund rows, template rows) keeps its place in the file. A day with a
  split or another row that changes the share count of a stock traded that day
  keeps the file's order. A
  newest-first export of a single day is recognised by its 资金余额 chain.
- Workbook cells stored as numbers are read as their exact values, whatever
  decimal-comma convention the text cells of the file use.
- An overlapping export without 成交编号: a booked fill is matched to the row with
  its own price, fee and amount first, so two same-size fills of a day pair
  correctly and only the new fill is booked. Two fills with different 成交编号 are
  two fills, never 可能重复 of each other; the numbers are compared without
  leading zeros (a workbook stores the text `000123` as the number 123).
- Booked events, duplicate candidates and the reconciliation's share lines are
  compared by listing, not spelling: a fill booked as `HKG:700` is the file's
  `00700`, and a share line counts the listing under every spelling the account
  holds.
- Known limitation: when the ledger has a Hong Kong listing under two spellings
  (one account holds `HKG:700` and another `HKG:00700`, or one account sold out
  `HKG:00700` and bought it back as `HKG:700`), the file's code resolves to the
  spelling the ledger lists first (`HKG:00700`), so a sale imported into an
  account holding `HKG:700` can need review with 卖出 100 股，但账本当时只持有
  0 股。… Changing the code in the file or in the row editor resolves the same
  way. Set the row to 跳过 and book the sale with 记一笔, which books it under the
  spelling the account held that day.
- Known limitation: in a file without times or 资金余额, a buy listed before the
  入金 (deposit) of the same day that pays for it needs review with 现金不足：…
  (the deposit is booked as usual). Tick 允许融资 (allow margin) for that row (the buy is booked
  with the margin flag, cash below zero until the deposit), or move the deposit
  row above the buy and import again.

Not handled by the importer: new-share payment and allotment (新股入帐, 中签缴款),
配股, ETF creations and redemptions, 场外基金 subscriptions, 融资融券 and generic cash
adjustments are 需处理 or 跳过 and are booked with 记一笔; 北交所 trades and B shares
are 不支持.

Re-importing the same file adds nothing: every row gets a stable id (from
成交编号 or 流水号 when present, otherwise a hash of the row), and
`source = import:<account>`.

## Account identities (资金账号 / 股东代码)

One ledger account stands for one broker account. When a file holds rows of more
than one 资金账号, or two 股东代码 of the same market, it is *mixed* and you must
choose which values belong to the ledger account before a preview can be made.
Rows of other values, or rows without any value (unless you accept them), are
需处理. The values confirmed into a ledger account and your explicit choices are
remembered: a later file with another or an excluded identity needs your choice
even when it holds only one. One Shanghai and one Shenzhen 股东代码 are one
account.

## Reconciliation (对账)

- **Trades and cash rows**: when the file has 资金余额 / 股份余额 columns, the
  ledger as of the file's last date plus the included rows is compared with the
  file's last balances of this account: 一致, or the difference with likely causes
  (for example 需处理 rows that are not booked). A balance the ledger meets while
  rows that move that cash or those shares are still 需处理, 不支持 or unchecked is
  未核对 (not checked) with the number of rows left out, never 一致. A difference
  must be acknowledged (我已核对这些差异，仍要生成入账预览) before the preview is
  generated.
- **Holdings statement, new account**: it becomes one opening balance. The opening
  date, cash per currency and 累计净投入 (net contributed capital) are not in a
  holdings export, so you enter them; 累计净投入 is suggested as cash + Σ quantity ×
  cost + the fund value and labelled
  按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金. Once booked it is no
  longer marked as an estimate and Today's cumulative profit is computed from it,
  so replace it with the capital you actually paid in. A broker's 参考 / 摊薄成本
  includes realized gains and can differ from the ledger's weighted average cost.
- **Holdings statement, existing account**: a table of file quantity against
  ledger quantity (一致, 差额 or 未核对). A difference offers a prefilled 记一笔; it never
  proposes corrective trades by itself.

## The template (本系统模板)

Download it with 下载模板 on the upload step (`GET /api/ledger/template.csv`). Columns:

```csv
external_id,date,kind,account,ticker,currency,quantity,price,fee,amount,fx_rate,to_account,to_currency,received_amount,ratio,note
ex-001,2026-11-03,deposit,示例账户,,CNY,,,,50000,,,,,,fictional
ex-002,2026-11-04,buy,示例账户,NASDAQ:EXAMPLE,USD,2,100,1,,,,,,,fictional
ex-003,2026-11-10,dividend,示例账户,NASDAQ:EXAMPLE,USD,,,,0.80,,,,,,fictional
```

The values are fictional. Rules for template rows: every row needs an
`external_id` that is unique in the file; `currency` is required (except for a
`split`); a `buy` or `sell` needs a `fee` (write 0 when there was none); the
`account` must be the account chosen on the upload step or empty; `opening` and
`reverse` rows are refused (use 记一笔). Event fields are those of
[LEDGER.md](LEDGER.md#accounting-conventions); A-share codes are 6 digits
(`SHA:600000`), Hong Kong codes up to 5 (`HKG:00700`).

## What to do with 需处理 rows

1. Open the row (处理第 n 行). The dialog says why it needs review and which fields
   resolve it.
2. Fix those fields (choose the security, the kind, the quantity in shares, the
   fee, the currency, the date ...) and 保存修改, or 设为跳过 to leave it out.
3. If the problem is a column, go back to 核对列; if it is the account, choose the
   identities in 核对列 again.
4. Some rows cannot be fixed here (the dialog says 这一行无法在这里修改): book them
   with 记一笔 after the import, or correct the file and import it again.

A row you leave as 需处理 is simply not booked; the reconciliation names it as a
likely cause of any difference.

## Storage and retention

The uploaded file is never stored. A session in `desk.sqlite3` keeps the file
name, size and SHA-256, the parsed cells (title lines included), the mapping,
your choices and edits and the batch records; it is deleted 30 days after the
upload, and at most 50 sessions are kept. Saved column mappings and remembered
US exchanges stay until you delete them. An import preview is bound to its
session: it cannot be confirmed once the import was edited, deleted or expired,
once the ledger changed, or after an upgrade that changed the import rules;
generate it again. The rules carry a version, `INTERPRETATION_VERSION` in
`dashboard/core/importers/__init__.py` (4 in 4.0.0), which is part of every
preview's fingerprint: a preview made under an earlier version is withdrawn when
you confirm it, and you import the same file again. A preview of the 3.2 CSV
import still pending at the upgrade is withdrawn the same way (请重新导入).

The page says 全部已导入 only when nothing but 已导入, 跳过 and 已合并 rows is left;
otherwise it says which rows remain.
