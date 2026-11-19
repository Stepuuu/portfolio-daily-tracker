"""
行情 API 路由

``GET /bars?ticker=&days=``  daily bars for the stock drawer's chart (read-only), from the same
                             source as the assistant's price-history tool (``core.market.bars``)::

    {ticker, currency, source, provider, adjust, adjust_label, adjust_note, fetched_at, data_date,
     last_bar_provisional, calendar, coverage, bars: [{date, open, high, low, close, volume}]}

``bars`` are oldest first. ``last_bar_provisional`` is true when the newest bar is today's and
the instrument's exchange has not closed yet (it still changes). Errors are
``{"detail": "<中文说明>"}``: 422 (not an ``EXCH:CODE`` ticker, an index, ``days`` out of
range), 502 (the data source failed; the detail names it), 503 (demo mode or scripted quotes
without scripted bars). With scripted quotes (demo mode or ``TRACKER_QUOTE_SOURCE=fake``) the
network is never used: bars come from ``PORTFOLIO_DIR/fake_bars.json``
(``{"SHA:DEMO": [{date, open, high, low, close, volume}, ...]}``) and the scripted clock, which is
"now" for them: a bar dated after the scripted clock's date at the instrument's own exchange does not
exist yet and is left out before the window is taken (so ``coverage`` and ``last_bar_provisional``
describe what is served), and a script for the ticker that is not a list of rows each with a
``YYYY-MM-DD`` date is a 502 that says so.
"""
import json
import logging
import re
from datetime import date, datetime
from typing import List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.provider_errors import log_failure

router = APIRouter()
logger = logging.getLogger(__name__)

FAKE_BARS_FILE = 'fake_bars.json'
ADJUST_LABELS = {'qfq': '前复权', 'yahoo_auto_adjust': '复权（雅虎）'}
NO_FAKE_BARS = '演示模式和模拟行情不联网，没有日线数据。'
SCRIPTED_LABEL = '模拟日线'


class QuoteRequest(BaseModel):
    """行情请求"""
    symbol: str
    market: str = "a_share"


class BatchQuoteRequest(BaseModel):
    """批量行情请求"""
    symbols: List[dict]  # [{"symbol": "600519", "market": "a_share"}, ...]


def get_service():
    """获取 Agent 服务实例"""
    from backend.main import get_agent_service
    service = get_agent_service()
    if service is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return service


@router.get("/overview")
async def get_market_overview():
    """获取市场概览，兼容旧版前端轮询入口。

    只返回主要指数。曾经附带的固定“热门股票”列表已移除：它与用户无关，
    关注的标的请用自选（/api/watchlist）。
    """
    service = get_service()

    indices = await service.get_quotes(["000001", "399001", "399006"], "a_share")

    return {
        "indices": indices,
        "updated_at": datetime.utcnow().isoformat()
    }


@router.get("/quote/{symbol}")
async def get_quote(symbol: str, market: str = "a_share"):
    """获取单只股票行情"""
    print(f"[API] 收到行情请求: symbol={symbol}, market={market}")
    service = get_service()
    quote = await service.get_quote(symbol, market)

    if quote:
        print(f"[API] 返回行情: {quote['name']} - {quote['price']}")
    else:
        print(f"[API] 未找到行情: {symbol}")

    if quote is None:
        raise HTTPException(status_code=404, detail=f"未找到股票: {symbol}")

    return quote


@router.post("/quotes")
async def get_quotes(request: BatchQuoteRequest):
    """批量获取行情"""
    service = get_service()
    grouped_symbols: dict[str, List[str]] = {}
    ordered_requests: List[tuple[str, str]] = []

    for item in request.symbols:
        symbol = item.get("symbol")
        market = item.get("market", "a_share")
        if not symbol:
            continue
        grouped_symbols.setdefault(market, []).append(symbol)
        ordered_requests.append((symbol, market))

    quote_map = {}
    results = []

    for market, symbols in grouped_symbols.items():
        try:
            quotes = await service.get_quotes(symbols, market)
            for quote in quotes:
                quote_map[(quote["symbol"], market)] = quote
        except Exception as e:
            # A fixed text: the provider's message stays in the log.
            reason = log_failure(logger, f"legacy quotes {market}", e)
            for symbol in symbols:
                results.append({
                    "symbol": symbol,
                    "market": market,
                    "error": reason
                })

    for symbol, market in ordered_requests:
        quote = quote_map.get((symbol, market))
        if quote:
            results.append(quote)
        elif not any(item.get("symbol") == symbol and item.get("market") == market for item in results):
            results.append({
                "symbol": symbol,
                "market": market,
                "error": "未找到数据"
            })

    return {"quotes": results}


@router.get("/indices")
async def get_market_indices():
    """获取主要指数行情"""
    service = get_service()
    results = await service.get_quotes(["000001", "399001", "399006"], "a_share")
    return {"indices": results}


@router.get("/search/{keyword}")
async def search_stocks(keyword: str):
    """搜索股票"""
    # TODO: 实现股票搜索功能
    # 目前返回空结果
    return {
        "results": [],
        "message": "搜索功能开发中"
    }


def _scripted_bars(instrument):
    """``(FakeBarSource, clock)`` reading ``fake_bars.json`` for ``instrument``, without the bars dated
    after the scripted clock's date at that instrument's exchange; HTTP 503 when there is no script,
    502 when the instrument's script is malformed."""
    from core.ledger import portfolio_directory
    from core.market.bars import FakeBarSource
    from core.market.runtime import get_market_runtime
    path = portfolio_directory() / FAKE_BARS_FILE
    try:
        script = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        script = None
    if not isinstance(script, dict):
        raise HTTPException(503, NO_FAKE_BARS)
    clock = get_market_runtime().clock
    today = clock.local_date(instrument.market)
    rows = script.get(instrument.ticker)
    if rows is not None:
        if not isinstance(rows, list) or not all(isinstance(row, dict) and _iso_date(row.get('date')) for row in rows):
            raise HTTPException(502, f'日线暂不可用：{SCRIPTED_LABEL}的数据格式不正确（每一行都需要 YYYY-MM-DD 格式的日期）')
        rows = [row for row in rows if _iso_date(row['date']) <= today]
    return FakeBarSource({instrument.ticker: rows} if rows is not None else {}, today=today, label=SCRIPTED_LABEL), clock


def _iso_date(value):
    """The date of a ``YYYY-MM-DD`` string, else ``None``."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/bars")
async def get_daily_bars(ticker: str = Query(..., max_length=40), days: int = Query(120, ge=5, le=250)):
    """Daily bars for one ticker (see the module docstring)."""
    from core.agent_tools.market_tools import ADJUST_NOTES, _session
    from core.market.bars import ADJUST_QFQ, BarError, get_bar_source
    from core.market.instruments import InstrumentError, parse
    from core.market.runtime import fake_requested
    from core.market.today import moment
    try:
        instrument = parse(ticker)
    except InstrumentError:
        raise HTTPException(422, '代码格式不正确，请使用“交易所:代码”，例如 SHA:600519。') from None
    if instrument.kind == 'index':
        raise HTTPException(422, '指数暂不提供日线。')
    clock = None
    source = get_bar_source()
    if fake_requested():
        source, clock = _scripted_bars(instrument)
    try:
        result = await source.get_daily(instrument.ticker, days, ADJUST_QFQ)
    except BarError as exc:
        where = f'（{exc.source}）' if exc.source and exc.source != 'bars' else ''
        raise HTTPException(502, f'日线暂不可用：{exc}{where}') from None
    if clock is not None:
        # The scripted clock is "now" for scripted data, so a bar of today is judged against it.
        result.fetched_at = moment(clock.now())
    if not result.bars:
        raise HTTPException(502, f'日线暂不可用：数据源没有返回日线（{result.source}）')
    provisional, calendar, _notes = _session(result)
    columns = ('open', 'high', 'low', 'close', 'volume')
    return {
        'ticker': result.ticker, 'currency': result.currency, 'source': result.source,
        'provider': getattr(result, 'provider', '') or '', 'adjust': result.adjust,
        'adjust_label': ADJUST_LABELS.get(result.adjust, result.adjust),
        'adjust_note': ADJUST_NOTES.get(result.adjust, ''), 'fetched_at': result.fetched_at,
        'data_date': result.bars[-1]['date'], 'last_bar_provisional': provisional, 'calendar': calendar,
        'coverage': result.coverage,
        'bars': [{'date': bar['date'], **{name: bar.get(name) for name in columns}} for bar in result.bars],
    }
