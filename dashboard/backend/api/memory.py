"""
记忆 API 路由
"""
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class UpdateProfileRequest(BaseModel):
    """更新用户画像请求"""
    name: Optional[str] = None
    experience_level: Optional[str] = None
    trading_style: Optional[str] = None
    risk_tolerance: Optional[str] = None
    notes: Optional[str] = None


class AddLessonRequest(BaseModel):
    """添加交易教训请求"""
    description: str
    lesson_type: str  # win/loss/mistake/insight
    lesson: str = ""
    symbol: Optional[str] = None


class AddSectorRequest(BaseModel):
    """添加偏好板块请求"""
    sector: str


class DeleteSectorRequest(BaseModel):
    """删除偏好板块请求"""
    sector: str


class AddTriggerRequest(BaseModel):
    """添加情绪触发器请求"""
    trigger: str


class DeleteTriggerRequest(BaseModel):
    """删除情绪触发器请求"""
    trigger: str


class AddGoalRequest(BaseModel):
    """添加目标请求"""
    goal_type: str  # short_term/long_term
    goal: str


def get_service():
    """获取 Agent 服务实例"""
    from backend.main import get_agent_service
    service = get_agent_service()
    if service is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return service


@router.get("")
async def get_memory():
    """获取完整用户记忆"""
    service = get_service()
    return service.get_memory()


@router.get("/profile")
async def get_profile():
    """获取用户画像"""
    service = get_service()
    memory = service.get_memory()
    return memory.get("profile", {})


@router.put("/profile")
async def update_profile(request: UpdateProfileRequest):
    """更新用户画像"""
    service = get_service()

    updates = {}
    if request.name:
        updates["name"] = request.name
    if request.experience_level:
        updates["experience_level"] = request.experience_level
    if request.trading_style:
        updates["trading_style"] = request.trading_style
    if request.risk_tolerance:
        updates["risk_tolerance"] = request.risk_tolerance
    if request.notes is not None:
        updates["notes"] = request.notes

    if updates:
        service.update_profile(**updates)

    return {"message": "用户画像已更新", "updates": updates}


@router.get("/preferences")
async def get_preferences():
    """获取交易偏好"""
    service = get_service()
    memory = service.get_memory()
    return memory.get("preferences", {})


@router.post("/preferences/sector")
async def add_preferred_sector(request: AddSectorRequest):
    """添加偏好板块"""
    service = get_service()
    service.memory_manager.add_preferred_sector(request.sector)
    return {"message": f"已添加偏好板块: {request.sector}"}


@router.delete("/preferences/sector")
async def delete_preferred_sector(request: DeleteSectorRequest):
    """删除偏好板块"""
    service = get_service()
    service.memory_manager.remove_preferred_sector(request.sector)
    return {"message": f"已删除偏好板块: {request.sector}"}


@router.post("/preferences/trigger")
async def add_emotional_trigger(request: AddTriggerRequest):
    """添加情绪触发器"""
    service = get_service()
    service.memory_manager.add_emotional_trigger(request.trigger)
    return {"message": f"已添加情绪触发器: {request.trigger}"}


@router.delete("/preferences/trigger")
async def delete_emotional_trigger(request: DeleteTriggerRequest):
    """删除情绪触发器"""
    service = get_service()
    service.memory_manager.remove_emotional_trigger(request.trigger)
    return {"message": f"已删除情绪触发器: {request.trigger}"}


@router.get("/history")
async def get_trading_history():
    """获取交易历史（教训）"""
    service = get_service()
    memory = service.get_memory()
    return memory.get("history", {})


@router.post("/history/lesson")
async def add_lesson(request: AddLessonRequest):
    """添加交易教训"""
    service = get_service()
    service.add_lesson(
        description=request.description,
        lesson_type=request.lesson_type,
        lesson=request.lesson,
        symbol=request.symbol
    )
    return {"message": "交易教训已记录"}


@router.delete("/history/lesson/{index}")
async def delete_lesson(index: int):
    """删除指定索引的交易教训"""
    service = get_service()
    service.memory_manager.remove_lesson(index)
    return {"message": f"已删除交易教训"}


@router.get("/goals")
async def get_goals():
    """获取用户目标"""
    service = get_service()
    memory = service.get_memory()
    return memory.get("goals", {})


@router.post("/goals")
async def add_goal(request: AddGoalRequest):
    """添加目标"""
    service = get_service()

    if request.goal_type == "short_term":
        service.memory_manager.add_short_term_goal(request.goal)
    elif request.goal_type == "long_term":
        service.memory_manager.add_long_term_goal(request.goal)
    else:
        raise HTTPException(status_code=400, detail="无效的目标类型")

    return {"message": f"已添加{request.goal_type}目标"}


@router.get("/context")
async def get_memory_context():
    """获取用于 Agent 的记忆上下文字符串"""
    service = get_service()
    context = service.memory_manager.get_context_string()
    return {"context": context}


@router.delete("/reset")
async def reset_memory():
    """重置记忆（危险操作）"""
    service = get_service()
    service.memory_manager.clear_all()
    return {"message": "记忆已重置"}
