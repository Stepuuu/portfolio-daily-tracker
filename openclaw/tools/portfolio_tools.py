"""
OpenClaw 🦞 Agent Tools for Portfolio Tracker

These tools integrate with the portfolio engine to enable
conversational portfolio management via AI agent.

Usage in your agent service:
    from openclaw.tools.portfolio_tools import get_tools
    tools = get_tools(portfolio_dir="/path/to/portfolio")
"""

import json, os, glob
from pathlib import Path
from typing import Dict, Any, List, Optional


def get_tracker_snapshot_tool(portfolio_dir: str = "") -> Dict:
    """Create a tool definition for reading portfolio snapshots.
    
    Returns a tool spec compatible with function-calling LLM APIs.
    """
    if not portfolio_dir:
        portfolio_dir = os.getenv("PORTFOLIO_DIR", "")

    async def get_tracker_snapshot(date: str = "") -> Dict:
        """Get detailed portfolio snapshot with groups, positions, leverage, quant metrics."""
        tracker_dir = Path(portfolio_dir) if portfolio_dir else Path("portfolio")
        snapshots_dir = tracker_dir / "snapshots"
        history_file = tracker_dir / "history.csv"

        # Load snapshot
        snapshot = None
        if date and snapshots_dir.exists():
            snap_file = snapshots_dir / f"{date}.json"
            if snap_file.exists():
                snapshot = json.loads(snap_file.read_text())
        elif snapshots_dir.exists():
            snap_files = sorted(snapshots_dir.glob("*.json"), reverse=True)
            if snap_files:
                snapshot = json.loads(snap_files[0].read_text())

        if not snapshot:
            return {"error": "No snapshot found"}

        result: Dict[str, Any] = {
            "date": snapshot.get("date"),
            "summary": snapshot.get("summary", {}),
            "groups": {},
        }

        for gname, g in snapshot.get("groups", {}).items():
            positions = [{
                "name": p["name"], "ticker": p["ticker"],
                "quantity": p["quantity"], "cost_price": p["cost_price"],
                "current_price": p["current_price"],
                "market_value_cny": p["market_value_cny"],
                "profit_cny": p["profit_cny"], "profit_pct": p["profit_pct"],
            } for p in g.get("positions", [])]

            cash = g.get("cash", 0)
            fund = g.get("fund", 0)
            pos_val = g.get("positions_value", 0)
            has_margin = cash < 0

            result["groups"][gname] = {
                "total_value": g["total_value"],
                "positions_value": pos_val,
                "fund": fund, "cash": cash,
                "profit": g.get("profit"),
                "return_pct": g.get("return_pct"),
                "has_margin": has_margin,
                "margin_amount": abs(cash) if has_margin else 0,
                "leverage_ratio": round((pos_val + fund) / g["total_value"], 2) if g["total_value"] != 0 and has_margin else 1.0,
                "positions": positions,
            }

        # Load recent history
        if history_file.exists():
            import csv
            with open(history_file) as f:
                rows = list(csv.DictReader(f))
            recent = rows[-10:]
            recent.reverse()
            result["recent_history"] = [{
                "date": r["date"],
                "total_value": float(r["total_value"]),
                "daily_change": float(r["daily_change"]),
                "daily_change_pct": float(r["daily_change_pct"]),
            } for r in recent]

        return result

    return {
        "name": "get_tracker_snapshot",
        "description": "获取投资组合跟踪器快照，包含分组持仓、融资杠杆、量化指标",
        "function": get_tracker_snapshot,
        "parameters": {
            "date": {"type": "string", "description": "日期 YYYY-MM-DD，留空获取最新", "required": False},
        }
    }


def get_update_holdings_tool(scripts_dir: str = "") -> Dict:
    """Create a tool definition for updating daily holdings."""

    async def update_holdings(date: str, changes_text: str) -> Dict:
        """Update holdings based on natural language description."""
        import sys
        if scripts_dir:
            sys.path.insert(0, scripts_dir)
        
        from portfolio_daily_update import (
            clone_holdings, load_holdings, save_holdings,
            parse_and_apply_changes
        )

        path, is_new = clone_holdings(date)
        if not path:
            return {"error": "Cannot create holdings file", "success": False}

        holdings = load_holdings(date)
        if not holdings:
            return {"error": "Cannot load holdings", "success": False}

        changes = parse_and_apply_changes(holdings, changes_text)
        has_real_changes = any(c["action"] not in ("no_change", "unknown") for c in changes)

        if has_real_changes:
            save_holdings(holdings, date)

        return {
            "success": True,
            "date": date,
            "changes_applied": [c["description"] for c in changes],
            "holdings_updated": has_real_changes,
        }

    return {
        "name": "update_holdings",
        "description": "更新每日持仓，接受自然语言描述（如'卖了500股xxx''现金变为xxx''未变化'）",
        "function": update_holdings,
        "parameters": {
            "date": {"type": "string", "description": "日期 YYYY-MM-DD", "required": True},
            "changes_text": {"type": "string", "description": "变更描述", "required": True},
        }
    }


def get_pipeline_tool(scripts_dir: str = "") -> Dict:
    """Create a tool definition for running the full portfolio pipeline."""

    async def run_portfolio_pipeline(date: str, send_report: bool = True) -> Dict:
        """Run full pipeline: snapshot → report → push → sync."""
        import sys
        if scripts_dir:
            sys.path.insert(0, scripts_dir)

        from portfolio_daily_update import clone_holdings, run_pipeline

        clone_holdings(date)
        success = run_pipeline(date, send_report=send_report)

        return {
            "success": success,
            "date": date,
            "message": f"{'✅' if success else '❌'} {date} pipeline {'completed' if success else 'failed'}",
        }

    return {
        "name": "run_portfolio_pipeline",
        "description": "运行完整管道：快照→报告→推送→同步Dashboard",
        "function": run_portfolio_pipeline,
        "parameters": {
            "date": {"type": "string", "description": "日期 YYYY-MM-DD", "required": True},
            "send_report": {"type": "boolean", "description": "是否推送飞书", "required": False},
        }
    }


def get_all_tools(portfolio_dir: str = "", scripts_dir: str = "") -> List[Dict]:
    """Get all portfolio tools as a list."""
    return [
        get_tracker_snapshot_tool(portfolio_dir),
        get_update_holdings_tool(scripts_dir),
        get_pipeline_tool(scripts_dir),
    ]
