"""
数据库存储层 - 本地缓存历史行情数据
灵感: vnpy 的 database 模块 (SQLite 适配器)

使用 SQLite 作为轻量级本地存储,避免每次回测都重新下载数据.
"""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import pandas as pd

logger = logging.getLogger(__name__)


class DataStore:
    """
    SQLite 本地数据存储
    
    表结构:
        daily_bars (symbol, date, open, high, low, close, volume, amount, 
                    pct_change, turnover, adjust)
    """

    DB_FILE = "data/backtesting.db"

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 相对于 trading-assistant 目录
            base = Path(__file__).parent.parent.parent  # trading-assistant/
            db_path = str(base / "data" / "backtesting.db")

        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ #
    #  公开 API
    # ------------------------------------------------------------------ #

    def save_daily(
        self,
        symbol: str,
        df: pd.DataFrame,
        adjust: str = "qfq",
    ) -> int:
        """
        保存日 K 线到数据库.
        返回实际写入行数.
        """
        if df.empty:
            return 0

        df = df.copy().reset_index()  # 将 date index 还原为列
        df["symbol"] = symbol
        df["adjust"] = adjust

        with sqlite3.connect(self.db_path) as conn:
            # 先删除已存在的同 symbol+adjust+date 数据 (upsert)
            rows = []
            for _, row in df.iterrows():
                date_str = row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])
                rows.append((
                    symbol,
                    date_str,
                    float(row.get("open", 0)),
                    float(row.get("high", 0)),
                    float(row.get("low", 0)),
                    float(row.get("close", 0)),
                    float(row.get("volume", 0)),
                    float(row.get("amount", 0) if "amount" in row else 0),
                    float(row.get("pct_change", 0) if "pct_change" in row else 0),
                    float(row.get("turnover", 0) if "turnover" in row else 0),
                    adjust,
                ))

            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, amount,
                 pct_change, turnover, adjust)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

        logger.info(f"[DataStore] 保存 {symbol} {len(rows)} 条日 K 线")
        return len(rows)

    def load_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """
        从数据库加载日 K 线.
        返回 DataFrame (date 为 DatetimeIndex).
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close, volume, amount,
                       pct_change, turnover
                FROM daily_bars
                WHERE symbol = ?
                  AND adjust = ?
                  AND date >= ?
                  AND date <= ?
                ORDER BY date ASC
                """,
                conn,
                params=(symbol, adjust, start_date, end_date),
                parse_dates=["date"],
            )

        if not df.empty:
            df = df.set_index("date")

        return df

    def get_available_symbols(self) -> List[str]:
        """查询数据库中已有数据的所有标的"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol")
            return [row[0] for row in cursor.fetchall()]

    def get_date_range(self, symbol: str, adjust: str = "qfq"):
        """查询某标的在数据库中的日期范围"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily_bars WHERE symbol=? AND adjust=?",
                (symbol, adjust),
            )
            row = cursor.fetchone()
            return row[0], row[1]

    def is_data_available(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        min_rows: int = 10,
    ) -> bool:
        """检查本地数据是否足够"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM daily_bars
                WHERE symbol=? AND adjust=? AND date>=? AND date<=?
                """,
                (symbol, adjust, start_date, end_date),
            )
            count = cursor.fetchone()[0]
        return count >= min_rows

    def delete_symbol(self, symbol: str) -> int:
        """删除某标的的所有数据"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM daily_bars WHERE symbol=?", (symbol,)
            )
            conn.commit()
        logger.info(f"[DataStore] 删除 {symbol} 的 {cursor.rowcount} 条数据")
        return cursor.rowcount

    def get_stats(self) -> dict:
        """获取数据库统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            symbols = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM daily_bars"
            ).fetchone()[0]
            total_rows = conn.execute(
                "SELECT COUNT(*) FROM daily_bars"
            ).fetchone()[0]
            date_range = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily_bars"
            ).fetchone()

        return {
            "symbols": symbols,
            "total_rows": total_rows,
            "earliest_date": date_range[0],
            "latest_date": date_range[1],
            "db_path": self.db_path,
        }

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _init_db(self):
        """初始化数据表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol      TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    open        REAL,
                    high        REAL,
                    low         REAL,
                    close       REAL,
                    volume      REAL,
                    amount      REAL,
                    pct_change  REAL,
                    turnover    REAL,
                    adjust      TEXT DEFAULT 'qfq',
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (symbol, date, adjust)
                )
                """
            )
            # 创建索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_bars(symbol, date)"
            )
            conn.commit()

        logger.debug(f"[DataStore] 数据库初始化完成: {self.db_path}")
