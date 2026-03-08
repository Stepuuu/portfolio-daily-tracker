"""
投资组合跟踪器 API — 读取 portfolio/ 目录的快照和持仓数据
"""
import json, os, glob
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", str(Path(__file__).parent.parent.parent.parent / "engine" / "portfolio"))


def _read_json(path: str):
    """Read and parse a JSON file."""
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


@router.get("/dates")
async def list_dates():
    """列出所有有快照的日期"""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "*.json")), reverse=True)
    dates = [os.path.basename(f).replace(".json", "") for f in files]
    return {"dates": dates, "count": len(dates)}


@router.get("/snapshot")
async def get_snapshot(date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认最新")):
    """获取指定日期的快照"""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")

    if date:
        path = os.path.join(snap_dir, f"{date}.json")
    else:
        files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
        if not files:
            raise HTTPException(status_code=404, detail="没有快照数据")
        path = files[-1]

    data = _read_json(path)
    if not data:
        raise HTTPException(status_code=404, detail=f"快照不存在: {date}")
    return data


@router.get("/holdings")
async def get_holdings(date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认最新")):
    """获取指定日期的持仓"""
    holdings_dir = os.path.join(PORTFOLIO_DIR, "holdings")

    if date:
        path = os.path.join(holdings_dir, f"{date}.json")
    else:
        files = sorted(glob.glob(os.path.join(holdings_dir, "*.json")))
        if not files:
            raise HTTPException(status_code=404, detail="没有持仓数据")
        path = files[-1]

    data = _read_json(path)
    if not data:
        raise HTTPException(status_code=404, detail=f"持仓不存在: {date}")
    return data


@router.get("/history")
async def get_history(limit: int = Query(365, ge=1, le=1000)):
    """获取历史时序数据（从 history.csv 读取）"""
    csv_path = os.path.join(PORTFOLIO_DIR, "history.csv")
    if not os.path.exists(csv_path):
        # Fall back to reading all snapshots
        snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
        files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
        history = []
        for f in files[-limit:]:
            data = _read_json(f)
            if data and "summary" in data:
                s = data["summary"]
                history.append({
                    "date": data["date"],
                    "total_value": s.get("total_value", 0),
                    "total_cost": s.get("total_cost", 0),
                    "total_profit": s.get("total_profit", 0),
                    "return_pct": s.get("total_return_pct", 0),
                    "daily_change": s.get("daily_change", 0),
                    "daily_change_pct": s.get("daily_change_pct", 0),
                    "max_drawdown_pct": s.get("max_drawdown_pct", 0),
                })
        return {"history": history, "count": len(history)}

    # Parse CSV
    history = []
    with open(csv_path, "r") as f:
        header = f.readline().strip().split(",")
        for line in f:
            vals = line.strip().split(",")
            if len(vals) == len(header):
                row = {}
                for h, v in zip(header, vals):
                    try:
                        row[h] = float(v) if h != "date" else v
                    except ValueError:
                        row[h] = v
                history.append(row)

    return {"history": history[-limit:], "count": len(history)}


@router.get("/config")
async def get_config():
    """获取配置信息（组别定义等）"""
    config = _read_json(os.path.join(PORTFOLIO_DIR, "config.json"))
    if not config:
        raise HTTPException(status_code=404, detail="配置文件不存在")
    # 不返回敏感信息
    safe_config = {
        "groups": config.get("groups", {}),
        "currency_map": config.get("currency_map", {}),
    }
    return safe_config


@router.get("/summary")
async def get_summary():
    """获取最新的投资组合摘要（用于 Dashboard 展示）"""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
    if not files:
        return {
            "date": None,
            "total_value": 0,
            "total_cost": 0,
            "total_profit": 0,
            "total_return_pct": 0,
            "daily_change": 0,
            "daily_change_pct": 0,
            "groups": {},
        }

    latest = _read_json(files[-1])
    if not latest:
        raise HTTPException(status_code=500, detail="无法读取最新快照")

    s = latest.get("summary", {})
    groups_summary = {}
    for gname, gdata in latest.get("groups", {}).items():
        groups_summary[gname] = {
            "total_value": gdata.get("total_value", 0),
            "cost_basis": gdata.get("cost_basis", 0),
            "profit": gdata.get("profit", 0),
            "return_pct": gdata.get("return_pct", 0),
            "positions_count": len(gdata.get("positions", [])),
        }

    return {
        "date": latest.get("date"),
        "total_value": s.get("total_value", 0),
        "total_cost": s.get("total_cost", 0),
        "total_profit": s.get("total_profit", 0),
        "total_return_pct": s.get("total_return_pct", 0),
        "daily_change": s.get("daily_change", 0),
        "daily_change_pct": s.get("daily_change_pct", 0),
        "max_drawdown_pct": s.get("max_drawdown_pct", 0),
        "month_return_pct": s.get("month_return_pct", 0),
        "groups": groups_summary,
    }


@router.get("/group/{group_name}")
async def get_group_detail(group_name: str, date: Optional[str] = Query(None)):
    """获取特定组别的详细数据"""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")

    if date:
        path = os.path.join(snap_dir, f"{date}.json")
    else:
        files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
        if not files:
            raise HTTPException(status_code=404, detail="没有快照数据")
        path = files[-1]

    data = _read_json(path)
    if not data:
        raise HTTPException(status_code=404, detail="快照不存在")

    groups = data.get("groups", {})
    if group_name not in groups:
        raise HTTPException(status_code=404, detail=f"组别不存在: {group_name}")

    return {
        "date": data["date"],
        "group_name": group_name,
        "fx_rates": data.get("fx_rates", {}),
        **groups[group_name]
    }
