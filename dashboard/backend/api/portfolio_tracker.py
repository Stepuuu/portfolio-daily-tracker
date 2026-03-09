"""
投资组合跟踪器 API — 读取 portfolio/ 目录的快照和持仓数据
"""
import json, os, glob
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

PORTFOLIO_DIR = os.environ.get(
    "PORTFOLIO_DIR",
    str(Path(__file__).resolve().parent.parent.parent.parent / "engine" / "portfolio"),
)


def _read_json(path: str):
    """Read and parse a JSON file."""
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


@router.get("/dates")
async def list_dates():
    """列出所有有数据的日期（快照文件 + history.csv 的并集）"""
    dates_set = set()

    # From snapshot files
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    for f in glob.glob(os.path.join(snap_dir, "*.json")):
        dates_set.add(os.path.basename(f).replace(".json", ""))

    # From history.csv
    csv_path = os.path.join(PORTFOLIO_DIR, "history.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            header = f.readline().strip().split(",")
            date_idx = header.index("date") if "date" in header else 0
            for line in f:
                parts = line.strip().split(",")
                if parts and len(parts) > date_idx:
                    dates_set.add(parts[date_idx])

    dates = sorted(dates_set, reverse=True)
    return {"dates": dates, "count": len(dates)}


def _build_synthetic_snapshot(target_date: str) -> Optional[dict]:
    """Build a synthetic snapshot from CSV history + closest real snapshot when JSON is missing."""
    csv_path = os.path.join(PORTFOLIO_DIR, "history.csv")
    if not os.path.exists(csv_path):
        return None

    # Parse CSV to find the target date row
    rows = []
    with open(csv_path, "r") as f:
        header = f.readline().strip().split(",")
        for line in f:
            vals = line.strip().split(",")
            if len(vals) >= 8:
                row = {}
                for h, v in zip(header, vals):
                    try:
                        row[h] = float(v) if h != "date" else v
                    except ValueError:
                        row[h] = v
                rows.append(row)

    target_row = None
    target_idx = -1
    for i, r in enumerate(rows):
        if r.get("date") == target_date:
            target_row = r
            target_idx = i
            break
    if target_row is None:
        return None

    # Compute capital_change / market_daily_change if not in CSV
    if "capital_change" not in target_row or target_row.get("capital_change") == "":
        prev_cost = rows[target_idx - 1]["total_cost"] if target_idx > 0 else target_row["total_cost"]
        cap_change = target_row["total_cost"] - prev_cost
        mkt_change = target_row["daily_change"] - cap_change
        prev_val = rows[target_idx - 1]["total_value"] if target_idx > 0 else target_row["total_value"]
        mkt_pct = (mkt_change / prev_val * 100) if prev_val else 0
        target_row["capital_change"] = round(cap_change, 2)
        target_row["market_daily_change"] = round(mkt_change, 2)
        target_row["market_daily_change_pct"] = round(mkt_pct, 2)

    # Compute month metrics from CSV rows
    month_prefix = target_date[:7]  # "YYYY-MM"
    month_rows = [r for r in rows if r["date"].startswith(month_prefix) and r["date"] <= target_date]
    if month_rows:
        first_month = month_rows[0]
        first_month_idx = rows.index(first_month)
        month_start_value = rows[first_month_idx - 1]["total_value"] if first_month_idx > 0 else first_month["total_value"]
        month_start_cost = rows[first_month_idx - 1]["total_cost"] if first_month_idx > 0 else first_month["total_cost"]
        month_change = target_row["total_value"] - month_start_value
        month_capital = target_row["total_cost"] - month_start_cost
        month_market_change = month_change - month_capital
        month_return_pct = (month_market_change / month_start_value * 100) if month_start_value else 0
    else:
        month_start_value = target_row["total_value"]
        month_change = 0
        month_market_change = 0
        month_return_pct = 0

    prev_date = rows[target_idx - 1]["date"] if target_idx > 0 else None
    prev_value = rows[target_idx - 1]["total_value"] if target_idx > 0 else target_row["total_value"]

    summary = {
        "total_value": target_row["total_value"],
        "total_cost": target_row["total_cost"],
        "total_profit": target_row["total_profit"],
        "total_return_pct": target_row["return_pct"],
        "prev_date": prev_date,
        "prev_total_value": prev_value,
        "daily_change": target_row["daily_change"],
        "daily_change_pct": target_row["daily_change_pct"],
        "capital_change": target_row.get("capital_change", 0),
        "market_daily_change": target_row.get("market_daily_change", target_row["daily_change"]),
        "market_daily_change_pct": target_row.get("market_daily_change_pct", target_row["daily_change_pct"]),
        "max_drawdown_pct": target_row.get("max_drawdown_pct", 0),
        "month_start_value": month_start_value,
        "month_change": month_change,
        "month_market_change": month_market_change,
        "month_return_pct": round(month_return_pct, 2),
        "sharpe_ratio": 0,
        "volatility_annual": 0,
        "win_rate": 0,
        "avg_win_pct": 0,
        "avg_loss_pct": 0,
        "profit_loss_ratio": 0,
        "trading_days": target_idx + 1,
    }

    # Try loading the closest snapshot's group data (for positions display)
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    snap_files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
    groups = {}
    fx_rates = {}
    closest = None
    for sf in reversed(snap_files):
        sd = os.path.basename(sf).replace(".json", "")
        if sd <= target_date:
            closest = sf
            break
    if closest:
        cs = _read_json(closest)
        if cs:
            groups = cs.get("groups", {})
            fx_rates = cs.get("fx_rates", {})

    return {
        "date": target_date,
        "generated_at": f"{target_date}T00:00:00 (synthetic)",
        "fx_rates": fx_rates,
        "groups": groups,
        "summary": summary,
        "synthetic": True,
    }


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
    if not data and date:
        # Snapshot file missing — build synthetic from CSV + closest snapshot
        data = _build_synthetic_snapshot(date)
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
            if len(vals) >= 8:  # tolerate old 8-col and new 11-col rows
                row = {}
                for h, v in zip(header, vals):
                    try:
                        row[h] = float(v) if h != "date" else v
                    except ValueError:
                        row[h] = v
                history.append(row)

    # Compute market_daily_change from CSV cost differences (reliable, no snapshot files needed)
    # capital_change[i] = cost[i] - cost[i-1]; market_change[i] = daily_change[i] - capital_change[i]
    for i, row in enumerate(history):
        # If CSV already has these columns (new format), use them directly
        if "market_daily_change" in row and row["market_daily_change"] != 0 or (
            "capital_change" in row and row.get("capital_change", 0) != 0
        ):
            continue  # already set from CSV columns
        prev_cost = history[i - 1]["total_cost"] if i > 0 else row["total_cost"]
        cap_change = row["total_cost"] - prev_cost
        mkt_change = row["daily_change"] - cap_change
        prev_value = history[i - 1]["total_value"] if i > 0 else row["total_value"]
        mkt_change_pct = (mkt_change / prev_value * 100) if prev_value != 0 else 0
        row["capital_change"] = round(cap_change, 2)
        row["market_daily_change"] = round(mkt_change, 2)
        row["market_daily_change_pct"] = round(mkt_change_pct, 2)

    # Override with snapshot data where available (higher fidelity for cash-flow capital detection)
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    for f in glob.glob(os.path.join(snap_dir, "*.json")):
        s = _read_json(f)
        if s and "summary" in s:
            sm = s["summary"]
            if "market_daily_change" in sm:
                snap_date = s.get("date", "")
                for row in history:
                    if row["date"] == snap_date:
                        row["market_daily_change"] = sm["market_daily_change"]
                        row["market_daily_change_pct"] = sm.get("market_daily_change_pct", 0)
                        row["capital_change"] = sm.get("capital_change", 0)
                        break

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
        "market_daily_change": s.get("market_daily_change", s.get("daily_change", 0)),
        "market_daily_change_pct": s.get("market_daily_change_pct", s.get("daily_change_pct", 0)),
        "capital_change": s.get("capital_change", 0),
        "max_drawdown_pct": s.get("max_drawdown_pct", 0),
        "month_change": s.get("month_change", 0),
        "month_market_change": s.get("month_market_change", s.get("month_change", 0)),
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
