"""
配置和设置 API

``POST /cash`` is the v3.2 manual portfolio's cash figure. Once a ledger is
active it answers 409 with the reason (cash is booked in the ledger as 入金 /
出金); no page calls it any more and it is removed in the next release.

``GET /calendar`` says which years each market's holiday table covers (the
session clock's table, ``core/market/holidays.json``): outside those years a
trading day is decided by weekday alone.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from pydantic import BaseModel

from config import DEFAULT_API_GROUP, get_config

router = APIRouter()
logger = logging.getLogger(__name__)

LEDGER_ACTIVE_CASH = "账本已启用：现金请在交易账本中记录（入金、出金、换汇），这里的旧版现金设置已停用。"
CALENDAR_MARKETS = {"CN": "A 股", "HK": "港股", "US": "美股"}


# 延迟导入避免循环依赖
def get_agent_service():
    from backend.main import agent_service
    if agent_service is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return agent_service


class ModelInfo(BaseModel):
    id: str
    name: str
    description: str
    supports_vision: bool = False


class APIGroupInfo(BaseModel):
    name: str
    description: str
    models: List[ModelInfo]


class ConfigResponse(BaseModel):
    current_api_group: str
    current_model: str
    api_groups: Dict[str, APIGroupInfo]
    available_models: List[ModelInfo]


@router.get("/config", response_model=ConfigResponse)
async def get_config_info():
    """获取配置信息"""
    config = get_config()
    
    # 获取所有 API 组
    api_groups_data = config.get_all_api_groups()
    api_groups = {}
    
    for key, group in api_groups_data.items():
        api_groups[key] = APIGroupInfo(
            name=group.get("name", ""),
            description=group.get("description", ""),
            models=[
                ModelInfo(
                    id=m["id"],
                    name=m["name"],
                    description=m.get("description", ""),
                    supports_vision=m.get("supports_vision", False)
                )
                for m in group.get("models", [])
            ]
        )
    
    # 获取当前配置
    current_api_group = config.get("current_api_group", DEFAULT_API_GROUP)
    current_model = config.get_current_model()
    available_models = [
        ModelInfo(
            id=m["id"],
            name=m["name"],
            description=m.get("description", ""),
            supports_vision=m.get("supports_vision", False)
        )
        for m in config.get_available_models()
    ]
    
    return ConfigResponse(
        current_api_group=current_api_group,
        current_model=current_model,
        api_groups=api_groups,
        available_models=available_models
    )


class SwitchModelRequest(BaseModel):
    model_id: str


@router.post("/model")
async def switch_model(request: SwitchModelRequest, service = Depends(get_agent_service)):
    """切换模型"""
    config = get_config()
    success = config.switch_model(request.model_id)
    
    if success:
        # 热重载LLM配置
        await service.reload_llm()
        return {"success": True, "model": request.model_id, "reloaded": True}
    else:
        return {"success": False, "error": "模型不可用"}


class SwitchAPIGroupRequest(BaseModel):
    group_name: str


@router.post("/api-group")
async def switch_api_group(request: SwitchAPIGroupRequest, service = Depends(get_agent_service)):
    """切换 API 组"""
    config = get_config()
    success = config.switch_api_group(request.group_name)
    
    if success:
        # 热重载LLM配置
        await service.reload_llm()
        return {"success": True, "group": request.group_name, "reloaded": True}
    else:
        return {"success": False, "error": "API 组不存在"}


class UpdateCashRequest(BaseModel):
    cash: float


@router.post("/cash")
async def update_cash(request: UpdateCashRequest):
    """更新现金 (the legacy manual portfolio only; refused once the ledger is active)"""
    from core.ledger import active_ledger
    if active_ledger():
        raise HTTPException(409, LEDGER_ACTIVE_CASH)
    service = get_agent_service()
    service.portfolio_provider.set_cash(request.cash)
    return {"success": True, "cash": request.cash}


@router.get("/calendar")
def calendar_coverage():
    """Per market: its name and the years of the holiday table (ascending); today in Asia/Shanghai."""
    try:
        from core.market.runtime import get_market_runtime
        clock = get_market_runtime().clock
        return {"today": clock.local_date("CN").isoformat(),
                "markets": {market: {"label": label, "years": clock.table_years(market)}
                            for market, label in CALENDAR_MARKETS.items()}}
    except Exception as exc:
        logger.warning("the trading calendar is unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "暂时读不到交易日历，请稍后重试。") from None
