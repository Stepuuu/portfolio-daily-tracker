"""
建议 API 路由
"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def get_service():
    """获取 Agent 服务实例"""
    from backend.main import get_agent_service
    service = get_agent_service()
    if service is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return service


@router.get("")
async def get_suggestions():
    """获取最近的建议"""
    service = get_service()
    return {
        "suggestions": service.get_recent_suggestions()
    }


@router.get("/risks")
async def get_risks():
    """获取最近的风险提示"""
    service = get_service()
    return {
        "risks": service.get_recent_risks()
    }


@router.get("/all")
async def get_all_insights():
    """获取所有洞察（建议 + 风险）"""
    service = get_service()
    return {
        "suggestions": service.get_recent_suggestions(),
        "risks": service.get_recent_risks()
    }


@router.get("/summary")
async def get_insights_summary():
    """获取建议摘要"""
    service = get_service()

    suggestions = service.get_recent_suggestions()
    risks = service.get_recent_risks()

    # 按类型统计建议
    suggestion_types = {}
    for s in suggestions:
        t = s.get("type", "unknown")
        suggestion_types[t] = suggestion_types.get(t, 0) + 1

    # 按级别统计风险
    risk_levels = {}
    for r in risks:
        level = r.get("level", "unknown")
        risk_levels[level] = risk_levels.get(level, 0) + 1

    return {
        "total_suggestions": len(suggestions),
        "suggestion_types": suggestion_types,
        "total_risks": len(risks),
        "risk_levels": risk_levels,
        "high_confidence_suggestions": [
            s for s in suggestions if s.get("confidence") == "high"
        ],
        "high_risks": [
            r for r in risks if r.get("level") == "high"
        ]
    }
