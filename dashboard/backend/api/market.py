"""
行情 API 路由
"""
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

    results = []
    for item in request.symbols:
        symbol = item.get("symbol")
        market = item.get("market", "a_share")

        try:
            quote = await service.get_quote(symbol, market)
            if quote:
                results.append(quote)
            else:
                results.append({
                    "symbol": symbol,
                    "error": "未找到数据"
                })
        except Exception as e:
            results.append({
                "symbol": symbol,
                "error": str(e)
            })

    return {"quotes": results}


@router.get("/indices")
async def get_market_indices():
    """获取主要指数行情"""
    service = get_service()

    indices = [
        ("000001", "a_share"),  # 上证指数
        ("399001", "a_share"),  # 深证成指
        ("399006", "a_share"),  # 创业板指
    ]

    results = []
    for symbol, market in indices:
        try:
            quote = await service.get_quote(symbol, market)
            if quote:
                results.append(quote)
        except Exception:
            pass

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
