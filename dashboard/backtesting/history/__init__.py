"""
回测记录存储 - 独立的回测历史数据库
与通用 memory.json 分离，专门存放每次回测的完整结果

功能:
  - 保存每次回测的完整结果 (策略、参数、统计、反思)
  - 按策略/标的/时间查询历史回测
  - 对比多次回测结果
  - 标记最优参数组合
  - 复现: 保存运行清单 (manifest: 设置、数据哈希、引擎版本) 与完整的每日净值

新增列只用 ALTER TABLE ADD COLUMN 追加 (见 RUN_COLUMNS / TRADE_COLUMNS), 为写入打开时
检查一遍, 对旧数据库幂等. 旧记录的新列为 NULL. 为读取打开 (backtesting.storage.for_reading:
页面的 GET) 时不建表也不迁移: 旧版本 (如 v3.2.1) 的数据库照原样读, 缺的列读作 NULL, 只读的
副本也能读.

没有 result_json 的旧记录 (v3.2.1 及更早) 由 legacy_result 只读地翻译成 RunResult 的形状,
带 legacy 标记 (哪些部分没有), 供打开和导出 (service.BacktestService.find).

页面读取的字段名作为别名一并返回 (旧页面读 primary_symbol / starred_count /
equity_curve / reflection / 教训的 run_date):
  list_runs, compare_runs, get_best_runs, get_all_lessons: primary_symbol (= symbol)
  get_run: primary_symbol, equity_curve (= equity_curve_json), reflection (= reflection_text)
  get_stats: starred_count (= starred_runs)
  get_all_lessons: run_date (该次回测的 run_date)
"""
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Additive columns of backtest_runs: (name, SQL type). Never renamed or removed.
RUN_COLUMNS = (
    ("ticker", "TEXT"),            # canonical EXCH:CODE (symbol keeps the label as entered)
    ("market", "TEXT"),            # CN / HK / US
    ("currency", "TEXT"),
    ("kind", "TEXT"),              # stock / etf
    ("adjust", "TEXT"),
    ("warmup", "INTEGER"),
    ("min_commission", "REAL"),
    ("lot_size", "INTEGER"),       # explicit lot override, NULL = market rule
    ("fees_json", "TEXT"),         # commission settings, slippage, fee schedule version
    ("data_sha256", "TEXT"),
    ("engine_version", "TEXT"),
    ("manifest_json", "TEXT"),     # everything a rerun needs
    ("result_json", "TEXT"),       # the full RunResult (daily equity, fills, round trips ...)
    ("headline_zh", "TEXT"),
    ("caveats_json", "TEXT"),
    ("preset", "TEXT"),
    ("period_preset", "TEXT"),
    ("source", "TEXT"),            # data source of the traded bars
    ("closed_trades", "INTEGER"),
    ("payoff_ratio", "REAL"),
    ("rerun_of", "TEXT"),
    ("first_date", "TEXT"),        # the first and last bar the run used (start_date/end_date: as requested)
    ("last_date", "TEXT"),
)
TRADE_COLUMNS = (
    ("fees_json", "TEXT"),
    ("reason", "TEXT"),
)
# New JSON columns are returned parsed under these names (the raw *_json key is dropped).
_JSON_ALIASES = {"manifest_json": "manifest", "result_json": "result", "caveats_json": "caveats", "fees_json": "fees"}


def _finite(value):
    """The value with NaN and infinities replaced by None (stored JSON stays valid JSON)."""
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def _json(value) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_finite(value), ensure_ascii=False, allow_nan=False, default=str)


def _canonical(symbol: str) -> Optional[str]:
    try:
        from backtesting.data.symbols import resolve
        return resolve(symbol).ticker
    except Exception:
        return None


class BacktestHistoryStore:
    """
    回测历史记录存储（独立 SQLite 数据库）

    表结构:
      - backtest_runs:      每次回测的基本信息与统计
      - backtest_trades:    每次回测的交易明细
      - backtest_lessons:   每次回测的反思教训
      - backtest_tags:      运行标签 (用于分组比较)
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # dashboard/data, TRACKER_BACKTEST_DIR, or the demo's own directory (see storage.py)
            from backtesting.storage import history_db_path
            db_path = history_db_path()
        self.db_path = db_path
        # Constructed for reading (backtesting.storage.for_reading) before the first run was saved: every
        # read answers empty and the database is not created.
        from backtesting.storage import absent_for_reading, reading
        self.absent = absent_for_reading(db_path)
        # Constructed for reading, an existing database is read as it is: never created, migrated or
        # otherwise written (an older version's file, a read-only copy). Missing columns read as NULL.
        self.reading = reading()
        if self.absent or self.reading:
            return
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id          TEXT PRIMARY KEY,
                    run_date        TEXT NOT NULL,
                    strategy_name   TEXT NOT NULL,
                    strategy_class  TEXT,
                    strategy_params TEXT,
                    symbol          TEXT NOT NULL,
                    start_date      TEXT,
                    end_date        TEXT,
                    initial_cash    REAL,
                    final_value     REAL,
                    total_return    REAL,
                    annualized_return REAL,
                    benchmark_return REAL,
                    alpha           REAL,
                    sharpe_ratio    REAL,
                    sortino_ratio   REAL,
                    calmar_ratio    REAL,
                    max_drawdown    REAL,
                    max_drawdown_duration INTEGER,
                    volatility      REAL,
                    var_95          REAL,
                    total_trades    INTEGER,
                    win_rate        REAL,
                    profit_factor   REAL,
                    avg_profit      REAL,
                    avg_loss        REAL,
                    max_single_profit REAL,
                    max_single_loss REAL,
                    -- 经纪商参数
                    commission_buy  REAL,
                    commission_sell REAL,
                    slippage_pct    REAL,
                    -- 反思
                    reflection_text TEXT,
                    -- 净值曲线（JSON数组）
                    equity_curve_json TEXT,
                    -- 摘要
                    summary_text    TEXT,
                    -- 元数据
                    tags            TEXT DEFAULT '[]',
                    notes           TEXT DEFAULT '',
                    starred         INTEGER DEFAULT 0,
                    created_at      TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS backtest_trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    trade_date      TEXT,
                    symbol          TEXT,
                    direction       TEXT,
                    quantity        REAL,
                    price           REAL,
                    commission      REAL,
                    pnl             REAL,
                    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS backtest_lessons (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    lesson_type     TEXT DEFAULT 'backtest',
                    description     TEXT,
                    lesson          TEXT,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_strategy ON backtest_runs(strategy_name);
                CREATE INDEX IF NOT EXISTS idx_runs_symbol ON backtest_runs(symbol);
                CREATE INDEX IF NOT EXISTS idx_runs_date ON backtest_runs(run_date);
                CREATE INDEX IF NOT EXISTS idx_trades_run ON backtest_trades(run_id);
                CREATE INDEX IF NOT EXISTS idx_lessons_run ON backtest_lessons(run_id);
            """)
            self._migrate(conn)
        logger.debug("[BacktestHistory] 数据库初始化完成")

    @staticmethod
    def _columns(conn, table: str = "backtest_runs") -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _select(names, existing: set, prefix: str = "") -> str:
        """The column list `names`, a column this database does not have (an older version's) as NULL."""
        return ", ".join(f"{prefix}{name}" if name in existing else f"NULL AS {name}" for name in names)

    @staticmethod
    def _migrate(conn) -> None:
        """Add the columns this version needs; a no-op when they exist."""
        for table, columns in (("backtest_runs", RUN_COLUMNS), ("backtest_trades", TRADE_COLUMNS)):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, sql_type in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_ticker ON backtest_runs(ticker)")

    # ------------------------------------------------------------------ #
    #  保存回测
    # ------------------------------------------------------------------ #

    def save_run(
        self,
        result_dict: Dict[str, Any],
        reflection: Optional[Dict] = None,
        equity_curve: Optional[List] = None,
        trades: Optional[List] = None,
        broker_config: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        notes: str = "",
        manifest: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        保存一次回测的完整结果。
        返回 run_id。

        equity_curve 按原样保存 (每日一点, 不抽样). manifest 是复现所需的运行清单
        (service.RunResult["manifest"]), result 是完整的 RunResult; 二者可省略
        (旧调用方式), 对应的新列为 NULL.
        """
        run_id = result_dict.get("run_id", datetime.now().strftime("%Y%m%d%H%M%S"))
        manifest = manifest or {}
        spec = manifest.get("spec") or {}
        facts = (result or {}).get("facts") or {}
        fees = {key: (broker_config or {}).get(key) for key in
                ("commission_buy", "commission_sell", "min_commission", "slippage_pct", "lot_size")}
        fees["fee_schedule_version"] = manifest.get("fee_schedule_version")

        with sqlite3.connect(self.db_path) as conn:
            self._migrate(conn)
            conn.execute("""
                INSERT OR REPLACE INTO backtest_runs (
                    run_id, run_date, strategy_name, strategy_class, strategy_params,
                    symbol, start_date, end_date,
                    initial_cash, final_value, total_return, annualized_return,
                    benchmark_return, alpha,
                    sharpe_ratio, sortino_ratio, calmar_ratio,
                    max_drawdown, max_drawdown_duration, volatility, var_95,
                    total_trades, win_rate, profit_factor,
                    avg_profit, avg_loss, max_single_profit, max_single_loss,
                    commission_buy, commission_sell, slippage_pct,
                    reflection_text, equity_curve_json, summary_text,
                    tags, notes,
                    ticker, market, currency, kind, adjust, warmup, min_commission, lot_size,
                    fees_json, data_sha256, engine_version, manifest_json, result_json,
                    headline_zh, caveats_json, preset, period_preset, source,
                    closed_trades, payoff_ratio, rerun_of, first_date, last_date
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
            """, (
                run_id,
                result_dict.get("run_date", datetime.now().isoformat()),
                result_dict.get("strategy_name", ""),
                result_dict.get("strategy_class", ""),
                json.dumps(result_dict.get("strategy_params", {}), ensure_ascii=False, default=str),
                result_dict.get("primary_symbol", result_dict.get("symbol", "")),
                result_dict.get("start_date", ""),
                result_dict.get("end_date", ""),
                result_dict.get("initial_cash", 0),
                result_dict.get("final_value", 0),
                result_dict.get("total_return", 0),
                result_dict.get("annualized_return", 0),
                result_dict.get("benchmark_return", 0),
                result_dict.get("alpha", 0),
                result_dict.get("sharpe_ratio", 0),
                result_dict.get("sortino_ratio", 0),
                result_dict.get("calmar_ratio", 0),
                result_dict.get("max_drawdown", 0),
                result_dict.get("max_drawdown_duration", 0),
                result_dict.get("volatility", 0),
                result_dict.get("var_95", 0),
                result_dict.get("total_trades", 0),
                result_dict.get("win_rate", 0),
                result_dict.get("profit_factor", 0),
                result_dict.get("avg_profit", 0),
                result_dict.get("avg_loss", 0),
                result_dict.get("max_single_profit", 0),
                result_dict.get("max_single_loss", 0),
                broker_config.get("commission_buy", 0.0003) if broker_config else 0.0003,
                broker_config.get("commission_sell", 0.0013) if broker_config else 0.0013,
                broker_config.get("slippage_pct", 0.0002) if broker_config else 0.0002,
                json.dumps(reflection, ensure_ascii=False) if reflection else None,
                json.dumps(equity_curve, ensure_ascii=False) if equity_curve else None,
                result_dict.get("summary", ""),
                json.dumps(tags or [], ensure_ascii=False),
                notes,
                result_dict.get("ticker") or facts.get("ticker") or spec.get("ticker"),
                facts.get("market") or spec.get("market"),
                facts.get("currency") or spec.get("currency"),
                facts.get("kind") or spec.get("kind"),
                spec.get("adjust", result_dict.get("adjust")),
                spec.get("warmup"),
                (broker_config or {}).get("min_commission"),
                (broker_config or {}).get("lot_size"),
                _json(fees),
                manifest.get("data_sha256"),
                manifest.get("engine_version", result_dict.get("engine_version")),
                _json(manifest) if manifest else None,
                _json(result) if result else None,
                (result or {}).get("headline_zh") or result_dict.get("headline_zh"),
                _json(result_dict.get("caveats")),
                (spec.get("preset") or {}).get("id") if isinstance(spec.get("preset"), dict) else None,
                (spec.get("period") or {}).get("preset") if isinstance(spec.get("period"), dict) else None,
                facts.get("source"),
                result_dict.get("closed_trades"),
                result_dict.get("payoff_ratio"),
                manifest.get("rerun_of"),
                facts.get("first_date"),
                facts.get("last_date"),
            ))

            # 保存交易明细
            if trades:
                for t in trades:
                    conn.execute("""
                        INSERT INTO backtest_trades (run_id, trade_date, symbol, direction, quantity, price,
                                                     commission, pnl, fees_json, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        run_id,
                        t.get("date", ""),
                        t.get("symbol", ""),
                        t.get("direction", ""),
                        t.get("quantity", 0),
                        t.get("price", 0),
                        t.get("commission", 0),
                        t.get("pnl", 0),
                        _json(t.get("fees")),
                        t.get("reason"),
                    ))

            # 保存教训
            if reflection and reflection.get("lessons"):
                self._insert_lessons(conn, run_id, result_dict.get("strategy_name", ""),
                                     result_dict.get("primary_symbol", ""), reflection["lessons"])

            conn.commit()

        logger.info(f"[BacktestHistory] 保存回测记录 run_id={run_id}")
        return run_id

    @staticmethod
    def _insert_lessons(conn, run_id: str, strategy_name: str, symbol: str, lessons) -> None:
        for lesson in lessons:
            conn.execute("""
                INSERT INTO backtest_lessons (run_id, lesson_type, description, lesson)
                VALUES (?, ?, ?, ?)
            """, (run_id, "backtest", f"{strategy_name} @ {symbol}", lesson))

    def update_reflection(self, run_id: str, reflection: Dict[str, Any]) -> bool:
        """Store a reflection made after the run was saved (replaces earlier lessons)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT strategy_name, symbol FROM backtest_runs WHERE run_id = ?",
                               (run_id,)).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE backtest_runs SET reflection_text = ? WHERE run_id = ?",
                         (json.dumps(reflection, ensure_ascii=False), run_id))
            conn.execute("DELETE FROM backtest_lessons WHERE run_id = ?", (run_id,))
            self._insert_lessons(conn, run_id, row[0], row[1], reflection.get("lessons") or [])
            conn.commit()
        return True

    # ------------------------------------------------------------------ #
    #  查询接口
    # ------------------------------------------------------------------ #

    def list_runs(
        self,
        strategy_name: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "run_date DESC",
    ) -> List[Dict]:
        """查询回测历史列表 (symbol 匹配保存时的写法或规范代码)"""
        if self.absent:
            return []
        conditions = []
        params = []

        if strategy_name:
            conditions.append("strategy_name LIKE ?")
            params.append(f"%{strategy_name}%")
        # 防 SQL 注入: 白名单校验 order_by
        allowed_order_columns = [
            "run_date DESC", "run_date ASC", "total_return DESC",
            "annualized_return DESC", "sharpe_ratio DESC"
        ]
        if order_by not in allowed_order_columns:
            order_by = "run_date DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = self._columns(conn)
            if symbol:
                if "ticker" in existing:
                    conditions.append("(symbol = ? OR ticker = ?)")
                    params.extend([symbol, _canonical(symbol) or symbol])
                else:
                    conditions.append("symbol = ?")
                    params.append(symbol)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            listed = self._select((
                "run_id", "run_date", "strategy_name", "symbol", "initial_cash", "final_value", "total_return",
                "annualized_return", "sharpe_ratio", "max_drawdown", "total_trades", "win_rate", "profit_factor",
                "tags", "notes", "starred", "ticker", "market", "currency", "adjust", "start_date", "end_date",
                "headline_zh", "preset", "period_preset", "closed_trades", "data_sha256", "engine_version",
                "rerun_of", "first_date", "last_date"), existing)
            rows = conn.execute(f"""
                SELECT {listed}
                FROM backtest_runs
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """, params + [limit, offset]).fetchall()

        return [{**dict(row), "primary_symbol": row["symbol"]} for row in rows]

    def get_run(self, run_id: str) -> Optional[Dict]:
        """获取单次回测完整结果"""
        if self.absent:
            return None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()

            if not row:
                return None

            result = dict(row)

            # 解析JSON字段
            for field in ["strategy_params", "equity_curve_json", "reflection_text", "tags"]:
                if result.get(field):
                    try:
                        result[field] = json.loads(result[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            for field, alias in _JSON_ALIASES.items():
                raw = result.pop(field, None)
                try:
                    result[alias] = json.loads(raw) if raw else None
                except (json.JSONDecodeError, TypeError):
                    result[alias] = None
            # Field names the history page reads.
            result["primary_symbol"] = result.get("symbol")
            result["equity_curve"] = result.get("equity_curve_json")
            result["reflection"] = result.get("reflection_text")

            # 加载交易明细
            trades = conn.execute(
                "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY trade_date",
                (run_id,)
            ).fetchall()
            result["trades"] = []
            for t in trades:
                trade = dict(t)
                raw = trade.pop("fees_json", None)
                try:
                    trade["fees"] = json.loads(raw) if raw else None
                except (json.JSONDecodeError, TypeError):
                    trade["fees"] = None
                result["trades"].append(trade)

            # 加载教训
            lessons = conn.execute(
                "SELECT * FROM backtest_lessons WHERE run_id = ?", (run_id,)
            ).fetchall()
            result["lessons"] = [dict(l) for l in lessons]

        return result

    def delete_run(self, run_id: str) -> bool:
        """删除一次回测记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM backtest_lessons WHERE run_id = ?", (run_id,))
            cursor = conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", (run_id,))
            conn.commit()
        return cursor.rowcount > 0

    def toggle_star(self, run_id: str) -> bool:
        """切换收藏标记"""
        with sqlite3.connect(self.db_path) as conn:
            current = conn.execute(
                "SELECT starred FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not current:
                return False
            new_val = 0 if current[0] else 1
            conn.execute(
                "UPDATE backtest_runs SET starred = ? WHERE run_id = ?",
                (new_val, run_id)
            )
            conn.commit()
        return bool(new_val)

    def update_notes(self, run_id: str, notes: str):
        """更新回测备注"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE backtest_runs SET notes = ? WHERE run_id = ?",
                (notes, run_id)
            )
            conn.commit()

    def compare_runs(self, run_ids: List[str]) -> List[Dict]:
        """对比多次回测结果"""
        if self.absent:
            return []
        results = []
        for rid in run_ids:
            run = self.get_run(rid)
            if run:
                results.append({
                    "run_id": run["run_id"],
                    "run_date": run["run_date"],
                    "strategy_name": run["strategy_name"],
                    "symbol": run["symbol"],
                    "primary_symbol": run["symbol"],
                    "ticker": run.get("ticker"),
                    "strategy_params": run.get("strategy_params", {}),
                    "total_return": run["total_return"],
                    "annualized_return": run["annualized_return"],
                    "sharpe_ratio": run["sharpe_ratio"],
                    "max_drawdown": run["max_drawdown"],
                    "win_rate": run["win_rate"],
                    "profit_factor": run["profit_factor"],
                    "total_trades": run["total_trades"],
                    "closed_trades": run.get("closed_trades"),
                    "start_date": run.get("start_date"),
                    "end_date": run.get("end_date"),
                    "first_date": run.get("first_date"),
                    "last_date": run.get("last_date"),
                    "adjust": run.get("adjust"),
                    "data_sha256": run.get("data_sha256"),
                })
        return results

    def get_best_runs(
        self,
        strategy_name: Optional[str] = None,
        symbol: Optional[str] = None,
        metric: str = "sharpe_ratio",
        top_n: int = 10,
    ) -> List[Dict]:
        """获取最优参数组合排行"""
        if self.absent:
            return []
        conditions = ["total_trades > 5"]  # 至少5笔交易才有统计意义
        params = []
        if strategy_name:
            conditions.append("strategy_name LIKE ?")
            params.append(f"%{strategy_name}%")

        # 防 SQL 注入: 白名单校验 metric
        allowed_metrics = [
            "total_return", "annualized_return", "sharpe_ratio",
            "sortino_ratio", "calmar_ratio", "profit_factor", "win_rate"
        ]
        if metric not in allowed_metrics:
            metric = "sharpe_ratio"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = self._columns(conn)
            if symbol:
                if "ticker" in existing:
                    conditions.append("(symbol = ? OR ticker = ?)")
                    params.extend([symbol, _canonical(symbol) or symbol])
                else:
                    conditions.append("symbol = ?")
                    params.append(symbol)
            where = f"WHERE {' AND '.join(conditions)}"
            ticker = "ticker" if "ticker" in existing else "NULL AS ticker"
            rows = conn.execute(f"""
                SELECT run_id, run_date, strategy_name, symbol, strategy_params,
                       total_return, annualized_return, sharpe_ratio, max_drawdown,
                       win_rate, profit_factor, total_trades, {ticker}
                FROM backtest_runs
                {where}
                ORDER BY {metric} DESC
                LIMIT ?
            """, params + [top_n]).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            d["primary_symbol"] = d["symbol"]
            if d.get("strategy_params"):
                try:
                    d["strategy_params"] = json.loads(d["strategy_params"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results

    def get_stats(self) -> Dict:
        """获取回测历史统计"""
        if self.absent:
            return {"total_runs": 0, "distinct_strategies": 0, "distinct_symbols": 0, "total_lessons": 0,
                    "starred_runs": 0, "starred_count": 0, "db_path": self.db_path}
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            strategies = conn.execute("SELECT COUNT(DISTINCT strategy_name) FROM backtest_runs").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM backtest_runs").fetchone()[0]
            lessons = conn.execute("SELECT COUNT(*) FROM backtest_lessons").fetchone()[0]
            starred = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE starred = 1").fetchone()[0]
        return {
            "total_runs": total,
            "distinct_strategies": strategies,
            "distinct_symbols": symbols,
            "total_lessons": lessons,
            "starred_runs": starred,
            "starred_count": starred,
            "db_path": self.db_path,
        }

    def get_all_lessons(self, limit: int = 100) -> List[Dict]:
        """获取所有回测教训（从专用教训表）"""
        if self.absent:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            ticker = "r.ticker" if "ticker" in self._columns(conn) else "NULL AS ticker"
            rows = conn.execute(f"""
                SELECT l.*, r.strategy_name, r.symbol, r.total_return, r.sharpe_ratio,
                       {ticker}, r.run_date AS run_date
                FROM backtest_lessons l
                JOIN backtest_runs r ON l.run_id = r.run_id
                ORDER BY l.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [{**dict(r), "primary_symbol": r["symbol"]} for r in rows]


# ------------------------------------------------------------------ #
#  Records saved without a RunResult (v3.2.1 and earlier)
# ------------------------------------------------------------------ #

LEGACY_SAVED_BY = "v3.2.1 或更早"
LEGACY_LABEL = f"旧版本（{LEGACY_SAVED_BY}）"
# (key, what is missing, its short name); key names the RunResult part (or kind of detail).
LEGACY_UNAVAILABLE = (
    ("manifest", "运行清单（设置、数据指纹、引擎和费用表版本）：不能按原设置重跑，也不能核对当时用的行情", "运行清单"),
    ("benchmark", "“一直持有”和指数的对比", "对照收益"),
    ("round_trips", "每一笔完整交易（买入到卖出）的明细", "完整交易明细"),
    ("unfilled", "没有成交的信号", "未成交信号"),
    ("end_mark", "期末仍持有的仓位按最后收盘价估值的标记", "期末估值标记"),
    ("prices", "回测用的 K 线", "K 线"),
    ("monthly_returns", "月度收益", "月度收益"),
    ("equity_daily", "每日净值：旧版本只保存了最多约 300 个抽样点", "每日净值"),
    ("metrics", "夏普比率、胜率等其余指标：旧版本算不出时记为 0，和真实的 0 分不开，这里不显示"
                "（保存的原值在 legacy.saved_metrics）", "其余指标"),
    ("fills_detail", "每笔成交的费用明细和成交原因", "成交的费用明细和原因"),
)
# The other statistics of a RunResult's metrics: v3.2.1 wrote 0 where it had none.
LEGACY_UNSHOWN_METRICS = (
    "benchmark_return", "alpha", "max_drawdown_duration", "volatility", "var_95", "downside_deviation",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio", "total_trades", "closed_trades", "winning_trades",
    "losing_trades", "breakeven_trades", "win_rate", "payoff_ratio", "profit_factor", "end_mark_pnl",
    "avg_profit", "avg_loss", "max_single_profit", "max_single_loss", "avg_holding_days",
)
_LEGACY_SAVED_METRICS = ("benchmark_return", "alpha", "sharpe_ratio", "sortino_ratio", "calmar_ratio",
                         "max_drawdown_duration", "volatility", "var_95", "total_trades", "win_rate",
                         "profit_factor", "avg_profit", "avg_loss", "max_single_profit", "max_single_loss")


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and value not in (float("inf"), float("-inf")) else None


def legacy_result(saved: Dict[str, Any]) -> Dict[str, Any]:
    """A record saved without a RunResult (get_run of a v3.2.1 row) in the RunResult's shape, read-only.

    Only what the record holds is filled in: the capital, final value, total and annual return and
    maximum drawdown it saved, the sampled net-value curve (as equity) and the trades (as fills).
    Everything else is null or empty, and ``legacy`` says so:
    {"saved_by", "note_zh", "unavailable": [{"key", "label_zh"}], "saved_metrics": {the other
    statistics exactly as saved, where v3.2.1 wrote 0 for "none"}}. The caveat legacy_result
    (warning) says it in the result too. Nothing is written."""
    from backtesting.summary import MARKET_LABELS, METRIC_NOTES, pct, span_label
    symbol = saved.get("symbol") or ""
    try:
        from backtesting.data.symbols import resolve
        sym = resolve(symbol)
    except Exception:
        sym = None
    capital = _number(saved.get("initial_cash"))
    capital = capital if capital and capital > 0 else None
    curve = saved.get("equity_curve_json") if isinstance(saved.get("equity_curve_json"), list) else []
    equity = []
    for point in curve:
        net = _number(point.get("net_value")) if isinstance(point, dict) else None
        if net is None or not point.get("date"):
            continue
        equity.append({"date": str(point["date"])[:10],
                       "total_value": round(net * capital, 2) if capital is not None else None,
                       "net_value": net, "drawdown": _number(point.get("drawdown"))})
    first, last = (equity[0]["date"], equity[-1]["date"]) if equity else (None, None)
    total = _number(saved.get("total_return")) if capital is not None else None
    annual = _number(saved.get("annualized_return")) if first and last and first != last else None
    drawdown = _number(saved.get("max_drawdown")) if equity else None
    fills = [{"date": str(trade.get("trade_date") or "")[:10], "symbol": trade.get("symbol"),
              "direction": trade.get("direction"), "quantity": trade.get("quantity"), "price": trade.get("price"),
              "commission": trade.get("commission"), "pnl": trade.get("pnl"), "reason": None, "fees": None}
             for trade in saved.get("trades") or []]
    params = saved.get("strategy_params") if isinstance(saved.get("strategy_params"), dict) else {}
    name = saved.get("strategy_name") or None
    market = sym.market if sym is not None else None
    facts = {
        "ticker": sym.ticker if sym is not None else symbol, "label": symbol, "name": None,
        "market": market, "market_label": MARKET_LABELS.get(market) if market else None,
        "currency": sym.currency if sym is not None else None, "kind": sym.kind if sym is not None else None,
        "source": None, "demo": False,
        "strategy": {"kind": None, "id": name, "name": name, "class": saved.get("strategy_class") or None,
                     "params": params},
        "period": {"preset": None, "requested_start": saved.get("start_date") or None,
                   "requested_end": saved.get("end_date") or None, "first_date": first, "last_date": last,
                   "calendar_days": None, "bars": None,
                   "span_label": span_label(first, last) if first and last else None},
        "first_date": first, "last_date": last, "capital": capital,
        "final_value": _number(saved.get("final_value")) if capital is not None else None,
        "total_return": total, "annualized_return": annual,
        "same_stock_return": None, "same_stock_reason": f"{LEGACY_LABEL}保存的记录没有“一直持有”的对照。",
        "index_ticker": None, "index_name": None, "index_return": None,
        "index_reason": f"{LEGACY_LABEL}保存的记录没有指数对比。",
        "max_drawdown": drawdown, "max_drawdown_peak": None, "max_drawdown_trough": None,
        "closed_trades": None, "winning_trades": None, "losing_trades": None, "open_at_end": None,
        "fills": len(fills), "fees_total": None, "end_mark": None, "adjust": None,
        "warmup_bars": None, "warmup_auto": None,
        "commission": {"buy": _number(saved.get("commission_buy")), "sell": _number(saved.get("commission_sell")),
                       "min": None},
        "slippage_pct": _number(saved.get("slippage_pct")),
    }
    unavailable = [{"key": key, "label_zh": label if key != "equity_daily" or equity else "净值曲线"}
                   for key, label, _ in LEGACY_UNAVAILABLE]
    missing = "、".join(short for _, _, short in LEGACY_UNAVAILABLE)
    note = (f"这是{LEGACY_LABEL}保存的回测，只保留了当时的汇总指标、抽样净值和成交明细；当时的费用、复权、预热和"
            f"数据规则与现在不同，结果不能和现在的回测直接比较，也不能重跑。没有的部分：{missing}。")
    text = f"{LEGACY_LABEL}保存的回测，" + (f"{first} 至 {last}（{span_label(first, last)}）" if first and last
                                               else "区间不详")
    text += f"，这个方法收益 {pct(total)}" if total is not None else "，收益不可用"
    if drawdown is not None and drawdown < 0:
        digits = next((d for d in (1, 2, 3, 4) if round(drawdown * 100, d) != 0), None)
        text += f"，最大回撤 {pct(drawdown, digits)}" if digits is not None else "，最大回撤不到 0.0001%"
    text += (f"，共 {len(fills)} 笔成交。" if fills else "，没有成交记录。")
    text += "这条记录只保留了汇总指标、抽样净值和成交明细，没有“一直持有”和指数的对比。"
    metrics: Dict[str, Any] = {"final_value": facts["final_value"], "total_return": total,
                               "annualized_return": annual, "max_drawdown": drawdown}
    metrics.update({key: None for key in LEGACY_UNSHOWN_METRICS})
    metrics["undefined"] = {key: f"{LEGACY_LABEL}保存的记录：这个指标算不出时记为 0，和真实的 0 分不开，这里不显示"
                            for key in LEGACY_UNSHOWN_METRICS}
    for key, value in (("final_value", metrics["final_value"]), ("total_return", total),
                       ("annualized_return", annual), ("max_drawdown", drawdown)):
        if value is None:
            metrics["undefined"][key] = f"{LEGACY_LABEL}保存的记录里没有可用的值"
    return {
        "run_id": saved.get("run_id"), "created_at": saved.get("created_at") or saved.get("run_date"),
        "legacy": {"saved_by": LEGACY_SAVED_BY, "note_zh": note, "unavailable": unavailable,
                   "saved_metrics": {key: saved.get(key) for key in _LEGACY_SAVED_METRICS}},
        "facts": facts, "headline_zh": text,
        "caveats": [{"code": "legacy_result", "level": "warning", "text": note}],
        "metrics": metrics, "metric_notes": METRIC_NOTES,
        "equity": equity, "benchmark": {"same_stock": None, "index": None},
        "round_trips": [], "fills": fills, "unfilled": [], "warmup": None,
        "assumptions": {"line_zh": f"按{LEGACY_LABEL}当时的费用和成交规则计算", "slippage_pct": facts["slippage_pct"]},
        "rerun": None, "end_mark": None,
        "manifest": {"legacy": True, "engine_version": None, "spec": None, "coverage": [], "data_sha256": None},
    }


class EmptyHistoryStore:
    """The history of a mode that keeps none (the demo): every read is empty, and no
    database is opened or created. Writes are refused."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    def save_run(self, *args, **kwargs):
        raise RuntimeError("演示模式不保存回测历史")

    def update_reflection(self, run_id: str, reflection: Dict[str, Any]) -> bool:
        return False

    def list_runs(self, *args, **kwargs) -> List[Dict]:
        return []

    def get_run(self, run_id: str) -> Optional[Dict]:
        return None

    def delete_run(self, run_id: str) -> bool:
        return False

    def toggle_star(self, run_id: str) -> bool:
        return False

    def update_notes(self, run_id: str, notes: str):
        return None

    def compare_runs(self, run_ids: List[str]) -> List[Dict]:
        return []

    def get_best_runs(self, *args, **kwargs) -> List[Dict]:
        return []

    def get_stats(self) -> Dict:
        return {"total_runs": 0, "distinct_strategies": 0, "distinct_symbols": 0, "total_lessons": 0,
                "starred_runs": 0, "starred_count": 0, "db_path": self.db_path, "kept": False}

    def get_all_lessons(self, limit: int = 100) -> List[Dict]:
        return []


def open_history():
    """The history store of this mode: the configured database, or in demo mode an
    EmptyHistoryStore (a demo run completes in memory: nothing is saved, and reading
    the history creates nothing)."""
    from backtesting.storage import demo_mode, history_db_path
    if demo_mode():
        return EmptyHistoryStore(history_db_path())
    return BacktestHistoryStore()
