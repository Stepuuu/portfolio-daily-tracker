"""
回测 API 路由 v2 - 完整的量化回测框架 API
功能:
  - 数据管理: 下载/浏览/导入/删除 SQLite 中的行情数据
  - 策略库: 查看内置策略列表、源码、参数定义；支持自定义策略上传
  - 回测执行: 完整参数配置 (佣金/滑点/T+1/初始资金/经纪商参数)
  - 回测历史: 独立记忆库，查询/对比/收藏/删除
  - 反思分析: LLM 反思 + 规则分析
"""
import json
import logging
import asyncio
import inspect
import uuid
import os
import sqlite3 as _sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])

from collections import OrderedDict

# 运行中的回测任务 (内存态)
_running_tasks: Dict[str, dict] = {}
# 使用缓存大小限制防 OOM
MAX_CACHED_RESULTS = 50
_completed_results: OrderedDict = OrderedDict()

# 自定义策略目录
CUSTOM_STRATEGY_DIR = Path(__file__).parent.parent.parent / "backtesting" / "strategies" / "custom"
CUSTOM_STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
(CUSTOM_STRATEGY_DIR / "__init__.py").touch(exist_ok=True)


# ================================================================== #
#  请求/响应模型
# ================================================================== #

class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="股票代码 (如 600519)")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    strategy: str = Field(..., description="策略标识 (如 sma_cross / rsi / dual_ma / custom:filename)")
    initial_cash: float = Field(1_000_000, description="初始资金")
    params: Dict[str, Any] = Field(default_factory=dict, description="策略参数覆盖")
    # 经纪商参数
    commission_buy: float = Field(0.0003, description="买入佣金率")
    commission_sell: float = Field(0.0013, description="卖出佣金率 (含印花税)")
    min_commission: float = Field(5.0, description="最低佣金 (元)")
    slippage_pct: float = Field(0.0002, description="滑点百分比")
    lot_size: int = Field(100, description="最小交易单位 (股)")
    # 功能开关
    with_reflection: bool = Field(True, description="是否进行 LLM 反思")
    adjust: str = Field("qfq", description="复权方式: qfq/hfq/空")
    warmup: int = Field(60, description="预热期 bar 数")
    tags: List[str] = Field(default_factory=list, description="回测标签")
    notes: str = Field("", description="备注")


class DataDownloadRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    adjust: str = "qfq"


class StrategyUploadModel(BaseModel):
    filename: str = Field(..., description="文件名 (如 my_strategy.py)")
    code: str = Field(..., description="Python 源码")


class CompareRequest(BaseModel):
    run_ids: List[str]


# ================================================================== #
#  工具函数
# ================================================================== #

def _get_strategy_registry() -> Dict:
    """获取策略注册表 (内置 + 自定义)"""
    from backtesting.strategies.examples import (
        SmaCrossStrategy, RsiMeanReversionStrategy, DualMaStrategy,
    )
    registry = {
        "sma_cross": {"cls": SmaCrossStrategy, "builtin": True},
        "rsi": {"cls": RsiMeanReversionStrategy, "builtin": True},
        "rsi_mean_reversion": {"cls": RsiMeanReversionStrategy, "builtin": True},
        "dual_ma": {"cls": DualMaStrategy, "builtin": True},
    }

    # 扫描自定义策略目录
    for py_file in CUSTOM_STRATEGY_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(py_file.stem, str(py_file))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            from backtesting.strategies.base import Strategy
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if issubclass(obj, Strategy) and obj is not Strategy:
                    key = f"custom:{py_file.stem}"
                    registry[key] = {"cls": obj, "builtin": False, "file": str(py_file)}
        except Exception as e:
            logger.warning(f"加载自定义策略 {py_file} 失败: {e}")

    return registry


def _get_strategy_class(name: str):
    registry = _get_strategy_registry()
    entry = registry.get(name.lower())
    if entry is None:
        raise ValueError(f"未知策略: {name}. 可用策略: {list(registry.keys())}")
    return entry["cls"]


def _get_history_store():
    from backtesting.history import BacktestHistoryStore
    return BacktestHistoryStore()


async def _run_backtest_task(run_id: str, req: BacktestRequest):
    """异步回测任务"""
    try:
        _running_tasks[run_id] = {"status": "running", "progress": "初始化引擎..."}

        from backtesting.engine import BacktestEngine
        from backtesting.broker.simulated import BrokerConfig

        broker_cfg = BrokerConfig(
            commission_buy=req.commission_buy,
            commission_sell=req.commission_sell,
            min_commission=req.min_commission,
            slippage_pct=req.slippage_pct,
            lot_size=req.lot_size,
        )

        engine = BacktestEngine(
            initial_cash=req.initial_cash,
            broker_config=broker_cfg,
        )

        _running_tasks[run_id]["progress"] = f"加载 {req.symbol} 数据..."
        engine.add_data(
            symbol=req.symbol,
            start_date=req.start_date,
            end_date=req.end_date,
            adjust=req.adjust,
            warmup=req.warmup,
        )

        strategy_cls = _get_strategy_class(req.strategy)
        engine.add_strategy(strategy_cls, **req.params)

        _running_tasks[run_id]["progress"] = "执行回测..."
        result = engine.run()

        result_dict = result.to_dict()
        result_dict["summary"] = result.summary()
        result_dict["start_date"] = req.start_date
        result_dict["end_date"] = req.end_date
        result_dict["strategy_class"] = strategy_cls.__name__

        # 净值曲线 (精简到最多300个点)
        eq_df = result.equity_df
        equity_curve_data = []
        if not eq_df.empty:
            step = max(1, len(eq_df) // 300)
            for i, (date, row) in enumerate(eq_df.iterrows()):
                if i % step == 0 or i == len(eq_df) - 1:
                    equity_curve_data.append({
                        "date": str(date.date()),
                        "net_value": round(float(row["net_value"]), 4),
                        "drawdown": round(float(row["drawdown"]), 4),
                    })
        result_dict["equity_curve"] = equity_curve_data

        # 全部交易明细
        trade_records = [
            {
                "date": str(t.timestamp),
                "symbol": t.symbol,
                "direction": t.direction,
                "quantity": t.quantity,
                "price": round(t.price, 2),
                "pnl": round(t.pnl, 2),
                "commission": round(t.commission, 2),
            }
            for t in result.trades
        ]
        result_dict["trade_records"] = trade_records

        # LLM 反思
        reflection = None
        if req.with_reflection:
            _running_tasks[run_id]["progress"] = "LLM 分析反思..."
            try:
                from backtesting.reflection import BacktestReflector
                reflector = BacktestReflector()
                reflection = await reflector.reflect(result)
                result_dict["reflection"] = reflection
            except Exception as e:
                logger.warning(f"LLM 反思失败: {e}")
                result_dict["reflection"] = None

        # ** 保存到独立回测历史库 **
        try:
            history = _get_history_store()
            history.save_run(
                result_dict=result_dict,
                reflection=reflection,
                equity_curve=equity_curve_data,
                trades=trade_records,
                broker_config={
                    "commission_buy": req.commission_buy,
                    "commission_sell": req.commission_sell,
                    "slippage_pct": req.slippage_pct,
                    "min_commission": req.min_commission,
                    "lot_size": req.lot_size,
                },
                tags=req.tags,
                notes=req.notes,
            )
            result_dict["saved_to_history"] = True
        except Exception as e:
            logger.warning(f"保存回测历史失败: {e}")
            result_dict["saved_to_history"] = False

        _completed_results[run_id] = result_dict
        if len(_completed_results) > MAX_CACHED_RESULTS:
            _completed_results.popitem(last=False)
            
        _running_tasks[run_id] = {"status": "completed", "progress": "完成"}

    except Exception as e:
        logger.error(f"[Backtest API] 回测失败 run_id={run_id}: {e}", exc_info=True)
        _running_tasks[run_id] = {
            "status": "failed",
            "progress": f"错误: {str(e)}",
            "message": str(e),
        }


# ================================================================== #
#  回测执行
# ================================================================== #

@router.post("/run", summary="提交异步回测任务")
async def run_backtest(req: BacktestRequest, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())[:8]
    background_tasks.add_task(_run_backtest_task, run_id, req)
    return {"run_id": run_id, "status": "submitted"}


@router.post("/run-sync", summary="同步回测 (等待完成)")
async def run_backtest_sync(req: BacktestRequest):
    run_id = str(uuid.uuid4())[:8]
    await _run_backtest_task(run_id, req)
    if run_id in _completed_results:
        return _completed_results[run_id]
    if run_id in _running_tasks and _running_tasks[run_id].get("status") == "failed":
        raise HTTPException(status_code=500, detail=_running_tasks[run_id].get("message", "回测失败"))
    raise HTTPException(status_code=500, detail="回测失败")


@router.get("/status/{run_id}", summary="查询回测状态")
async def get_status(run_id: str):
    if run_id in _running_tasks:
        status = _running_tasks[run_id].copy()
        status["run_id"] = run_id
        status["has_result"] = run_id in _completed_results
        return status
    raise HTTPException(status_code=404, detail=f"未找到 run_id: {run_id}")


@router.get("/result/{run_id}", summary="获取内存中的回测结果")
async def get_result(run_id: str):
    if run_id in _completed_results:
        return _completed_results[run_id]
    raise HTTPException(status_code=404, detail=f"未找到 run_id: {run_id}")


# ================================================================== #
#  数据管理
# ================================================================== #

@router.get("/data/cache", summary="数据库概览")
async def get_cache_stats():
    from backtesting.data.store import DataStore
    store = DataStore()
    stats = store.get_stats()
    symbols = store.get_available_symbols()
    details = []
    for sym in symbols:
        try:
            d_range = store.get_date_range(sym)
            details.append({"symbol": sym, "start_date": d_range[0], "end_date": d_range[1]})
        except Exception:
            details.append({"symbol": sym, "start_date": None, "end_date": None})
    stats["symbol_details"] = details
    return stats


@router.get("/data/symbols", summary="列出所有已缓存标的")
async def list_cached_symbols():
    from backtesting.data.store import DataStore
    store = DataStore()
    symbols = store.get_available_symbols()
    result = []
    for sym in symbols:
        try:
            with _sqlite3.connect(store.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), MIN(date), MAX(date) FROM daily_bars WHERE symbol=?",
                    (sym,)
                ).fetchone()
                result.append({"symbol": sym, "rows": row[0], "start_date": row[1], "end_date": row[2]})
        except Exception:
            result.append({"symbol": sym, "rows": 0})
    return {"symbols": result}


@router.get("/data/preview/{symbol}", summary="预览标的K线数据")
async def preview_data(symbol: str, limit: int = 50, offset: int = 0, adjust: str = "qfq"):
    from backtesting.data.store import DataStore
    store = DataStore()
    with _sqlite3.connect(store.db_path) as conn:
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume, amount, pct_change, turnover
            FROM daily_bars WHERE symbol = ? AND adjust = ?
            ORDER BY date DESC LIMIT ? OFFSET ?
        """, (symbol, adjust, limit, offset)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE symbol=? AND adjust=?",
            (symbol, adjust)
        ).fetchone()[0]
    return {"symbol": symbol, "total_rows": total, "data": [dict(r) for r in rows]}


@router.post("/data/download", summary="下载并缓存股票数据")
async def download_data(req: DataDownloadRequest):
    from backtesting.data.loader import DataLoader
    from backtesting.data.cleaner import DataCleaner
    from backtesting.data.store import DataStore
    loader = DataLoader(source="akshare")
    store = DataStore()
    try:
        df = loader.get_daily(req.symbol, req.start_date, req.end_date, req.adjust)
        df = DataCleaner(df).fill_missing().clip_price().add_returns().result
        rows = store.save_daily(req.symbol, df, req.adjust)
        return {"symbol": req.symbol, "rows_saved": rows, "start_date": req.start_date, "end_date": req.end_date}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据下载失败: {e}")


@router.post("/data/import-csv", summary="从CSV导入数据")
async def import_csv(file: UploadFile = File(...), symbol: str = Form(...), adjust: str = Form("none")):
    """上传 CSV 文件导入行情数据。CSV 必须包含列: date, open, high, low, close, volume"""
    import pandas as pd
    from io import StringIO
    from backtesting.data.store import DataStore
    content = await file.read()
    text = content.decode("utf-8")
    try:
        df = pd.read_csv(StringIO(text), parse_dates=["date"])
        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"CSV 缺少必要列: {missing}")
        df = df.set_index("date")
        store = DataStore()
        rows = store.save_daily(symbol, df, adjust)
        return {"symbol": symbol, "rows_imported": rows, "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 导入失败: {e}")


@router.delete("/data/cache/{symbol}", summary="删除标的缓存数据")
async def delete_cache(symbol: str):
    from backtesting.data.store import DataStore
    store = DataStore()
    rows = store.delete_symbol(symbol)
    return {"deleted_rows": rows, "symbol": symbol}


# ================================================================== #
#  策略库管理
# ================================================================== #

@router.get("/strategies", summary="获取全部策略列表")
async def list_strategies():
    registry = _get_strategy_registry()
    strategies = []
    for key, entry in registry.items():
        cls = entry["cls"]
        instance = cls()
        strategies.append({
            "id": key,
            "name": instance.name,
            "class": cls.__name__,
            "parameters": instance.parameters,
            "description": (cls.__doc__ or "").strip(),
            "builtin": entry.get("builtin", True),
            "file": entry.get("file", ""),
        })
    return {"strategies": strategies}


@router.get("/strategies/{strategy_id}/source", summary="查看策略源码")
async def get_strategy_source(strategy_id: str):
    registry = _get_strategy_registry()
    entry = registry.get(strategy_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")
    cls = entry["cls"]
    try:
        source = inspect.getsource(cls)
        module_file = inspect.getfile(cls)
    except OSError:
        source = "# 源码不可用"
        module_file = ""
    instance = cls()
    return {
        "id": strategy_id,
        "name": instance.name,
        "class_name": cls.__name__,
        "source_code": source,
        "file_path": module_file,
        "parameters": instance.parameters,
        "description": (cls.__doc__ or "").strip(),
        "builtin": entry.get("builtin", True),
    }


@router.post("/strategies/upload", summary="上传自定义策略")
async def upload_strategy(req: StrategyUploadModel):
    """上传自定义策略 Python 文件。要求: 文件中包含至少一个继承自 Strategy 的类。"""
    filename = req.filename
    if not filename.endswith(".py"):
        filename += ".py"
    filepath = CUSTOM_STRATEGY_DIR / filename

    try:
        compile(req.code, filename, "exec")
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Python 语法错误: {e}")

    if "Strategy" not in req.code:
        raise HTTPException(status_code=400, detail="代码中未找到 Strategy 子类")

    filepath.write_text(req.code, encoding="utf-8")

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(filepath.stem, str(filepath))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from backtesting.strategies.base import Strategy
        found = [name for name, obj in inspect.getmembers(mod, inspect.isclass)
                 if issubclass(obj, Strategy) and obj is not Strategy]
        if not found:
            filepath.unlink()
            raise HTTPException(status_code=400, detail="未找到有效的 Strategy 子类")
        return {"filename": filename, "strategy_classes": found, "strategy_id": f"custom:{filepath.stem}"}
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"策略加载失败: {e}")


@router.delete("/strategies/custom/{filename}", summary="删除自定义策略")
async def delete_custom_strategy(filename: str):
    if not filename.endswith(".py"):
        filename += ".py"
    filepath = CUSTOM_STRATEGY_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="策略文件不存在")
    filepath.unlink()
    return {"deleted": filename}


# ================================================================== #
#  回测历史记录
# ================================================================== #

@router.get("/history", summary="查询回测历史列表")
async def list_history(strategy: Optional[str] = None, symbol: Optional[str] = None, limit: int = 50, offset: int = 0):
    history = _get_history_store()
    runs = history.list_runs(strategy_name=strategy, symbol=symbol, limit=limit, offset=offset)
    stats = history.get_stats()
    return {"runs": runs, "stats": stats}


@router.get("/history/{run_id}", summary="获取历史回测详情")
async def get_history_detail(run_id: str):
    history = _get_history_store()
    run = history.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"未找到回测记录: {run_id}")
    return run


@router.delete("/history/{run_id}", summary="删除回测记录")
async def delete_history(run_id: str):
    history = _get_history_store()
    ok = history.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到回测记录")
    return {"deleted": run_id}


@router.post("/history/{run_id}/star", summary="收藏/取消收藏")
async def toggle_star(run_id: str):
    history = _get_history_store()
    starred = history.toggle_star(run_id)
    return {"run_id": run_id, "starred": starred}


@router.put("/history/{run_id}/notes", summary="更新回测备注")
async def update_notes(run_id: str, body: dict):
    history = _get_history_store()
    history.update_notes(run_id, body.get("notes", ""))
    return {"run_id": run_id, "updated": True}


@router.post("/history/compare", summary="对比多次回测")
async def compare_runs(req: CompareRequest):
    history = _get_history_store()
    return {"comparison": history.compare_runs(req.run_ids)}


@router.get("/history/best", summary="最优参数排行")
async def best_runs(strategy: Optional[str] = None, symbol: Optional[str] = None, metric: str = "sharpe_ratio", top_n: int = 10):
    history = _get_history_store()
    return {"best": history.get_best_runs(strategy, symbol, metric, top_n)}


@router.get("/history/lessons", summary="所有回测教训")
async def all_lessons(limit: int = 100):
    history = _get_history_store()
    return {"lessons": history.get_all_lessons(limit)}


# ================================================================== #
#  快速测试
# ================================================================== #

@router.post("/quick-test", summary="合成数据快速测试")
async def quick_test():
    import numpy as np
    import pandas as pd
    from backtesting.engine import BacktestEngine
    from backtesting.strategies.examples import SmaCrossStrategy

    np.random.seed(42)
    n = 500
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    price = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.01))
    df = pd.DataFrame({
        "open": price * (1 + np.random.randn(n) * 0.003),
        "high": price * (1 + abs(np.random.randn(n)) * 0.01),
        "low": price * (1 - abs(np.random.randn(n)) * 0.01),
        "close": price,
        "volume": np.random.randint(1000000, 5000000, n).astype(float),
        "amount": price * np.random.randint(1000000, 5000000, n).astype(float),
    }, index=dates)
    df.index.name = "date"

    engine = BacktestEngine(initial_cash=500_000)
    engine.add_data("TEST", df=df, start_date="", end_date="")
    engine.add_strategy(SmaCrossStrategy, fast_period=10, slow_period=30)
    result = engine.run()
    return {"status": "ok", "summary": result.summary(), "stats": result.to_dict()}
