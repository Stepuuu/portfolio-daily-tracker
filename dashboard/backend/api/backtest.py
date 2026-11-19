"""
回测 API 路由 v2 - 完整的量化回测框架 API
功能:
  - 快速回测: POST /runs 启动, GET /runs/{id} 轮询 (阶段 准备数据 → 模拟交易 → 统计),
    POST /runs/{id}/cancel, GET /runs/{id}/export, POST /runs/{id}/reflect (AI 解读, 不默认生成),
    GET /defaults?ticker= (市场、货币、每手、费率假设), GET /presets (预设方法)
  - 数据管理: 下载/浏览/导入/删除 SQLite 中的行情数据
  - 策略库: 查看内置策略列表、源码、参数定义；支持自定义策略上传
  - 回测执行 (旧接口 /run, /run-sync, /status, /result): 与 /runs 走同一个服务, 只增加字段
  - 回测历史: 独立记忆库，查询/对比/收藏/删除, POST /history/{id}/rerun 复现
  - 反思分析: 只基于结果事实, 异步, 不阻塞回测

用户能改正的错误一律返回 4xx 和中文 detail (新接口另带 errors: [{field, message}]).
请求与返回的完整说明见 backtesting/service.py. 页面要读的几处:

  期末估值 (数据结束时仍持有的仓位按最后收盘价估值, 不是真实卖出):
    RunResult.fills[] 和 round_trips[]       end_mark (bool), blocked, blocked_quantity, blocked_zh
    RunResult.facts.end_mark                  null 或 {date, pnl, positions: [{symbol, date, quantity,
                                              price, pnl, blocked, blocked_quantity, blocked_zh}]}
    旧接口 trade_records[]                    reason ("end_mark" 等), reason_zh, end_mark, blocked,
                                              blocked_quantity, blocked_zh; end_mark.positions[] 同上
    blocked: null (能卖) / "suspended" (最后一天停牌或没有行情) / "limit_down" (一字跌停) /
    "t_plus_one" (A 股当天买入, T+1 不能卖), 同时成立时按这个顺序取第一个; blocked_quantity 是
    这个原因涉及的股数 (不受限时为 0); blocked_zh 是中文原因. blocked 不为 null 的期末估值实际
    卖不出, 不能显示成一笔普通卖出.
  行情的来历 (RunResult.manifest.coverage[], 每个标的一条, 原样来自 Coverage.to_dict()):
    unverified            来自更早一次下载、本次下载没有的复权 K 线的日期 (无法核对复权锚点)
    splice_seams          两次下载在这些日期拼接 (新旧两段交界后的第一天)
    dropped_provisional   去掉的临时 K 线 (收盘前取得或存入, 没能重新下载) 的日期
    last_session          请求区间内最后一个已收盘的交易日 (没有交易日历的市场为 null)
    ends_early            true: 数据止于 last_session 之前 (数据源可能还没发布这一天), complete 为 false
    另有 features_sha256 (交给策略的整张表, 含引擎算出的指标列) 等, 见 service.py.
  提醒 (RunResult.caveats[] 与旧接口 caveats[], {code, level, text}), 其中引擎给出的:
    unverified_splice     warning  数据由两次下载拼接, 拼接处可能有假的涨跌 (对应 unverified/splice_seams)
    provisional_dropped   warning  临时 K 线被去掉, 这些交易日没有行情 (对应 dropped_provisional)
    data_ends_early       warning  行情止于最后一个已收盘的交易日之前 (对应 ends_early / last_session)
  POST /data/import-csv 的返回: {symbol, rows_imported, filename, adjust, sorted_by_date,
    provisional_dates: [YYYY-MM-DD, ...] (导入时还没收盘确定的日期, 没有导入, 也不算覆盖),
    provisional_note (只在 provisional_dates 不为空时出现: 中文说明, 收盘后再导入这一天)}
  旧版本 (v3.2.1 及更早) 保存的记录没有 RunResult: GET /runs/{id} 与 /runs/{id}/export 照样可用,
    result 是只读翻译 (backtesting.history.legacy_result, 读取时不迁移数据库): result.legacy =
    {saved_by, note_zh, unavailable: [{key, label_zh}], saved_metrics (当时保存的其余指标原值,
    当时算不出时记为 0)}, caveats 里有 legacy_result (warning); 没有的部分为 null 或空
    (benchmark.same_stock / index 为 null, round_trips 与 unfilled 为空, equity 是抽样的净值点,
    metrics 只有 final_value / total_return / annualized_return / max_drawdown, 其余为 null 并在
    metrics.undefined 写明原因, manifest 为 {legacy: true, spec: null, ...}); 不能重跑 (rerun 返回 422).
  POST /data/download: 请求可加 all_adjustments=true (连同缓存里这个代码的其他复权方式一起更新;
    指数只有不复权, 发什么 adjust 都存为不复权). 返回另有 adjustments[]: 每种复权方式一条
    {adjust, label, ok, rows_saved, rows, first, last, last_session, up_to_date, error}, 见 download_data.
"""
import json
import re
import logging
import asyncio
import inspect
import uuid
import os
import sqlite3 as _sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from backtesting import service as service_module
from backtesting.data.quality import DataQualityError
from backtesting.data.symbols import SymbolError
from backtesting.service import BacktestService, UserError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# 自定义策略目录 (part of the source tree: importing this module never writes there, not
# even to create it; only an upload, with the switch on, writes a strategy file)
CUSTOM_STRATEGY_DIR = Path(__file__).parent.parent.parent / "backtesting" / "strategies" / "custom"
# 用户策略文件名: 字母开头, 不含路径分隔符和点 (排除 __init__.py 与目录穿越)
_CUSTOM_STRATEGY_FILENAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.py)?")
_CUSTOM_STRATEGIES_DISABLED = "自定义策略会执行 Python，仅可信本地环境可设置 TRACKER_ALLOW_CUSTOM_STRATEGIES=1 启用"


def _custom_strategies_enabled() -> bool:
    return os.environ.get("TRACKER_ALLOW_CUSTOM_STRATEGIES") == "1"


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
    # 只是券商佣金; 印花税、过户费、港股交易费/征费、美股 SEC 费按市场与成交日期另计.
    # null 或不传: 按市场默认 (A股/港股 0.025% 最低 5, 美股 0).
    # fee_basis 说明这三个数的含义 (旧客户端不传, 见 service.translate_legacy_fees):
    #   "commission_only"            只是佣金, 原样使用 (现在的页面总是这样发送)
    #   "legacy_inclusive" 或不传    v3.2 的含义: commission_sell 含印花税 (旧页面默认 0.0013 =
    #                                佣金 0.0003 + 当时的印花税 0.001). 卖出按 commission_sell 原样收取,
    #                                不再另收印花税; v3.2 没有计算的过户费、港股交易费与征费、美股 SEC 费
    #                                按成交日的费率另计; 买入不变. 结果带 legacy_fees_translated 提醒.
    commission_buy: Optional[float] = Field(None, description="买入佣金率 (仅佣金)")
    commission_sell: Optional[float] = Field(
        None, description="卖出佣金率: fee_basis=commission_only 时仅佣金; 不传 fee_basis 时按 v3.2 含印花税")
    min_commission: Optional[float] = Field(None, description="每笔最低佣金 (仅佣金, 交易货币)")
    fee_basis: Optional[Literal["commission_only", "legacy_inclusive"]] = Field(
        None, description="commission_only: 三个佣金字段只是佣金; legacy_inclusive 或不传: 旧客户端的卖出佣金率含印花税")
    slippage_pct: float = Field(0.0002, description="滑点比例 (0.0002 = 0.02%)")
    lot_size: Optional[int] = Field(
        None, gt=0,
        description="最小交易单位 (股, 正整数). null 与不传完全相同: 按市场取值 (A股/港股 100, 美股 1); "
                    "0 或负数返回 422",
    )
    # 功能开关
    with_reflection: bool = Field(False, description="回测完成后在后台生成解读 (不阻塞回测; 结果见 "
                                                     "GET /result/{run_id} 或历史记录的 reflection)")
    adjust: str = Field("qfq", description="复权方式: qfq/hfq/空 (none)")
    allow_unadjusted: bool = Field(False, description="不复权数据有疑似除权跳空时仍然运行 (结果带提醒)")
    benchmark: str = Field("none", description="对比指数: none (默认) / auto (按市场) / IDX:代码")
    warmup: int = Field(60, ge=0, description="预热期 bar 数 (>= 0)")
    tags: List[str] = Field(default_factory=list, description="回测标签")
    notes: str = Field("", description="备注")


class DataDownloadRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    adjust: str = "qfq"
    # true: also every other price adjustment the cache holds for this symbol (an index has
    # only 不复权, the one the comparison reads); the answer's adjustments[] says per adjustment
    # what the cache now holds.
    all_adjustments: bool = False


class StrategyUploadModel(BaseModel):
    filename: str = Field(..., description="文件名 (如 my_strategy.py)")
    code: str = Field(..., description="Python 源码")


class CompareRequest(BaseModel):
    run_ids: List[str]


# ================================================================== #
#  工具函数
# ================================================================== #

def _load_custom_strategy_classes(py_file: Path) -> list:
    """执行自定义策略文件, 返回该文件自身定义的 Strategy 子类 (不含它导入的类).

    The text is read once and that text is what runs (no bytecode cache), and each
    class carries it (service.STRATEGY_SOURCE_ATTR): a run's manifest keeps the source
    and its sha256, so a rerun can tell when the file changed."""
    import importlib.util
    from backtesting.strategies.base import Strategy
    code = py_file.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location(py_file.stem, str(py_file))
    mod = importlib.util.module_from_spec(spec)
    exec(compile(code, str(py_file), "exec"), mod.__dict__)
    classes = [
        obj for _, obj in inspect.getmembers(mod, inspect.isclass)
        if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == mod.__name__
    ]
    for cls in classes:
        setattr(cls, service_module.STRATEGY_SOURCE_ATTR, code)
    return classes


def _custom_strategy_ids(py_file: Path, classes: list) -> list:
    """Stable registry ids for the classes of one custom file, as (id, cls) pairs.

    A file with one class keeps the id custom:<file>; a file with several gives
    each class custom:<file>:<ClassName>, so none of them shadows another.
    """
    if len(classes) == 1:
        return [(f"custom:{py_file.stem}", classes[0])]
    return [(f"custom:{py_file.stem}:{cls.__name__}", cls) for cls in classes]


def _read_custom_strategy_file(path: str):
    """Text of a registered custom strategy file, only if it is a regular file
    directly inside CUSTOM_STRATEGY_DIR (symlinks are resolved and refused if they
    lead elsewhere)."""
    try:
        resolved = Path(path).resolve(strict=True)
        inside = resolved.parent == CUSTOM_STRATEGY_DIR.resolve() and resolved.is_file()
    except (OSError, RuntimeError):
        inside = False
    if not inside or not _CUSTOM_STRATEGY_FILENAME.fullmatch(resolved.name):
        raise HTTPException(status_code=404, detail="策略文件不存在")
    return resolved.read_text(encoding="utf-8"), str(resolved)


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

    # 扫描自定义策略目录 (加载即执行其中的 Python, 开关未开启时不加载)
    custom_files = CUSTOM_STRATEGY_DIR.glob("*.py") if _custom_strategies_enabled() else []
    for py_file in custom_files:
        if py_file.name.startswith("_"):
            continue
        try:
            for key, obj in _custom_strategy_ids(py_file, _load_custom_strategy_classes(py_file)):
                registry[key] = {"cls": obj, "builtin": False, "file": str(py_file)}
        except Exception as e:
            logger.warning(f"加载自定义策略 {py_file} 失败: {e}")

    return registry


def _get_strategy_class(name: str):
    registry = _get_strategy_registry()
    entry = registry.get(name)
    if entry is None:
        # Ids are matched case-insensitively when that is unambiguous.
        matches = [value for key, value in registry.items() if key.lower() == name.lower()]
        entry = matches[0] if len(matches) == 1 else None
    if entry is None:
        raise ValueError(f"未知策略: {name}. 可用策略: {list(registry.keys())}")
    return entry["cls"]


def _get_history_store(reading: bool = False):
    # Demo mode: an empty store; reading the history creates no database there. ``reading``: a read
    # before the first saved run gets an absent store (empty answers) instead of creating the database.
    from backtesting.history import open_history
    if not reading:
        return open_history()
    from backtesting.storage import for_reading
    with for_reading():
        return open_history()


def _demo() -> bool:
    from backtesting.storage import demo_mode
    return demo_mode()


def _demo_bar_path() -> str:
    from backtesting.storage import bars_db_path
    return bars_db_path()


# One service for every run route. The lambdas look the helpers up at call time,
# so a replaced registry, history store or presets module takes effect at once.
SERVICE = BacktestService(
    strategy_resolver=lambda name: _get_strategy_class(name),
    presets_loader=lambda: service_module.load_presets(),
    history_factory=lambda: _get_history_store(),
    history_reader=lambda: _get_history_store(reading=True),
)


def _user_error(exc: UserError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.message, "errors": exc.errors})


async def _json_body(request: Request, required: bool = True):
    raw = await request.body()
    if not raw.strip():
        if required:
            raise UserError("请求体不能为空，需要 JSON 对象")
        return {}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise UserError("请求体不是有效的 JSON") from None
    except RecursionError:
        raise UserError("请求体嵌套太深") from None


def _not_found(run_id: str) -> UserError:
    return UserError(f"没有找到回测 {run_id}（只保留最近的运行；已保存的运行可在历史记录里打开）", 404)


# ================================================================== #
#  快速回测: 启动 / 轮询 / 取消 / 导出 / 解读 / 默认值 / 预设
# ================================================================== #

@router.post("/runs", summary="启动一次回测 (RunSpec), 返回 run_id", status_code=202)
async def start_run(request: Request):
    try:
        spec = service_module.parse_spec(await _json_body(request))
        plan = await asyncio.to_thread(SERVICE.plan, spec)
        task = SERVICE.start(plan)
    except UserError as exc:
        return _user_error(exc)
    return JSONResponse(status_code=202, content={
        "run_id": task.id, "status": task.status, "poll": f"/api/backtest/runs/{task.id}"})


@router.get("/runs/{run_id}", summary="回测状态、阶段, 完成后带 RunResult")
async def get_run(run_id: str, include_result: bool = True):
    """include_result=false leaves the RunResult out (result: null): for polling the
    stage or the AI reading without downloading the whole result again."""
    task = await asyncio.to_thread(SERVICE.find, run_id)
    if task is None:
        return _user_error(_not_found(run_id))
    return task.snapshot(with_result=include_result)


@router.post("/runs/{run_id}/cancel", summary="取消进行中的回测")
async def cancel_run(run_id: str):
    task = SERVICE.registry.get(run_id)
    if task is None:
        return _user_error(_not_found(run_id))
    if task.finished:
        state = {"completed": "完成", "failed": "失败", "cancelled": "取消"}[task.status]
        return _user_error(UserError(f"回测已经{state}，不能再取消", 409))
    SERVICE.registry.cancel(run_id)
    return {**task.snapshot(with_result=False), "cancel_requested": True}


@router.post("/runs/{run_id}/reflect", summary="生成 AI 解读 (异步, 只基于结果事实)", status_code=202)
async def reflect_run(run_id: str):
    try:
        task = await asyncio.to_thread(SERVICE.reflect, run_id)
    except UserError as exc:
        return _user_error(exc)
    return JSONResponse(status_code=202, content={"run_id": run_id, "reflection": dict(task.reflection)})


EXPORT_PARTS = ("fills", "round_trips", "unfilled", "equity", "manifest")


@router.get("/runs/{run_id}/export", summary="导出成交、净值和运行清单 (csv / json)")
async def export_run(run_id: str, format: str = "csv", part: Optional[str] = None):
    from backtesting.export import to_csv, to_json
    fmt = (format or "").lower()
    if fmt not in ("csv", "json"):
        return _user_error(UserError("format 只能是 csv 或 json"))
    if part is not None and part not in EXPORT_PARTS:
        return _user_error(UserError(f"part 只能是 {' / '.join(EXPORT_PARTS)}"))
    task = await asyncio.to_thread(SERVICE.find, run_id)
    if task is None:
        return _user_error(_not_found(run_id))
    if task.status != "completed" or not task.result:
        return _user_error(UserError("回测还没有完成，完成后才能导出", 409))
    name = f"backtest-{run_id}{'-' + part if part else ''}"
    if fmt == "json":
        return Response(content=to_json(task.result, part), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    return Response(content=to_csv(task.result, part), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/defaults", summary="标的的市场、货币、每手股数和费率假设")
async def get_defaults(ticker: Optional[str] = None):
    if ticker is None or not ticker.strip():
        return _user_error(UserError("请提供标的代码 ticker，例如 SHA:600519、0700.HK、AAPL"))
    try:
        return await asyncio.to_thread(SERVICE.defaults, ticker)
    except UserError as exc:
        return _user_error(exc)


@router.get("/presets", summary="预设方法列表 (presets 模块不存在时为空)")
async def list_presets():
    module = service_module.load_presets()
    if module is None:
        return {"available": False, "presets": [], "detail": service_module.PRESETS_MISSING}
    try:
        return {"available": True, "presets": list(module.describe())}
    except Exception as exc:
        logger.warning("[Backtest] presets.describe failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            "available": False, "presets": [], "detail": "预设方法列表暂时不可用"})


# ================================================================== #
#  回测执行 (旧接口, 与 /runs 走同一个服务)
# ================================================================== #

def _plan_legacy(req: "BacktestRequest"):
    return SERVICE.plan_legacy(req)


@router.post("/run", summary="提交异步回测任务")
async def run_backtest(req: BacktestRequest):
    try:
        plan = await asyncio.to_thread(_plan_legacy, req)
        task = SERVICE.start(plan, kind="legacy", reflect_after=req.with_reflection)
    except UserError as exc:
        return _user_error(exc)
    return {"run_id": task.id, "status": "submitted"}


@router.post("/run-sync", summary="同步回测 (等待完成)")
async def run_backtest_sync(req: BacktestRequest):
    try:
        plan = await asyncio.to_thread(_plan_legacy, req)
        task = await asyncio.to_thread(SERVICE.run_inline, plan, "legacy", req.with_reflection)
    except UserError as exc:
        return _user_error(exc)
    legacy = task.legacy_copy()
    if task.status == "completed" and legacy is not None:
        return legacy
    error = task.error or {"status": 409, "message": "回测已取消"}
    raise HTTPException(status_code=error["status"], detail=error["message"])


@router.get("/status/{run_id}", summary="查询回测状态")
async def get_status(run_id: str):
    task = SERVICE.registry.get(run_id)
    if task is not None:
        return task.legacy_status()
    raise HTTPException(status_code=404, detail=f"未找到 run_id: {run_id}")


@router.get("/result/{run_id}", summary="获取内存中的回测结果")
async def get_result(run_id: str):
    task = SERVICE.registry.get(run_id)
    legacy = task.legacy_copy() if task is not None else None
    if legacy is not None:
        return legacy
    raise HTTPException(status_code=404, detail=f"未找到 run_id: {run_id}")


# ================================================================== #
#  数据管理
# ================================================================== #

def _bar_store_for_reading():
    """The bar cache for a read, or ``None`` when there is none to read: the demo keeps none, and a normal
    one does not exist before its first download (a read answers empty and creates nothing)."""
    from backtesting.data.store import DataStore
    from backtesting.storage import for_reading
    if _demo():
        return None
    with for_reading():
        store = DataStore()
    return None if getattr(store, "absent", False) else store


@router.get("/data/cache", summary="数据库概览")
async def get_cache_stats():
    """Counts and dates, and per cached instrument (additive fields of ``symbol_details``): ``source``
    (the source of its latest download or import, null when the cache predates source records),
    ``fetched_at`` (that download's time, null when unknown), ``complete`` (true when its bars lie inside
    one recorded download interval, false when they do not, null when nothing was recorded) and
    ``adjustments`` (the same per price adjustment, with ``rows``, ``first``, ``last``, ``coverage`` and
    ``sources``; ``DataStore.describe``)."""
    store = _bar_store_for_reading()
    if store is None:                   # the demo keeps no bar cache; a normal one before its first download
        empty = {"symbols": 0, "total_rows": 0, "earliest_date": None, "latest_date": None,
                 "db_path": _demo_bar_path(), "symbol_details": []}
        return {**empty, "kept": False} if _demo() else empty
    stats = store.get_stats()
    symbols = store.get_available_symbols()
    described = {entry["symbol"]: entry for entry in store.describe(symbols)}
    details = []
    for sym in symbols:
        try:
            d_range = store.get_date_range(sym)
            detail = {"symbol": sym, "start_date": d_range[0], "end_date": d_range[1]}
        except Exception:
            detail = {"symbol": sym, "start_date": None, "end_date": None}
        entry = described.get(sym) or {}
        details.append({**detail, "source": entry.get("source"), "fetched_at": entry.get("fetched_at"),
                        "complete": entry.get("complete"), "adjustments": entry.get("adjustments", [])})
    stats["symbol_details"] = details
    return stats


@router.get("/data/symbols", summary="列出所有已缓存标的")
async def list_cached_symbols():
    store = _bar_store_for_reading()
    if store is None:
        return {"symbols": []}
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
async def preview_data(symbol: str, limit: int = 50, offset: int = 0, adjust: Optional[str] = None):
    """adjust 不传时返回有数据的复权方式 (依次 qfq / 不复权 / hfq); 返回的 adjust 字段
    说明实际使用的是哪一种, available_adjustments 列出该标的已有数据的复权方式。"""
    store = _bar_store_for_reading()
    if store is None:
        return {"symbol": symbol, "adjust": "qfq" if adjust is None else adjust, "available_adjustments": [],
                "total_rows": 0, "data": []}
    with _sqlite3.connect(store.db_path) as conn:
        conn.row_factory = _sqlite3.Row
        stored = {row[0] for row in conn.execute(
            "SELECT DISTINCT adjust FROM daily_bars WHERE symbol=?", (symbol,)).fetchall()}
        available = [value for value in ("qfq", "", "hfq") if value in stored]
        if adjust is None:
            adjust = available[0] if available else "qfq"
        else:
            adjust = adjust.strip().lower()
            if adjust == "none":
                adjust = ""
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume, amount, pct_change, turnover
            FROM daily_bars WHERE symbol = ? AND adjust = ?
            ORDER BY date DESC LIMIT ? OFFSET ?
        """, (symbol, adjust, limit, offset)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE symbol=? AND adjust=?",
            (symbol, adjust)
        ).fetchone()[0]
    return {"symbol": symbol, "adjust": adjust, "available_adjustments": available,
            "total_rows": total, "data": [dict(r) for r in rows]}


ADJUST_NAMES = {"qfq": "前复权", "": "不复权", "hfq": "后复权"}


def _clock() -> datetime:
    """The download route's clock (tests replace it)."""
    return datetime.now()


@router.post("/data/download", summary="下载并缓存股票数据")
def download_data(req: DataDownloadRequest):
    """Downloads [start_date, end_date] of one symbol into the bar cache.

    adjust: qfq / hfq / 空 (不复权); an index is always stored 不复权 (its bars are not adjusted
    and the comparison reads that series), whatever is sent. all_adjustments=true: also every
    other adjustment the cache holds for this symbol, each downloaded on its own.

    Answer: {symbol, rows_saved (all adjustments together), start_date, end_date,
    adjustments: [{adjust, label, ok, rows_saved, rows, first, last, last_session, up_to_date,
    error}]}, one per adjustment downloaded: rows / first / last are what the cache now holds
    for the symbol under that adjustment (null when nothing), last_session the last trading day
    up to end_date whose close has settled (null without a session calendar), up_to_date whether
    the cached bars reach it (null when unknown), error the fixed Chinese reason a download
    failed (ok false; the others are still saved). When every download fails the route answers
    422 or 502 as before."""
    from backtesting.data.loader import DataLoader
    from backtesting.data.store import DataStore
    loader = DataLoader(source="akshare")
    store = DataStore()
    try:
        from backtesting.data.symbols import resolve
        sym = resolve(req.symbol)         # an unknown form is a 422 before any network call
    except SymbolError as e:
        raise HTTPException(status_code=422, detail=f"数据下载失败: {e}")
    adjust = (req.adjust or "").strip().lower()
    adjust = "" if adjust == "none" or not sym.adjustable else adjust
    adjustments = [adjust]
    if req.all_adjustments and sym.adjustable:
        try:
            held = {item["adjust"] for entry in store.list_available([req.symbol]) for item in entry["adjustments"]}
        except Exception:
            logger.warning("[Backtest] 读不到 %s 已缓存的复权方式", req.symbol, exc_info=True)
            held = set()
        adjustments += [value for value in ("qfq", "", "hfq") if value in held and value not in adjustments]
    entries, refusals = [], []
    for value in adjustments:
        try:
            rows = _download_one(loader, store, req, value)
        except HTTPException as exc:
            refusals.append(exc)
            entries.append({"adjust": value, "label": ADJUST_NAMES.get(value, value), "ok": False,
                            "rows_saved": None, "error": exc.detail, **_held(store, req.symbol, sym, value, req.end_date)})
            continue
        entries.append({"adjust": value, "label": ADJUST_NAMES.get(value, value), "ok": True, "rows_saved": rows,
                        "error": None, **_held(store, req.symbol, sym, value, req.end_date)})
    if len(refusals) == len(entries):
        raise refusals[0]
    return {"symbol": req.symbol, "rows_saved": sum(entry["rows_saved"] or 0 for entry in entries),
            "start_date": req.start_date, "end_date": req.end_date, "adjustments": entries}


def _download_one(loader, store, req: DataDownloadRequest, adjust: str) -> int:
    """Download, check and save one adjustment of the request; the rows saved."""
    from backtesting.data.cleaner import DataCleaner
    from backtesting.data.quality import require_valid_daily_bars
    try:
        fetched_at = _clock().isoformat(timespec="seconds")
        try:
            df = loader.get_daily(req.symbol, req.start_date, req.end_date, adjust)
        except Exception as e:
            # What the provider raised (its class and message) goes to the log;
            # the person gets a fixed classification.
            from core.provider_errors import log_failure
            raise HTTPException(status_code=502, detail=f"数据下载失败：{log_failure(logger, 'backtest download', e)}") from None
        require_valid_daily_bars(df, symbol=req.symbol, adjust=adjust)
        df = DataCleaner(df).add_returns().result
        rows = store.save_daily(req.symbol, df, adjust)
        store.record_download(req.symbol, adjust, req.start_date, req.end_date, fetched_at=fetched_at)
        return rows
    except HTTPException:
        raise
    except (SymbolError, DataQualityError) as e:
        # An unknown symbol form, or bars that fail the quality check: the application's own words.
        raise HTTPException(status_code=422, detail=f"数据下载失败: {e}")
    except Exception as e:
        # Storing the bars failed: the class and message go to the log only.
        from core.provider_errors import log_failure
        raise HTTPException(status_code=502, detail=f"数据下载失败：{log_failure(logger, 'backtest download', e)}") from None


def _held(store, key: str, sym, adjust: str, end_date: str) -> Dict[str, Any]:
    """What the cache holds for `key` under `adjust` after a download, and whether it reaches
    the last settled session up to end_date."""
    from backtesting.data.repository import last_settled_session
    try:
        found = next((item for entry in store.list_available([key]) for item in entry["adjustments"]
                      if item["adjust"] == adjust), None)
    except Exception:
        # The cache cannot be read back: what it holds is unknown (null), the download's answer stands.
        logger.warning("[Backtest] 下载后读不到缓存 %s (%s)", key, adjust or "不复权", exc_info=True)
        found = None
    try:
        session = last_settled_session(sym.market, end_date, _clock())
    except (TypeError, ValueError):
        session = None
    last = found["last"] if found else None
    return {"rows": found["rows"] if found else None, "first": found["first"] if found else None, "last": last,
            "last_session": session.strftime("%Y-%m-%d") if session is not None else None,
            "up_to_date": (last >= session.strftime("%Y-%m-%d")) if last and session is not None else None}


class CsvRefused(ValueError):
    """A CSV the import refuses; the message is the route's own Chinese text."""


CSV_UNREADABLE = ("CSV 导入失败: 文件内容无法读取。请检查日期 (YYYY-MM-DD) 和数字的写法, 每行都要有 "
                  "date, open, high, low, close, volume")


@router.post("/data/import-csv", summary="从CSV导入数据")
async def import_csv(file: UploadFile = File(...), symbol: str = Form(...), adjust: str = Form("")):
    """上传 CSV 文件导入行情数据。CSV 必须包含列: date, open, high, low, close, volume

    adjust 与回测引擎一致: qfq / hfq / 空串(不复权)。"none" 作为不复权的别名接受。
    表头有重复列名时拒绝导入; 日期唯一但非升序 (如最新在前) 时按日期排序后导入,
    返回 sorted_by_date=true。停牌日 (成交量 0 且开高低收相同) 合法。
    """
    import csv
    import pandas as pd
    from io import StringIO
    from backtesting.data.quality import require_valid_daily_bars
    from backtesting.data.store import DataStore
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV 导入失败: 文件不是 UTF-8 编码; 请另存为 UTF-8 (CSV UTF-8) 后再导入")
    adjust = adjust.strip().lower()
    if adjust == "none":
        adjust = ""
    try:
        # pandas renames a repeated header ("open" -> "open.1") and would then
        # import the first column silently, so check the raw header first.
        header = [name.strip() for name in next(csv.reader(StringIO(text)), [])]
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if duplicates:
            raise CsvRefused(f"CSV 表头有重复列名: {', '.join(duplicates)}; 请删除或重命名重复的列")
        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in header]
        if missing:
            raise CsvRefused(f"CSV 缺少必要列: {missing}")
        df = pd.read_csv(StringIO(text), parse_dates=["date"])
        df = df.set_index("date")
        # Row order carries no information once dates are unique (the store returns
        # bars by date), so sort instead of rejecting newest-first exports.
        sorted_by_date = False
        if (isinstance(df.index, pd.DatetimeIndex) and not df.index.hasnans
                and not df.index.duplicated().any() and not df.index.is_monotonic_increasing):
            df = df.sort_index()
            sorted_by_date = True
        # 写入前校验: 复权取值、OHLC 一致性、重复日期
        require_valid_daily_bars(df, symbol=symbol, adjust=adjust)
        store = DataStore()
        # An upload has no fetch time: it is judged now. A row dated today before the
        # market's close is final is provisional (the file was made during the session)
        # and is neither saved nor covered (provisional_dates in the answer).
        rows = store.save_daily(symbol, df, adjust)
        provisional = list(store.last_save.provisional) if store.last_save is not None else []
        # The file vouches for the dates it spans, up to the last day whose bar was
        # final (record_coverage clamps the end).
        store.record_coverage(symbol, adjust, str(df.index.min().date()), str(df.index.max().date()))
        answer = {"symbol": symbol, "rows_imported": rows, "filename": file.filename, "adjust": adjust,
                  "sorted_by_date": sorted_by_date, "provisional_dates": provisional}
        if provisional:
            answer["provisional_note"] = (f"{'、'.join(provisional)} 的行情在导入时还没有收盘确定, 是临时数据, "
                                          f"没有导入; 收盘后再导入这一天")
        return answer
    except (CsvRefused, DataQualityError) as e:          # the application's own words
        raise HTTPException(status_code=400, detail=f"CSV 导入失败: {e}")
    except Exception as e:
        # What pandas or the store raised (its class and message) goes to the log; the person gets a fixed text.
        from core.provider_errors import log_failure
        log_failure(logger, "backtest CSV import", e)
        raise HTTPException(status_code=400, detail=CSV_UNREADABLE) from None


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
    if not entry.get("builtin", True):
        # Custom modules are loaded from a file path and are not in sys.modules, so
        # inspect.getsource raises TypeError; serve the user's file instead.
        source, module_file = _read_custom_strategy_file(entry.get("file", ""))
    else:
        try:
            source = inspect.getsource(cls)
            module_file = inspect.getfile(cls)
        except (OSError, TypeError):
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
    if not _custom_strategies_enabled():
        raise HTTPException(status_code=403, detail=_CUSTOM_STRATEGIES_DISABLED)
    filename = req.filename
    if not _CUSTOM_STRATEGY_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=422, detail="策略文件名只允许字母、数字和下划线")
    if not filename.endswith(".py"):
        filename += ".py"
    filepath = CUSTOM_STRATEGY_DIR / filename

    try:
        compile(req.code, filename, "exec")
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Python 语法错误: {e}")

    if "Strategy" not in req.code:
        raise HTTPException(status_code=400, detail="代码中未找到 Strategy 子类")

    CUSTOM_STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(req.code, encoding="utf-8")

    try:
        classes = _load_custom_strategy_classes(filepath)
        if not classes:
            filepath.unlink()
            raise HTTPException(status_code=400, detail="未找到有效的 Strategy 子类")
        ids = [key for key, _ in _custom_strategy_ids(filepath, classes)]
        return {"filename": filename, "strategy_classes": [cls.__name__ for cls in classes],
                "strategy_id": ids[0], "strategy_ids": ids}
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"策略加载失败: {e}")


@router.delete("/strategies/custom/{filename}", summary="删除自定义策略")
async def delete_custom_strategy(filename: str):
    if not _custom_strategies_enabled():
        raise HTTPException(status_code=403, detail=_CUSTOM_STRATEGIES_DISABLED)
    # 只允许删除自定义目录下的用户策略文件
    if not _CUSTOM_STRATEGY_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=422, detail="策略文件名只允许字母、数字和下划线")
    if not filename.endswith(".py"):
        filename += ".py"
    filepath = CUSTOM_STRATEGY_DIR / filename
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="策略文件不存在")
    filepath.unlink()
    return {"deleted": filename}


# ================================================================== #
#  回测历史记录
# ================================================================== #

@router.get("/history", summary="查询回测历史列表")
async def list_history(strategy: Optional[str] = None, symbol: Optional[str] = None, limit: int = 50, offset: int = 0):
    history = _get_history_store(reading=True)
    runs = history.list_runs(strategy_name=strategy, symbol=symbol, limit=limit, offset=offset)
    stats = history.get_stats()
    return {"runs": runs, "stats": stats}


# 固定路径必须注册在 /history/{run_id} 之前，否则会被参数路径遮蔽
@router.get("/history/best", summary="最优参数排行")
async def best_runs(strategy: Optional[str] = None, symbol: Optional[str] = None, metric: str = "sharpe_ratio", top_n: int = 10):
    history = _get_history_store(reading=True)
    return {"best": history.get_best_runs(strategy, symbol, metric, top_n)}


@router.get("/history/lessons", summary="所有回测教训")
async def all_lessons(limit: int = 100):
    history = _get_history_store(reading=True)
    return {"lessons": history.get_all_lessons(limit)}


@router.get("/history/{run_id}", summary="获取历史回测详情")
async def get_history_detail(run_id: str):
    history = _get_history_store(reading=True)
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


@router.post("/history/{run_id}/rerun", summary="按保存的运行清单重跑 (同数据得同指标, 数据变了带 data_changed 提醒)",
             status_code=202)
async def rerun_history(run_id: str, request: Request):
    try:
        body = await _json_body(request, required=False)
        if not isinstance(body, dict) or set(body) - {"save"} or not isinstance(body.get("save", False), bool):
            raise UserError("请求体只能是 {\"save\": true/false}")
        saved = await asyncio.to_thread(lambda: _get_history_store().get_run(run_id))
        if not saved:
            raise UserError(f"未找到回测记录: {run_id}", 404)
        plan = await asyncio.to_thread(SERVICE.plan_rerun, saved, save=bool(body.get("save", False)))
        task = SERVICE.start(plan, kind="rerun")
    except UserError as exc:
        return _user_error(exc)
    return JSONResponse(status_code=202, content={
        "run_id": task.id, "rerun_of": run_id, "status": task.status, "poll": f"/api/backtest/runs/{task.id}"})


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


# ================================================================== #
#  快速测试
# ================================================================== #

@router.post("/quick-test", summary="合成数据快速测试")
def quick_test():
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
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df.index.name = "date"

    engine = BacktestEngine(initial_cash=500_000)
    engine.add_data("TEST", df=df, start_date="", end_date="")
    engine.add_strategy(SmaCrossStrategy, fast_period=10, slow_period=30)
    result = engine.run()
    return {"status": "ok", "summary": result.summary(), "stats": result.to_dict()}
