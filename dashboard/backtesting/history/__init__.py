"""
回测记录存储 - 独立的回测历史数据库
与通用 memory.json 分离，专门存放每次回测的完整结果

功能:
  - 保存每次回测的完整结果 (策略、参数、统计、反思)
  - 按策略/标的/时间查询历史回测
  - 对比多次回测结果
  - 标记最优参数组合
"""
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


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
            base = Path(__file__).parent.parent.parent  # trading-assistant/
            db_path = str(base / "data" / "backtest_history.db")
        self.db_path = db_path
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
        logger.debug("[BacktestHistory] 数据库初始化完成")

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
    ) -> str:
        """
        保存一次回测的完整结果。
        返回 run_id。
        """
        run_id = result_dict.get("run_id", datetime.now().strftime("%Y%m%d%H%M%S"))
        
        with sqlite3.connect(self.db_path) as conn:
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
                    tags, notes
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
                    ?, ?
                )
            """, (
                run_id,
                result_dict.get("run_date", datetime.now().isoformat()),
                result_dict.get("strategy_name", ""),
                result_dict.get("strategy_class", ""),
                json.dumps(result_dict.get("strategy_params", {}), ensure_ascii=False),
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
            ))

            # 保存交易明细
            if trades:
                for t in trades:
                    conn.execute("""
                        INSERT INTO backtest_trades (run_id, trade_date, symbol, direction, quantity, price, commission, pnl)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        run_id,
                        t.get("date", ""),
                        t.get("symbol", ""),
                        t.get("direction", ""),
                        t.get("quantity", 0),
                        t.get("price", 0),
                        t.get("commission", 0),
                        t.get("pnl", 0),
                    ))

            # 保存教训
            if reflection and reflection.get("lessons"):
                for lesson in reflection["lessons"]:
                    conn.execute("""
                        INSERT INTO backtest_lessons (run_id, lesson_type, description, lesson)
                        VALUES (?, ?, ?, ?)
                    """, (
                        run_id,
                        "backtest",
                        f"{result_dict.get('strategy_name', '')} @ {result_dict.get('primary_symbol', '')}",
                        lesson,
                    ))

            conn.commit()

        logger.info(f"[BacktestHistory] 保存回测记录 run_id={run_id}")
        return run_id

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
        """查询回测历史列表"""
        conditions = []
        params = []

        if strategy_name:
            conditions.append("strategy_name LIKE ?")
            params.append(f"%{strategy_name}%")
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # 防 SQL 注入: 白名单校验 order_by
        allowed_order_columns = [
            "run_date DESC", "run_date ASC", "total_return DESC", 
            "annualized_return DESC", "sharpe_ratio DESC"
        ]
        if order_by not in allowed_order_columns:
            order_by = "run_date DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT run_id, run_date, strategy_name, symbol, 
                       initial_cash, final_value, total_return, annualized_return,
                       sharpe_ratio, max_drawdown, total_trades, win_rate,
                       profit_factor, tags, notes, starred
                FROM backtest_runs
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """, params + [limit, offset]).fetchall()

        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> Optional[Dict]:
        """获取单次回测完整结果"""
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

            # 加载交易明细
            trades = conn.execute(
                "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY trade_date",
                (run_id,)
            ).fetchall()
            result["trades"] = [dict(t) for t in trades]

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
        results = []
        for rid in run_ids:
            run = self.get_run(rid)
            if run:
                results.append({
                    "run_id": run["run_id"],
                    "run_date": run["run_date"],
                    "strategy_name": run["strategy_name"],
                    "symbol": run["symbol"],
                    "strategy_params": run.get("strategy_params", {}),
                    "total_return": run["total_return"],
                    "annualized_return": run["annualized_return"],
                    "sharpe_ratio": run["sharpe_ratio"],
                    "max_drawdown": run["max_drawdown"],
                    "win_rate": run["win_rate"],
                    "profit_factor": run["profit_factor"],
                    "total_trades": run["total_trades"],
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
        conditions = ["total_trades > 5"]  # 至少5笔交易才有统计意义
        params = []
        if strategy_name:
            conditions.append("strategy_name LIKE ?")
            params.append(f"%{strategy_name}%")
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)

        where = f"WHERE {' AND '.join(conditions)}"

        # 防 SQL 注入: 白名单校验 metric
        allowed_metrics = [
            "total_return", "annualized_return", "sharpe_ratio", 
            "sortino_ratio", "calmar_ratio", "profit_factor", "win_rate"
        ]
        if metric not in allowed_metrics:
            metric = "sharpe_ratio"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT run_id, run_date, strategy_name, symbol, strategy_params,
                       total_return, annualized_return, sharpe_ratio, max_drawdown,
                       win_rate, profit_factor, total_trades
                FROM backtest_runs
                {where}
                ORDER BY {metric} DESC
                LIMIT ?
            """, params + [top_n]).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            if d.get("strategy_params"):
                try:
                    d["strategy_params"] = json.loads(d["strategy_params"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results

    def get_stats(self) -> Dict:
        """获取回测历史统计"""
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
            "db_path": self.db_path,
        }

    def get_all_lessons(self, limit: int = 100) -> List[Dict]:
        """获取所有回测教训（从专用教训表）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT l.*, r.strategy_name, r.symbol, r.total_return, r.sharpe_ratio
                FROM backtest_lessons l
                JOIN backtest_runs r ON l.run_id = r.run_id
                ORDER BY l.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
