"""
配置和设置 API
"""
from fastapi import APIRouter, Depends
from typing import List, Dict, Any
from pydantic import BaseModel

from config import get_config

router = APIRouter()


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
    current_api_group = config.get("current_api_group", "xhub")
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
async def update_cash(request: UpdateCashRequest, service = Depends(get_agent_service)):
    """更新现金"""
    service.portfolio_provider.set_cash(request.cash)
    return {"success": True, "cash": request.cash}
