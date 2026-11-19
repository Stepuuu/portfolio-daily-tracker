"""Broker file import (交割单 / 对账单 / 持仓). Mounted at ``/api/imports``.

Nothing here writes to the ledger: ``propose`` creates a standard ledger proposal
that the person confirms with the existing ``POST /api/ledger/proposals/{id}/confirm``.
Every error is a 4xx with a Chinese ``detail`` (413 too large, also once expanded,
415 unreadable type, 404 unknown or expired session, 409 not possible in the
current state, 422 invalid input), except 503 when the store or the ledger is busy. In demo
mode the application's middleware answers 403 to every non-GET request; ``GET``
still works. A ``GET`` never creates the desk store or the ledger file.

All money and quantities are decimal **strings** (``"168043.68"``), never JSON numbers.

Stored data and retention: the uploaded file is never stored. A session keeps its
file name, size and SHA-256, the parsed text cells of the imported sheet (all
rows, including the title lines above the header) and of the workbook's other
data sheets, the mapping, the choices and row edits, and the batch records. It
is deleted 30 days after the upload (404 from then on; the row is removed by the
next import write); at most 50 sessions are kept. Saved mappings (per ledger
account and header layout, with the chosen fields and the chosen 资金账号 /
股东代码 values) and US exchange choices are kept until deleted. Details:
``core/importers/session.py``.

Routes
======

``POST /api/imports/parse?status=&offset=0&limit=`` (multipart/form-data)
    fields: ``file`` (the export, ≤ 5 MB; an .xlsx also ≤ 50 MB expanded, ≤ 20 MB per
    member, ≤ 500 members, no member above 1 MB compressed more than 100:1, all
    measured by inflating it, and only stored or deflated members: else 413 / 415),
    ``account`` (ledger account name), ``statement`` (optional: ``auto`` |
    ``transactions`` | ``holdings`` | ``template``).
    → 200 ``Session`` (below). A mapping saved earlier for this account and header
    layout (units in headers are part of the layout) is applied automatically,
    with the fields and the account identities chosen then. Hidden sheets are
    ignored; when several sheets hold data the session has a blocker until
    ``sheet`` is chosen. ``status`` / ``offset`` / ``limit`` choose the rows returned,
    as for ``GET`` (checked before the file is read: 422 creates no session).

``GET /api/imports/{id}?status=&offset=0&limit=``
    → 200 ``Session``, recomputed against the current ledger (after a batch is
    confirmed its rows show ``imported``).

Rows window (``parse``, ``GET`` and ``PATCH`` alike): ``status`` filters ``rows`` to one
row status (a key of ``status_labels``); ``offset`` (≥ 0) / ``limit`` (1 … 20,000) page
them. Without them every row is returned. ``rows_total`` is the filtered count and
``rows_offset`` the offset; ``counts``, ``reconciliation``, ``accounts`` and everything
else always describe the whole file, so a page can ask for the counts plus one page
of rows (``?limit=50``). A bad window is 422 and changes nothing.

``PATCH /api/imports/{id}?status=&offset=0&limit=`` (JSON, every key optional; atomic: a request refused
with 422, 409 or 503 changes nothing, neither the session nor the saved mapping;
409 when another request changed the session after this one read it, 503 when the
ledger is locked)::

    {
      "sheet": "Sheet2",                          # import another data sheet (Session.format.sheet_options);
                                                  # applies its saved or detected mapping, clears row edits.
                                                  # Naming the current sheet confirms it. Applied first.
      "statement": "transactions" | "holdings" | "template",   # re-detects the mapping, clears row edits
      "mapping": {"date": 0, "code": 2, "cash_change": null, "fees": [8, 9, 10]},
                  # partial: merged into the current mapping; null unmaps a field;
                  # "fees" replaces the fee column list. Every field named here counts as
                  # chosen (resolves its column_conflicts entry). Saved for (account, header layout).
      "account_filter": ["111", "A000000001", ""] | null,
                  # the 资金账号 / 股东代码 values (Session.accounts.columns[].values[].value) that belong
                  # to this ledger account; "" accepts rows without any account value. Every other
                  # value must occur in the file (else 422). null returns to the default. Saved with
                  # the mapping, so it applies to the next file of this layout, and remembered for the
                  # ledger account (chosen values accepted, the file's other values of the chosen or
                  # mixed dimensions excluded). A choice accepts only rows that match it: a value of a
                  # chosen dimension (or of one with several values in the file) must be chosen, a row
                  # whose values are all unchosen needs review, and a row without any account value
                  # needs review unless "" is chosen.
      "short_code_market": "HKG" | "A" | null,   # how to read ≤5-digit codes in this file
      "rows": [{"row": 12,                       # row number as in Session.rows[].row
                "include": true,                 # new / possible_duplicate; false also drops a 需处理
                                                 # holdings row from an opening
                "ticker": "SHA:600519",          # choose the security (needs review rows)
                "kind": "buy" | "sell" | "dividend" | "fee" | "deposit" | "withdrawal" | "interest" | "skip",
                "quantity": "100",               # in shares / units (股, 张, 份), never 手; ≤ 1e12
                "price": "18.20",                # ≤ 1e9
                "fee": "0", "amount": "12.34",   # ≤ 1e12 / ≤ 1e15
                "currency": "CNY" | "HKD" | "USD",   # settlement currency (CNY on an HK row = 港股通);
                                                     # overrides the file's currency evidence
                "fx_rate": "0.92",                   # CNY per unit, foreign deposits / 港股通; ≤ 1000
                "date": "2026-11-12", "allow_margin": true,
                "reset": true}],                 # drop this row's earlier edits first; a null value drops one edit
                # numbers: decimal strings, at most 10 decimal places, no scientific notation.
                # An edit replaces the file's unreadable cell of the same field (price also 成交金额,
                # amount also the cash columns; on a trade amount is |发生金额|).
                # Holdings rows accept only include, kind "skip", ticker, quantity, price; template
                # rows only include and kind "skip" (else 422). Edits stored earlier that these rules
                # now refuse make their row review ("needs" names them); drop them with null.
      "opening": {"date": "2026-11-14",          # holdings statement for a new account
                  "cash_balances": {"CNY": "12000.00", "HKD": "0"},
                  "contributed_cny": "100000", "fund_cny": "0"}
    }

    → 200 ``Session``. A US exchange chosen for a symbol is remembered in
    ``instrument_aliases``. A PATCH that changes anything the pending batch was
    built from withdraws that ledger proposal, in the same step.

``POST /api/imports/{id}/propose``
    → 200 ``{"proposal": LedgerProposal, "batch": Batch, "import": Session without rows}``.
    ``LedgerProposal`` is exactly what ``POST /api/ledger/proposals`` returns
    (``id, revision, events, before, after, duplicates, warnings``). It holds at most
    1,000 events, oldest first, and exactly the events the preview shows for the
    batch. While it is unconfirmed, calling propose again returns the same proposal
    (``batch.reused = true``) as long as neither the ledger nor the session content
    changed; otherwise the stale proposal is withdrawn and the batch prepared again.
    After it is confirmed, the next call prepares the next batch. 409 when there is
    nothing to propose, a blocker applies (``Session.blockers``), the ledger or the
    session changed while the batch was prepared (ask again), or the ledger would not
    hold exactly the preview's events (the proposal is withdrawn). A session has at
    most one unconfirmed proposal.
    ``Batch = {"number": 1, "total": 3, "label": "第 1/3 批", "events": 1000,
    "rows": [row numbers], "reused": false}``.

``DELETE /api/imports/{id}`` → ``{"deleted": true}``. The session's unconfirmed ledger
proposals are withdrawn.

Confirming (``POST /api/ledger/proposals/{id}/confirm``): every proposal made here is
bound to its session. Confirmation answers 409 and withdraws the proposal when the
session's content (or a remembered exchange or account identity it depends on) changed
after the proposal was prepared, the session expired or was deleted, the ledger changed,
or the importer's rules changed in an upgrade (``detail`` says which); prepare the batch
again. An import proposal made before bindings existed is refused the same way, and
confirming a proposal that was already withdrawn answers 409. See "Consistency" in
``core/importers/session.py``.

Session
=======
::

    {
      "id": "32 hex", "account": "主账户", "account_exists": true,
      "created_at": "...", "updated_at": "...",
      "file": {"name": "x.xls", "size": 12345, "sha256": "..."},
      "format": {"type": "text" | "xlsx" | "xls" | "html", "label": "GBK 制表符分隔文本（扩展名是 .xls）",
                 "encoding": "GBK" | "UTF-8" | "UTF-8 (BOM)" | "UTF-16" | null,
                 "delimiter": "tab" | "comma" | "semicolon" | "pipe" | "whitespace" | null,
                 "sheet": "Sheet1" | null,           # the sheet (or web-page table) being imported
                 "sheets": [...],                    # every sheet name of the workbook
                 "sheet_options": [{"name": "Sheet1", "hidden": false, "data_rows": 120}],   # sheets with data
                 "sheet_chosen": false,              # true once the person chose with PATCH sheet
                 "header_row": 3, "extension": ".xls",
                 "title_lines": ["交割单 2026-11-01 至 2026-11-14"]},
      "statement": "transactions" | "holdings" | "template", "statement_label": "交割单 / 对账单",
      "preset": {"id": "ths_trades", "label": "同花顺交割单", "verified": false,
                 "note": "预设未经你的券商文件验证" | null},
      "columns": [{"index": 0, "header": "成交日期", "field": "date" | "fees" | null, "field_label": "日期"}],
      "mapping": {"date": 0, ..., "fees": [8, 9]},
      "mapping_source": "saved" | "preset" | "synonyms" | "edited",
      "fields": [{"field": "date", "label": "日期", "required": true}, ...],   # what a column can map to
                                              # (new: fund_account 资金账号)
      "column_conflicts": [{"field": "price", "label": "成交价格", "columns": [5, 6], "chosen": 5,
                            "rows": [3, 4], "resolved": false}],
                  # two columns that could feed the same field and differ in these rows (at most 20
                  # listed); for "fees" (a fee column name listed twice) "chosen" is null and "rows" [].
                  # "action" columns (业务名称, 操作, 摘要 ...) conflict when they state different
                  # business types (证券买入 and 买入 agree; an empty cell states nothing).
                  # An unresolved conflict is a blocker until PATCH mapping names the field.
      "accounts": {"mixed": true, "source": "single" | "dominant" | "chosen" | "saved",
                   "columns": [{"dimension": "fund_account" | "shareholder:SH" | "shareholder:SZ"
                                             | "shareholder:other",
                                "label": "资金账号", "values": [{"value": "111", "rows": 5, "accepted": true}]}],
                   "accepted": ["111"], "accept_rows_without_account": false, "rows_without_account": 0,
                   "rows_kept_out": 3,
                   "summary": "资金账号 111（5 行）、222（3 行）" | null,
                   "remembered": [{"dimension", "label", "value", "state": "accepted" | "excluded"}]} | null,
                  # null: no such columns. "accepted" (per value and the list) is the decision applied:
                  # an accepted value the account's memory refuses is false. "rows_kept_out": the rows
                  # this decision makes review (needs "account"). "remembered": what earlier imports into
                  # this ledger account confirmed and what the person chose or excluded for it; unless this file's identities
                  # are chosen explicitly, a row of an excluded value, or of a value other than the accepted
                  # ones of its dimension, needs review (needs "account"), also in a single-identity file.
                  # One 沪 and one 深 股东代码 are one account. A dimension with two values is "mixed":
                  # rows outside "accepted" are review (needs "account"); "dominant" (the most frequent
                  # value, a preview only) is a blocker until account_filter is chosen.
      "short_code_market": null,
      "date_range": {"from": "2026-11-03", "to": "2026-11-14"} | null,
      "counts": {"new": 180, "imported": 22, "changed": 0, "possible_duplicate": 8, "review": 4,
                 "skipped": 8, "unsupported": 1, "merged": 1, "total": 224,
                 "included_rows": 180, "included_events": 181},
      "status_labels": {"new": "新", ...},
      "skipped": [{"status": "skipped" | "unsupported" | "merged", "label": "撤单 / 废单", "count": 5,
                   "rows": [..], "amount": "12.34" (merged only)}],
      "reconciliation": Reconciliation | null,        # transactions and template
      "holdings": Holdings | null,                    # holdings statements
      "batches": {"confirmed": 1, "pending": {"number": 2, "proposal_id": "...", "events": 1000,
                  "rows": [...], "created_at": "...", "state": "pending", "stale": false} | null,
                  "remaining_events": 1300, "remaining_batches": 2, "total": 3, "next": 2,
                  "limit": 1000, "history": [{..., "state": "confirmed" | "pending" | "discarded"}]},
                  # stale: the ledger or the session content changed since it was prepared
      "blockers": ["账户“主账户”还没有期初余额。…"],   # propose is refused while non-empty
      "warnings": [...],
      "ledger_revision": 12,
      "rows": [Row], "rows_total": 224, "rows_offset": 0
    }

Row::

    {"row": 5, "cells": ["2026-11-03", "600519", ...], "order": 3,      # order = chronological position
     "status": "new", "status_label": "新", "include": true, "can_include": true,
     "category": "buy", "category_label": "买入", "action": "证券买入",
     "date": "2026-11-03", "time": "09:31:25", "code": "600519", "name": "贵州茅台",
     "ticker": "SHA:600519" | null, "currency": "CNY",
     "quantity": "100", "price": "1680", "turnover": "168000", "cash_change": "-168043.68",
     "fee": "43.68", "fee_source": "columns" | "derived" | "edited" | null,
     "price_source": "columns" | "derived" | "edited" | null,   # derived: 成交金额 ÷ 数量 (the file's price
                       # was rounded or missing), with as many places as make the cash exact; null: no trade price
     "cash_balance": "31956.32", "share_balance": "100",
     "external_id": "t:2026-11-03:600519:0012345",
     "events": [ledger events exactly as they will be proposed, including "id"],
     "messages": ["why the row has this status"], "notes": ["what was inferred"],
     "needs": [...],   # what resolves it: a row edit field (ticker, kind, quantity, price, fee, amount,
                       # date, currency, fx_rate, allow_margin), "mapping" (fix the column mapping)
                       # or "account" (choose it in account_filter). Empty only when a message says
                       # the row cannot be fixed here ("这一行无法在这里修改") and what to do instead.
     "choices": {"ticker": ["SHE:000001", "HKG:00001"]},
     "account_identity": [{"dimension": "fund_account", "label": "资金账号", "value": "111"}],
     "edits": {...}, "existing": {ledger event} | null,
     "duplicate_of": {"id", "source", "date", "kind", "ticker", "quantity", "price", "fee", "currency",
                      "amount", "ratio", "note",        # as booked, when present
                      "name": "招商银行",                 # the event's, else the listing's name in the ledger
                      "source_label": "手工" | "交割单导入" | "助手" | …,
                      "added_quantity": "300"} | null,   # a split: the shares it added
     "merged_into": 6 | null, "pair_row": 6 | null}

Row statuses: ``new`` 新 (bookable, included by default), ``possible_duplicate``
可能重复 (bookable, unchecked by default: the ledger already has an event of this
account, date, listing, kind and quantity, or currency and amount, under another id,
e.g. from an earlier export without 成交编号; for a 送股 row, a split that added the
same shares), ``imported`` 已导入, ``changed``
与已入账记录不同 (same id, different content; excluded), ``review`` 需处理
(cannot be booked until edited: ``needs`` says what), ``skipped`` 跳过, ``unsupported``
不支持 (北交所, B 股), ``merged`` 已合并 (逆回购 lending leg folded into its 购回 row).
Category ``correction`` (冲正 / 撤销: 冲正, 红冲, 冲销, 撤销 …) is always review.

Review, never a guess (``core/importers/transactions.py``, "Guards"): a row is bookable
only when the file establishes every fact the booking needs. Among the cases:
a correction or a trade whose signs contradict its action (also a negative 成交金额 on
a sell, a stated rate that is not positive, money moving on a 送股 row, a fee on a cash
row); a fractional quantity of a mainland or Hong Kong listing; a row dated outside
the period the title lines state; a row with more cells than the header (a comma that
split a number) or below a second header with other columns; a number with a space
inside or a comma outside its grouping place (a file that uses the decimal comma is
read that way throughout; one that mixes both conventions is not);
a 逆回购 leg whose partner is ambiguous, whose stated direction (借出 / 购回) contradicts
its cash, or whose interest exceeds the sanity bound; fee columns whose overlap only a
cash amount could settle; an unsupported (EUR, NZD, 新西兰元 …), ambiguous ($) or
conflicting currency, and a cash row without a currency in a file that names another;
a template row without a currency, or a buy / sell without a fee; a quantity in 手 whose lot is
not certain (only A-share stocks and ETFs: 100) or a 万元 figure that is not exact
to the cent; a unit that does not fit its column; an exchange in the file that
contradicts a remembered ticker; a qualified or suffixed code (SHA:000001, 000001.SZ)
whose prefix or market column names another exchange, a 6-digit code on NASDAQ / NYSE,
a US symbol on a mainland or Hong Kong market; a B share (one message for every spelling);
two columns of one field that disagree after their units (股 vs 手) are converted, and an
action column that states a business where the chosen one is blank; a row of another account
identity or of one the account choice does not name; a negative price, a dividend
from a negative amount, or a negative fee no cash amount confirms; a 逆回购 leg or
an A-share dividend in a currency other than CNY; a dividend read from 成交金额 whose
currency contradicts the row's; any number outside the field bounds (template rows
included); a stored edit that today's rules refuse.

Reconciliation::

    {"available": true, "as_of": "2026-11-14",
     "cash": [{"currency": "CNY", "file": "120000", "ledger": "120000", "difference": "0",
               "status": "一致" | "差额" | "未核对",
               "label": "一致" | "差 CNY 12.00" | "未核对：还有 2 行没有入账", "row": 214}],
     "positions": [{"ticker": "SHA:600519", "code": "600519", "name": "...", "file": "300", "ledger": "300",
                    "difference": "0", "status": "一致" | "差额" | "未核对", "label": "一致", "row": 210}],
     "causes": ["需处理 2 行没有入账，资金变动合计 -10,000.00", ...],
     "summary": "导入后 CNY 现金 120,000.00，券商资金余额 120,000.00，一致 · 股份余额 3/3 一致",
     "consistent": true}
    or {"available": false, "reason": "..."}

``ledger`` is the ledger as of ``as_of`` (the file's last date) plus every included
row, as if confirmed; events confirmed with a later date never change it. A line the
ledger meets while rows that move that cash (or those shares) are 需处理, 不支持 or
unchecked is ``未核对`` (``consistent`` false), never ``一致``. A cash-only row (a
dividend, interest, a fee) moves no shares and counts for its cash line only.

Holdings::

    {"mode": "opening" | "reconcile", "summary": [{"label": "资金余额", "value": "12000.00"}],
     "cost_note": "券商“参考/摊薄成本”含已实现盈亏…", "opening": {...} | null,
     "positions": [{"ticker", "name", "quantity", "cost_price"}],
     "suggested_contributed_cny": {"value": "123456.78" | null,    # opening mode: CNY cash + positions at cost
                                   "label": "按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金"},   # + fund
     "reconciliation": [{"ticker", "name", "row", "file_quantity", "ledger_quantity", "difference",
                         "status": "一致" | "差额" | "未核对", "label", "file_cost", "ledger_cost", "cost_note",
                         "entry": {"kind": "buy" | "sell", "account", "ticker", "quantity",
                                   "price": null, "source": "import"} | null}]}   # reconcile mode
    # A row needing review for its cost still compares its quantity (file_cost null). 未核对: the
    # ticker is listed twice, or an unresolved code may be it; file_quantity and difference null, no entry.
"""
import logging
import sqlite3
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile

from core.desk_store import DeskStoreError
from core.importers import ImportFailure, session
from core.importers.sniff import MAX_BYTES, MAX_ROWS
from core.importers.transactions import STATUS_LABELS
from core.ledger import LedgerError

logger = logging.getLogger(__name__)
router = APIRouter()

_PATCH_KEYS = {'statement', 'mapping', 'short_code_market', 'rows', 'opening', 'account_filter', 'sheet'}


def attempt(operation):
    try:
        return operation()
    except ImportFailure as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except LedgerError as exc:
        raise HTTPException(422, f'账本无法处理：{exc}') from None
    except DeskStoreError:
        raise HTTPException(409, '导入数据的版本比程序新。请先更新程序。') from None
    except sqlite3.OperationalError as exc:
        if 'locked' not in str(exc) and 'busy' not in str(exc):
            raise
        raise HTTPException(503, '系统正忙，暂时无法处理。请稍后重试。') from None


def _rows_window(status: Optional[str], offset: int, limit: Optional[int]):
    """The rows window of parse, GET and PATCH (see "Rows window")."""
    if status is not None and status not in STATUS_LABELS:
        raise HTTPException(422, '未知的行状态：' + str(status)[:40])
    if offset < 0 or limit is not None and not 1 <= limit <= MAX_ROWS:
        raise HTTPException(422, f'行的范围无效：起始行不能为负数，每页行数必须在 1 到 {MAX_ROWS} 之间。')


@router.post('/parse')
async def parse(file: Optional[UploadFile] = File(None), account: Optional[str] = Form(None),
                statement: Optional[str] = Form(None), status: Optional[str] = Query(None),
                offset: int = Query(0), limit: Optional[int] = Query(None)):
    _rows_window(status, offset, limit)
    if file is None:
        raise HTTPException(422, '请选择要导入的文件。')
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, '文件超过 5 MB。请缩短导出的日期范围后再试。')

    def run():
        try:
            created, analysis = session.create(data, file.filename or '', account, statement, analysed=True)
        except (ImportFailure, LedgerError, DeskStoreError, sqlite3.OperationalError):
            raise
        except Exception as exc:  # a parser library failing on a malformed file is the file's problem
            logger.warning('import parse failed: %s', type(exc).__name__)
            raise ImportFailure('文件无法解析。请确认它是券商导出的交割单、对账单或持仓文件。') from None
        return session.view(created, analysis, status=status, offset=offset, limit=limit)
    return attempt(run)


@router.get('/{session_id}')
def read(session_id: str, status: Optional[str] = Query(None), offset: int = Query(0),
         limit: Optional[int] = Query(None)):
    _rows_window(status, offset, limit)
    return attempt(lambda: session.view(session.load(session_id), status=status, offset=offset, limit=limit))


@router.patch('/{session_id}')
def update(session_id: str, body: Dict[str, Any] = Body(...), status: Optional[str] = Query(None),
           offset: int = Query(0), limit: Optional[int] = Query(None)):
    _rows_window(status, offset, limit)
    unknown = set(body) - _PATCH_KEYS
    if unknown:
        raise HTTPException(422, '不能修改：' + '、'.join(sorted(unknown)))
    if body.get('rows') is not None and (not isinstance(body['rows'], list) or len(body['rows']) > MAX_ROWS
                                         or not all(isinstance(r, dict) for r in body['rows'])):
        raise HTTPException(422, 'rows 必须是对象数组。')
    if body.get('mapping') is not None and not isinstance(body['mapping'], dict):
        raise HTTPException(422, 'mapping 必须是对象。')

    def run():
        saved, analysis = session.patch(session_id, body)
        return session.view(saved, analysis, status=status, offset=offset, limit=limit)
    return attempt(run)


@router.post('/{session_id}/propose')
def propose(session_id: str):
    return attempt(lambda: session.propose(session_id))


@router.delete('/{session_id}')
def discard(session_id: str):
    return attempt(lambda: session.delete(session_id))
