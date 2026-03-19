"""
Agent 工具定义
为对话 Agent 提供函数调用能力
"""
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass
from enum import Enum


def _normalize_market(market: str) -> str:
    """将 LLM 可能传入的各种 market 值统一为正确的 Market enum 值"""
    mapping = {
        "hk": "hk_stock",
        "hk_stock": "hk_stock",
        "hong_kong": "hk_stock",
        "us": "us_stock",
        "us_stock": "us_stock",
        "a_share": "a_share",
        "a": "a_share",
        "cn": "a_share",
    }
    return mapping.get(market.lower().strip(), market)


class ToolParameterType(Enum):
    """工具参数类型"""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: ToolParameterType
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    items: Optional[Dict] = None  # 用于 array 类型


@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    parameters: List[ToolParameter]
    function: Callable


def create_claude_tool_schema(tool: Tool) -> Dict[str, Any]:
    """
    将工具定义转换为 Claude API 的 tool schema 格式

    参考: https://docs.anthropic.com/claude/docs/tool-use
    """
    properties = {}
    required = []

    for param in tool.parameters:
        prop = {
            "type": param.type.value,
            "description": param.description
        }

        if param.enum:
            prop["enum"] = param.enum

        if param.items:
            prop["items"] = param.items

        properties[param.name] = prop

        if param.required:
            required.append(param.name)

    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required
        }
    }


# ==================== 工具定义 ====================

def get_stock_quote_tool(market_provider) -> Tool:
    """获取股票行情工具"""
    async def get_stock_quote(symbol: str, market: str = "a_share") -> Dict:
        """
        获取指定股票的实时行情

        Args:
            symbol: 股票代码（如 600519, 000001, 9988, GOOGL）
            market: 市场类型（a_share/hk_stock/us_stock）

        Returns:
            包含股票行情数据的字典
        """
        from core.models import Market
        market = _normalize_market(market)
        quote = await market_provider.get_quote(symbol, Market(market))

        if not quote:
            return {"error": f"未找到股票 {symbol}"}

        return {
            "symbol": quote.stock.symbol,
            "name": quote.stock.name,
            "market": quote.stock.market.value,
            "price": quote.price,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "prev_close": quote.prev_close,
            "volume": quote.volume,
            "change": quote.price - quote.prev_close if quote.prev_close else 0,
            "change_pct": (quote.price - quote.prev_close) / quote.prev_close * 100 if quote.prev_close else 0
        }

    return Tool(
        name="get_stock_quote",
        description="获取指定股票的实时行情数据，包括最新价、涨跌幅、成交量等。获取股票当前价格时必须调用此工具，严禁自行编造价格数据",
        parameters=[
            ToolParameter(
                name="symbol",
                type=ToolParameterType.STRING,
                description="股票代码，如 600519（贵州茅台）、000001（上证指数）、9988（阿里巴巴-HK）、GOOGL（谷歌）"
            ),
            ToolParameter(
                name="market",
                type=ToolParameterType.STRING,
                description="市场类型: a_share(沪深A股), hk_stock(港股), us_stock(美股)",
                required=False,
                enum=["a_share", "hk_stock", "us_stock"]
            )
        ],
        function=get_stock_quote
    )


def get_portfolio_tool(portfolio_provider) -> Tool:
    """获取持仓信息工具"""
    async def get_portfolio() -> Dict:
        """
        获取用户当前的持仓信息

        Returns:
            包含持仓列表、总资产、总盈亏等信息的字典
        """
        portfolio = await portfolio_provider.get_portfolio()

        return {
            "positions": [
                {
                    "symbol": p.stock.symbol,
                    "name": p.stock.name,
                    "market": p.stock.market.value,
                    "quantity": p.quantity,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "profit": p.profit,
                    "profit_pct": p.profit_pct
                }
                for p in portfolio.positions
            ],
            "cash": portfolio.cash,
            "total_market_value": portfolio.total_market_value,
            "total_assets": portfolio.total_assets,
            "total_profit": portfolio.total_profit
        }

    return Tool(
        name="get_portfolio",
        description="获取用户当前的持仓信息，包括持有股票、数量、成本价、当前价、盈亏等",
        parameters=[],
        function=get_portfolio
    )


def get_market_indices_tool(market_provider) -> Tool:
    """获取主要市场指数工具"""
    async def get_market_indices() -> Dict:
        """
        获取主要市场指数的实时行情

        Returns:
            包含上证指数、深证成指、创业板指等指数行情的字典
        """
        from core.models import Market

        indices = [
            ("000001", "上证指数"),
            ("399001", "深证成指"),
            ("399006", "创业板指"),
            ("000300", "沪深300"),
        ]

        results = []
        for symbol, name in indices:
            try:
                quote = await market_provider.get_quote(symbol, Market.A_SHARE)
                if quote:
                    results.append({
                        "symbol": quote.stock.symbol,
                        "name": quote.stock.name,
                        "price": quote.price,
                        "change": quote.price - quote.prev_close if quote.prev_close else 0,
                        "change_pct": (quote.price - quote.prev_close) / quote.prev_close * 100 if quote.prev_close else 0
                    })
            except:
                continue

        return {"indices": results}

    return Tool(
        name="get_market_indices",
        description="获取主要市场指数的实时行情，包括上证指数、深证成指、创业板指、沪深300等",
        parameters=[],
        function=get_market_indices
    )


def get_market_news_tool(news_provider) -> Tool:
    """获取市场新闻工具"""
    async def get_market_news(keyword: str = "", category: str = "all", limit: int = 6) -> Dict:
        """
        获取最新市场新闻，或按关键词过滤。

        Args:
            keyword: 搜索关键词，留空则获取最新新闻
            category: 新闻分类（market/world/all）
            limit: 返回条数
        """
        category = (category or "all").strip().lower()
        if category not in {"market", "world", "all"}:
            category = "all"

        limit = max(1, min(int(limit or 6), 12))

        if keyword and keyword.strip():
            items = await news_provider.search_news(keyword.strip(), limit=limit, category=category)
        else:
            items = await news_provider.get_latest_news(category=category, limit=limit)

        return {
            "keyword": keyword,
            "category": category,
            "count": len(items),
            "news": items,
        }

    return Tool(
        name="get_market_news",
        description="获取近期市场新闻和国际宏观新闻。用户询问为什么市场涨跌、板块为何异动、隔夜发生了什么、宏观/地缘事件影响时，应调用此工具获取真实新闻标题后再分析。",
        parameters=[
            ToolParameter(
                name="keyword",
                type=ToolParameterType.STRING,
                description="关键词，可留空表示获取最新新闻；例如 fed、oil、tariff、nvidia、ai、middle east",
                required=False,
            ),
            ToolParameter(
                name="category",
                type=ToolParameterType.STRING,
                description="新闻分类：market(财经市场), world(国际/地缘), all(全部)",
                required=False,
                enum=["market", "world", "all"],
            ),
            ToolParameter(
                name="limit",
                type=ToolParameterType.NUMBER,
                description="返回新闻条数，默认 6，最大 12",
                required=False,
            ),
        ],
        function=get_market_news,
    )


def compare_stocks_tool(market_provider) -> Tool:
    """比较多只股票工具"""
    async def compare_stocks(symbols: List[str], market: str = "a_share") -> Dict:
        """
        比较多只股票的行情表现

        Args:
            symbols: 股票代码列表
            market: 市场类型

        Returns:
            包含各股票对比数据的字典
        """
        from core.models import Market
        market = _normalize_market(market)

        results = []
        for symbol in symbols:
            try:
                quote = await market_provider.get_quote(symbol, Market(market))
                if quote:
                    results.append({
                        "symbol": quote.stock.symbol,
                        "name": quote.stock.name,
                        "price": quote.price,
                        "change_pct": (quote.price - quote.prev_close) / quote.prev_close * 100 if quote.prev_close else 0,
                        "volume": quote.volume
                    })
            except:
                continue

        return {"comparison": results}

    return Tool(
        name="compare_stocks",
        description="比较多只股票的行情表现，用于板块分析或同类股票对比",
        parameters=[
            ToolParameter(
                name="symbols",
                type=ToolParameterType.ARRAY,
                description="要比较的股票代码列表",
                items={"type": "string"}
            ),
            ToolParameter(
                name="market",
                type=ToolParameterType.STRING,
                description="市场类型: a_share(沪深A股), hk_stock(港股), us_stock(美股)",
                required=False,
                enum=["a_share", "hk_stock", "us_stock"]
            )
        ],
        function=compare_stocks
    )


def get_tracker_snapshot_tool() -> Tool:
    """获取投资组合跟踪器的详细快照数据"""
    async def get_tracker_snapshot(date: str = "") -> Dict:
        """
        获取投资组合跟踪器的快照数据，包含分组持仓详情、量化指标、杠杆信息等。

        Args:
            date: 日期（格式 YYYY-MM-DD），留空则获取最新

        Returns:
            包含分组持仓、汇总指标、量化数据的详细字典
        """
        import json
        from pathlib import Path

        snapshot_dir = Path(__file__).parent.parent / "data"
        portfolio_file = snapshot_dir / "portfolio.json"

        # Also check the dedicated portfolio tracker data
        tracker_dir = Path(os.environ.get("PORTFOLIO_DIR", str(Path(__file__).parent.parent.parent / "engine" / "portfolio")))
        snapshots_dir = tracker_dir / "snapshots"
        history_file = tracker_dir / "history.csv"

        # Load snapshot
        snapshot = None
        if date and snapshots_dir.exists():
            snap_file = snapshots_dir / f"{date}.json"
            if snap_file.exists():
                snapshot = json.loads(snap_file.read_text())
        elif portfolio_file.exists():
            snapshot = json.loads(portfolio_file.read_text())
        elif snapshots_dir.exists():
            # Get latest snapshot
            snap_files = sorted(snapshots_dir.glob("*.json"), reverse=True)
            if snap_files:
                snapshot = json.loads(snap_files[0].read_text())

        if not snapshot:
            return {"error": "未找到快照数据"}

        # Build rich summary
        result: Dict[str, Any] = {
            "date": snapshot.get("date"),
            "summary": snapshot.get("summary", {}),
            "groups": {},
        }

        # Per-group detail with leverage info
        for gname, g in snapshot.get("groups", {}).items():
            positions = []
            for p in g.get("positions", []):
                positions.append({
                    "name": p["name"],
                    "ticker": p["ticker"],
                    "quantity": p["quantity"],
                    "cost_price": p["cost_price"],
                    "current_price": p["current_price"],
                    "currency": p.get("currency", "CNY"),
                    "market_value_cny": p["market_value_cny"],
                    "profit_cny": p["profit_cny"],
                    "profit_pct": p["profit_pct"],
                })

            cash = g.get("cash", 0)
            fund = g.get("fund", 0)
            pos_val = g.get("positions_value", 0)
            has_margin = cash < 0
            margin = abs(cash) if has_margin else 0
            gross_asset = pos_val + fund
            leverage = gross_asset / g["total_value"] if g["total_value"] != 0 and has_margin else 1.0

            result["groups"][gname] = {
                "cost_basis": g.get("cost_basis"),
                "total_value": g["total_value"],
                "positions_value": pos_val,
                "fund": fund,
                "cash": cash,
                "profit": g.get("profit"),
                "return_pct": g.get("return_pct"),
                "has_margin": has_margin,
                "margin_amount": margin,
                "leverage_ratio": round(leverage, 2),
                "positions": positions,
            }

        # Load recent history (last 10 rows) for context
        if history_file.exists():
            import csv
            rows = []
            with open(history_file) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            recent = rows[-10:] if len(rows) > 10 else rows
            recent.reverse()
            result["recent_history"] = [
                {
                    "date": r["date"],
                    "total_value": float(r["total_value"]),
                    "total_cost": float(r["total_cost"]),
                    "daily_change": float(r["daily_change"]),
                    "daily_change_pct": float(r["daily_change_pct"]),
                    "return_pct": float(r["return_pct"]),
                }
                for r in recent
            ]

        return result

    return Tool(
        name="get_tracker_snapshot",
        description="获取投资组合跟踪器的详细快照，包含分组持仓（进攻/稳健）、每个持仓的成本价/现价/盈亏、融资杠杆信息、量化指标（夏普比率/波动率/胜率）、近期历史净值。当用户询问持仓详情、账户表现、杠杆率、收益率等问题时调用此工具",
        parameters=[
            ToolParameter(
                name="date",
                type=ToolParameterType.STRING,
                description="查询日期（格式 YYYY-MM-DD），留空获取最新数据",
                required=False,
            )
        ],
        function=get_tracker_snapshot,
    )


def update_holdings_tool() -> Tool:
    """更新每日持仓工具 — 支持自然语言描述持仓变化"""
    async def update_holdings(date: str, changes_text: str) -> Dict:
        """
        根据用户自然语言描述更新当日持仓文件。

        Args:
            date: 日期（格式 YYYY-MM-DD）
            changes_text: 变更描述，如 "现金变为5000, 卖了500股药明康德" 或 "未变化"

        Returns:
            更新结果，包含应用的变更列表
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "engine" / "scripts"))
        
        from portfolio_daily_update import (
            clone_holdings, load_holdings, save_holdings,
            parse_and_apply_changes
        )

        # Ensure today's holdings exist
        path, is_new = clone_holdings(date)
        if not path:
            return {"error": "无法创建今日持仓文件", "success": False}

        holdings = load_holdings(date)
        if not holdings:
            return {"error": "无法加载今日持仓", "success": False}

        # Parse and apply changes
        changes = parse_and_apply_changes(holdings, changes_text)

        changes_desc = [c["description"] for c in changes]
        has_real_changes = any(c["action"] not in ("no_change", "unknown") for c in changes)
        has_unknown = any(c["action"] == "unknown" for c in changes)

        if has_real_changes:
            save_holdings(holdings, date)

        return {
            "success": True,
            "date": date,
            "changes_applied": changes_desc,
            "holdings_updated": has_real_changes,
            "has_unrecognized": has_unknown,
            "message": (
                f"已更新 {date} 持仓: {'; '.join(changes_desc)}"
                if has_real_changes
                else f"持仓未变化" if any(c["action"] == "no_change" for c in changes)
                else f"未识别变更: {'; '.join(changes_desc)}"
            ),
            "next_step": "调用 run_portfolio_pipeline 生成快照和日报" if not has_unknown else "请重新描述未识别的变更"
        }

    return Tool(
        name="update_holdings",
        description="更新投资组合每日持仓。接受自然语言描述的持仓变化（如'卖了500股药明康德''现金变为5000''基金变为16万''持仓未变化'）。更新完成后应调用 run_portfolio_pipeline 生成快照和日报。",
        parameters=[
            ToolParameter(
                name="date",
                type=ToolParameterType.STRING,
                description="日期（格式 YYYY-MM-DD），如 2026-03-08",
            ),
            ToolParameter(
                name="changes_text",
                type=ToolParameterType.STRING,
                description="持仓变化的自然语言描述。支持: '未变化''卖了500股xxx''现金变为xxx''基金变为xxx''买了1000股xxx' 等，多条变更用逗号分隔",
            ),
        ],
        function=update_holdings,
    )


def run_portfolio_pipeline_tool() -> Tool:
    """运行投资组合完整管道 — 快照+报告+推送+同步QR"""
    async def run_portfolio_pipeline(date: str, send_report: bool = True) -> Dict:
        """
        运行完整的投资组合管道：生成快照 → 报告 → 推送飞书 → 同步 QR Dashboard。

        Args:
            date: 日期（格式 YYYY-MM-DD）
            send_report: 是否推送飞书日报

        Returns:
            管道执行结果
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "engine" / "scripts"))
        
        from portfolio_daily_update import clone_holdings, run_pipeline

        # Ensure holdings exist
        clone_holdings(date)

        # Run full pipeline
        success = run_pipeline(date, send_report=send_report)

        if success:
            # Read the generated snapshot for summary
            from pathlib import Path
            snap_file = Path(os.environ.get("PORTFOLIO_DIR", str(Path(__file__).parent.parent.parent / "engine" / "portfolio"))) / "snapshots" / f"{date}.json"
            summary = {}
            if snap_file.exists():
                import json
                snap = json.loads(snap_file.read_text())
                s = snap.get("summary", {})
                summary = {
                    "total_value": s.get("total_value"),
                    "total_profit": s.get("total_profit"),
                    "daily_change": s.get("daily_change"),
                    "daily_change_pct": s.get("daily_change_pct"),
                    "sharpe_ratio": s.get("sharpe_ratio"),
                }

            return {
                "success": True,
                "date": date,
                "message": f"✅ {date} 管道完成：快照已生成，报告{'已推送飞书' if send_report else '已生成'}，QR Dashboard 已同步",
                "summary": summary,
            }
        else:
            return {
                "success": False,
                "date": date,
                "message": f"❌ {date} 管道执行失败，请检查日志",
            }

    return Tool(
        name="run_portfolio_pipeline",
        description="运行投资组合完整管道：获取价格 → 生成快照 → 生成报告 → 推送飞书日报 → 同步QR Dashboard。通常在 update_holdings 之后调用。",
        parameters=[
            ToolParameter(
                name="date",
                type=ToolParameterType.STRING,
                description="日期（格式 YYYY-MM-DD）",
            ),
            ToolParameter(
                name="send_report",
                type=ToolParameterType.BOOLEAN,
                description="是否推送飞书日报，默认 true",
                required=False,
            ),
        ],
        function=run_portfolio_pipeline,
    )


# ==================== 工具执行器 ====================

class ToolExecutor:
    """工具执行器"""

    def __init__(self, tools: List[Tool]):
        self.tools = {tool.name: tool for tool in tools}

    def get_tool_schemas(self) -> List[Dict]:
        """获取所有工具的 schema（用于发送给 LLM）"""
        return [create_claude_tool_schema(tool) for tool in self.tools.values()]

    async def execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """执行工具调用"""
        if tool_name not in self.tools:
            return {"error": f"未知工具: {tool_name}"}

        tool = self.tools[tool_name]

        try:
            result = await tool.function(**tool_input)
            return result
        except Exception as e:
            return {"error": f"工具执行失败: {str(e)}"}
