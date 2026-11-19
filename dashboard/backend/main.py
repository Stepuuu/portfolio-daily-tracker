"""
FastAPI 后端主入口
"""
import logging
import sys
import os
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from backend.api import chat, portfolio, market, memory, suggestions, settings
from backend.api import backtest, portfolio_tracker, ledger, lab
from backend import extensions, features
from backend.services.agent_service import AgentService
from backend.trusted_hosts import HostAllowList, allowed_hosts
from core import sqlite_guard
from core.lab_service import LabService


# 全局服务实例
agent_service: AgentService = None


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    """应用生命周期管理"""
    global agent_service

    # 启动时初始化服务
    print("正在初始化服务...")
    agent_service = AgentService()
    await agent_service.initialize()
    print("服务初始化完成")

    yield

    # 关闭时清理
    print("正在关闭服务...")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if sqlite_guard.notice():
        logging.getLogger("backend").warning(sqlite_guard.notice())
    app.state.lab_service = LabService(demo=os.environ.get("TRACKER_DEMO_MODE") == "1")
    await app.state.lab_service.start()
    try:
        if os.environ.get("TRACKER_LAB_ONLY") == "1":
            yield
        elif os.environ.get("TRACKER_DEMO_MODE") == "1":
            async with features.background(app):
                yield
        else:
            async with application_lifespan(app), features.background(app):
                yield
    finally:
        await app.state.lab_service.stop()


app = FastAPI(
    title="交易助手 API",
    description="智能股票交易辅助系统 API",
    version="4.0.0",
    lifespan=lifespan
)

# Never echo accidentally submitted credentials in research validation errors.
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def validated_request_error(request, exc):
    if request.url.path.startswith("/api/lab/"):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()]})
    return await request_validation_exception_handler(request, exc)


# 配置 CORS
@app.middleware("http")
async def protect_demo_data(request, call_next):
    if (os.environ.get("TRACKER_DEMO_MODE") == "1"
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not (request.method == "POST" and (request.url.path == "/api/lab/runs"
                     or request.url.path == "/api/backtest/runs" or request.url.path.startswith("/api/backtest/runs/") and request.url.path.endswith(("/cancel", "/reflect"))
                     or request.url.path.startswith("/api/lab/runs/") and request.url.path.endswith(("/cancel", "/retry"))))):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Demo mode is read-only"})
    return await call_next(request)


# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The API has no authentication. Answer only for loopback names, the Compose
# service name and TRACKER_ALLOWED_HOSTS (read here, once, at startup), so a
# website cannot reach it through a DNS name pointed at this machine. Added
# last, which makes it the first middleware a request meets.
app.add_middleware(HostAllowList, allowed_hosts=allowed_hosts())

# 注册路由
app.include_router(lab.router)
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["持仓"])
app.include_router(market.router, prefix="/api/market", tags=["行情"])
app.include_router(memory.router, prefix="/api/memory", tags=["记忆"])
app.include_router(suggestions.router, prefix="/api/suggestions", tags=["建议"])
app.include_router(settings.router, prefix="/api/settings", tags=["设置"])
app.include_router(backtest.router, tags=["回测"])
app.include_router(ledger.router, prefix="/api/ledger", tags=["账本"])
app.include_router(portfolio_tracker.router, prefix="/api/tracker", tags=["投资组合跟踪"])
for _router, _prefix, _tags in features.routers():
    app.include_router(_router, prefix=_prefix, tags=_tags)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "交易助手 API",
        "version": "4.0.0",
        "status": "running",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "research_lab": app.state.lab_service.health() if getattr(app.state, "lab_service", None) else {"worker_alive": False},
        "services": {
            "agent": agent_service is not None,
            "llm": agent_service.llm_provider is not None if agent_service else False,
            "portfolio": agent_service.portfolio_provider is not None if agent_service else False,
            "memory": agent_service.memory_manager is not None if agent_service else False,
        }
    }


# Separately installed modules named in TRACKER_EXTENSIONS (docs/EXTENSIONS.md), after every built-in
# route (so none of those can be shadowed) and behind the same middleware. Nothing is imported when
# the variable is unset.
extensions.load()
extensions.mount(app)


def get_agent_service() -> AgentService:
    """获取 Agent 服务实例（供路由使用）"""
    return agent_service


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.environ.get("BACKEND_HOST", "127.0.0.1"),
        port=int(os.environ.get("BACKEND_PORT", "8000")),
        reload=True
    )
