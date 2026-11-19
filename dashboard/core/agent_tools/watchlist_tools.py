"""Agent tools for live quotes and the watchlist: read both, propose an addition.

``get_quotes`` and ``get_watchlist`` return the same quote objects as
``GET /api/quotes`` (with source, exchange time and status; ``null`` means
unavailable, never zero). ``propose_watchlist_add`` stores a
``watchlist_add`` proposal; the item exists only after the person confirms
it in the web app.

Instrument names (from the quote provider, the ledger or the name index),
the watchlist's notes and any text quoting them reach the model only as
``{"untrusted_text": ...}`` (``_envelope.fence``); the HTTP API returns the
same values as plain strings.
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from core import agent_tools, proposals, watchlist
from core.market.instruments import InstrumentError, parse
from core.market.runtime import get_market_runtime
from core.market.today import MAX_QUOTE_TICKERS, build_quotes
from core.tools import Tool, ToolParameter, ToolParameterType

from ._envelope import UNTRUSTED_NOTE, fence, not_proposed, proposal_response

SOURCE = 'agent:chat'
QUOTE_NOTE = ('price/prev_close/change_pct 为 null 表示不可用（quote.reason 说明原因），不是 0；'
              'quote.status 为 stale/time_unknown/prev_close 时只能称为参考价，不能说成实时价。'
              '引用价格时写明 quote.source 和 quote.quote_time。' + UNTRUSTED_NOTE)


def _source_names(runtime=None) -> List[str]:
    try:
        return list((runtime or get_market_runtime()).quotes.source_names)
    except Exception:
        return []


def _tickers(value: Any) -> List[str]:
    if isinstance(value, str):
        value = value.replace('，', ',').split(',')
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item).strip().upper() for item in value if str(item).strip()))


def _hint(text: str) -> Optional[str]:
    """A suggested ticker for a bare code, from the shared code rule."""
    from core.market.search import infer
    for candidate in infer(text):
        if candidate.ticker and candidate.supported and not candidate.needs_confirmation:
            return candidate.ticker
    return None


def _quotes(tickers: Any) -> dict:
    wanted = _tickers(tickers)
    if not wanted:
        return {'error': '请提供 tickers：带交易所前缀的代码列表，例如 ["SHE:000001", "HKG:00700"]', 'source': 'quotes'}
    if len(wanted) > MAX_QUOTE_TICKERS:
        return {'error': f'一次最多查询 {MAX_QUOTE_TICKERS} 个代码', 'source': 'quotes'}
    valid, errors = [], []
    for ticker in wanted:
        try:
            parse(ticker)
            valid.append(ticker)
        except InstrumentError:
            hint = _hint(ticker)
            errors.append({'ticker': ticker, 'error': '代码必须带交易所前缀' + (f'，是否指 {hint}？' if hint else '')})
    runtime = get_market_runtime()
    payload = build_quotes(runtime, valid) if valid else {'as_of': None, 'source': None, 'quotes': []}
    payload = fence(payload, sources=_source_names(runtime))
    payload['note'] = QUOTE_NOTE
    if errors:
        payload['errors'] = errors
    return payload


def _watchlist() -> dict:
    runtime = get_market_runtime()
    payload = fence(watchlist.view(quotes=True, runtime=runtime), sources=_source_names(runtime))
    payload['note'] = QUOTE_NOTE
    return payload


@agent_tools.register
def watchlist_tools(runtime):
    async def get_quotes(tickers: Any) -> dict:
        return await asyncio.to_thread(_quotes, tickers)

    async def get_watchlist() -> dict:
        return await asyncio.to_thread(_watchlist)

    async def propose_watchlist_add(ticker: str, note: Optional[str] = None) -> dict:
        payload = {'ticker': ticker}
        if note:
            payload['note'] = note
        try:
            proposal = await asyncio.to_thread(proposals.propose, watchlist.PROPOSAL_KIND, payload, SOURCE)
        except proposals.ProposalError as exc:
            return not_proposed(exc, [ticker, note])
        # The payload's name was looked up (ledger, alias, name index), the
        # note is the person's: both, and the summary quoting them, are fenced.
        return proposal_response(proposal,
                                 message='尚未加入自选。请用户在页面上的“待确认”中核对并确认；确认之前自选没有任何变化。')

    return [
        Tool(name='get_quotes',
             description=('查询一个或多个标的的最新行情（最多 50 个），返回价格、昨收、涨跌幅、币种，以及行情来源、'
                          '交易所时间和状态（live/delayed/closed/stale/prev_close/time_unknown/unavailable）。'
                          '代码必须带交易所前缀：SHE:000001（平安银行，深市）、SHA:600519、HKG:00700、NASDAQ:AAPL；'
                          '指数写 IDX:000001（上证指数）。裸代码会被拒绝而不是猜测。'
                          '价格为 null 表示不可用，必须如实告诉用户，不得编造或补 0。'),
             parameters=[ToolParameter('tickers', ToolParameterType.ARRAY, '带交易所前缀的代码列表',
                                       items={'type': 'string'})],
             function=get_quotes),
        Tool(name='get_watchlist',
             description=('读取用户的自选列表（代码、名称、备注、是否持有）以及每个标的的最新行情（同 get_quotes 的格式，'
                          '带来源、交易所时间和状态）。'),
             parameters=[], function=get_watchlist),
        Tool(name='propose_watchlist_add',
             description=('提出（不是执行）把一个标的加入自选的方案，由用户在页面上确认后才会加入。'
                          '只在用户明确要求关注某个标的时使用；代码必须带交易所前缀。不得声称已经加入。'),
             parameters=[ToolParameter('ticker', ToolParameterType.STRING, '带交易所前缀的代码，例如 SHE:000001'),
                         ToolParameter('note', ToolParameterType.STRING, '可选的简短备注（用户的原话）', required=False)],
             function=propose_watchlist_add),
    ]
