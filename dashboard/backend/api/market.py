"""
行情 API 路由
"""
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


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
    """获取市场概览，兼容旧版前端轮询入口"""
    service = get_service()

    indices = await service.get_quotes(["000001", "399001", "399006"], "a_share")
    hot_stocks = await service.get_quotes(
        ["600519", "000858", "601318", "600036", "000333"],
        "a_share"
    )

    return {
        "indices": indices,
        "hot_stocks": hot_stocks,
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
            for symbol in symbols:
                results.append({
                    "symbol": symbol,
                    "market": market,
                    "error": str(e)
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
