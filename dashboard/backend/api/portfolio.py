"""
持仓 API 路由
"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class AddPositionRequest(BaseModel):
    """添加持仓请求"""
    symbol: str
    name: str
    quantity: int
    cost_price: float
    market: str = "a_share"  # a_share, hk, us


class UpdatePositionRequest(BaseModel):
    """更新持仓请求"""
    quantity: Optional[int] = None
    cost_price: Optional[float] = None


def get_service():
    """获取 Agent 服务实例"""
    from backend.main import get_agent_service
    service = get_agent_service()
    if service is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return service


@router.get("")
async def get_portfolio():
    """获取持仓列表"""
    service = get_service()
    return await service.get_portfolio()


@router.get("/live")
async def get_live_portfolio():
    """刷新行情后获取持仓列表"""
    service = get_service()
    await service.refresh_portfolio()
    return await service.get_portfolio()


@router.post("/add")
async def add_position(request: AddPositionRequest):
    """添加持仓"""
    service = get_service()

    await service.add_position(
        symbol=request.symbol,
        name=request.name,
        quantity=request.quantity,
        cost_price=request.cost_price,
        market=request.market
    )

    return {"message": f"已添加持仓: {request.name}({request.symbol})"}


@router.put("/{symbol}")
async def update_position(symbol: str, request: UpdatePositionRequest):
    """更新持仓"""
    service = get_service()
    await service.update_position(
        symbol=symbol,
        quantity=request.quantity,
        cost_price=request.cost_price
    )
    return {"message": f"已更新持仓: {symbol}"}


@router.delete("/{symbol}")
async def remove_position(symbol: str):
    """删除持仓"""
    service = get_service()
    await service.remove_position(symbol)
    return {"message": f"已删除持仓: {symbol}"}


@router.post("/refresh")
async def refresh_portfolio():
    """刷新持仓价格"""
    service = get_service()
    await service.refresh_portfolio()
    return {"message": "持仓价格已刷新"}


@router.get("/summary")
async def get_portfolio_summary():
    """获取持仓摘要"""
    service = get_service()
    portfolio = await service.get_portfolio()

    # 计算摘要统计
    positions = portfolio.get("positions", [])
    total_profit = portfolio.get("total_profit", 0)
    total_assets = portfolio.get("total_assets", 0)

    # 按盈亏排序
    winners = [p for p in positions if p.get("profit", 0) > 0]
    losers = [p for p in positions if p.get("profit", 0) < 0]

    return {
        "total_positions": len(positions),
        "total_assets": total_assets,
        "cash": portfolio.get("cash", 0),
        "market_value": portfolio.get("total_market_value", 0),
        "total_profit": total_profit,
        "profit_pct": (total_profit / (total_assets - total_profit) * 100) if (total_assets - total_profit) > 0 else 0,
        "winners_count": len(winners),
        "losers_count": len(losers),
        "top_winner": max(positions, key=lambda x: x.get("profit_pct", 0)) if positions else None,
        "top_loser": min(positions, key=lambda x: x.get("profit_pct", 0)) if positions else None,
    }
