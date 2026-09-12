"""Typed research API. No account mutations or arbitrary code execution."""
import asyncio
import os
from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from core.lab_models import Connection, MarketDatasetRequest, RunRequest, ScheduleRequest
from core.lab_service import LabService, safe_message
from core.lab_store import Conflict, encode

router = APIRouter(prefix="/api/lab", tags=["研究工作台"])


def get_service(request: Request) -> LabService:
    service = getattr(request.app.state, "lab_service", None)
    if service is None:
        raise HTTPException(503, "研究工作台尚未启动")
    return service


def writable(service):
    if service.demo:
        raise HTTPException(403, "演示模式仅允许合成数据实验")


def checked(callback):
    try:
        return callback()
    except KeyError:
        raise HTTPException(404, "未找到研究、数据或模型连接") from None
    except Conflict as exc:
        raise HTTPException(409, safe_message(exc)) from None
    except ValueError as exc:
        from pydantic import ValidationError
        if isinstance(exc, ValidationError):
            detail = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False))
        else:
            detail = safe_message(exc)
        raise HTTPException(422, detail) from None


@router.get("/capabilities")
async def capabilities(service=Depends(get_service)):
    return service.capabilities()


@router.get("/templates")
async def templates(service=Depends(get_service)):
    return service.templates()


@router.get("/datasets")
async def datasets(service=Depends(get_service)):
    return service.store.datasets()


@router.post("/datasets/import", status_code=201)
async def import_dataset(file: UploadFile = File(...), name: str = Form("导入的股票行情"), service=Depends(get_service)):
    writable(service)
    content = await file.read(8 * 1024 * 1024 + 1)
    return checked(lambda: service.import_data(content, name))


@router.get("/datasets/template.csv")
async def csv_template():
    return Response("date,symbol,open,high,low,close,volume\n2024-01-02,EXAMPLE,100,102,99,101,100000\n",
                    media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="research-bars-template.csv"'})


@router.post("/datasets/market", status_code=201)
async def market_dataset(request: MarketDatasetRequest, service=Depends(get_service)):
    writable(service)
    try:
        return await service.market_data(request.model_dump(mode="json"))
    except (ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(422, safe_message(exc) or "行情获取超时") from None


@router.get("/connections")
async def connections(service=Depends(get_service)):
    return service.store.connections()


@router.put("/connections")
async def save_connection(request: Connection, service=Depends(get_service)):
    writable(service)
    from providers.llm.research import provider_capabilities
    if request.provider not in {p["kind"] for p in provider_capabilities()}:
        raise HTTPException(422, "未注册的模型适配器")
    return service.store.save_connection(request.model_dump())


@router.post("/connections/{identifier}/check")
async def check_connection(identifier: str, service=Depends(get_service)):
    writable(service)
    connection = checked(lambda: service.store.connection(identifier))
    from providers.llm.research import probe_research_provider
    result = await probe_research_provider(connection)
    return {**result, "message": result.get("reason", "连接状态已检测")}


@router.post("/runs", status_code=202)
async def create_run(request: RunRequest, service=Depends(get_service)):
    return checked(lambda: service.submit(request.model_dump(mode="json")))


@router.get("/runs")
async def runs(service=Depends(get_service)):
    return service.store.runs()


@router.get("/runs/{identifier}")
async def run_detail(identifier: str, service=Depends(get_service)):
    return checked(lambda: service.public_run(identifier))


@router.get("/runs/{identifier}/result")
async def run_result(identifier: str, service=Depends(get_service)):
    run = checked(lambda: service.public_run(identifier))
    if run["status"] != "completed":
        raise HTTPException(409, "研究尚未完成")
    return run["result"]


@router.post("/runs/{identifier}/cancel")
async def cancel_run(identifier: str, service=Depends(get_service)):
    checked(lambda: service.store.cancel(identifier))
    return service.public_run(identifier)


@router.post("/runs/{identifier}/retry", status_code=202)
async def retry_run(identifier: str, service=Depends(get_service)):
    new_id = checked(lambda: service.store.retry(identifier))
    service._wake.set()
    return service.public_run(new_id)


@router.get("/runs/{identifier}/export")
async def export_run(identifier: str, service=Depends(get_service)):
    run = checked(lambda: service.public_run(identifier))
    # A private research export contains the selected research data/goal, never account state.
    run["export_notice"] = "研究导出可能包含您选择的股票、研究问题和数据，请自行决定分享范围。"
    return Response(encode(run), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{run["id"]}.json"'})


@router.get("/schedules")
async def schedules(service=Depends(get_service)):
    return service.store.schedules()


@router.post("/schedules")
async def save_schedule(request: ScheduleRequest, service=Depends(get_service)):
    writable(service)
    config = request.model_dump(mode="json")
    prepared = checked(lambda: service.prepare_request(config["request"]))
    prepared.pop("client_request_id", None)
    config["request"] = prepared
    return checked(lambda: service.store.schedule(config))


@router.get("/tools")
async def tools(service=Depends(get_service)):
    from core.lab_tools import tool_definitions
    return tool_definitions()
