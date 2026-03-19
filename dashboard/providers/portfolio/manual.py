"""
手动持仓管理 Provider
支持从文件导入或手动维护持仓
"""
from typing import List, Optional
from datetime import datetime
import asyncio
import json
import os
from pathlib import Path

from core.data.base import PortfolioProvider, MarketDataProvider
from core.models import Portfolio, Position, Stock, Market, PositionSide


class ManualPortfolioProvider(PortfolioProvider):
    """
    手动持仓管理

    支持：
    1. 从 JSON 文件加载持仓
    2. 手动添加/删除/修改持仓
    3. 自动保存持仓变更
    """

    def __init__(
        self,
        data_file: str = "portfolio.json",
        market_provider: Optional[MarketDataProvider] = None
    ):
        self.data_file = Path(data_file)
        self.market_provider = market_provider
        self._portfolio = Portfolio()
        self._last_update: Optional[datetime] = None
        self._load()

    @property
    def name(self) -> str:
        return "手动管理"

    @property
    def last_update(self) -> Optional[datetime]:
        return self._last_update

    def _load(self):
        """从文件加载持仓"""
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                positions = []
                for p in data.get("positions", []):
                    stock = Stock(
                        symbol=p["symbol"],
                        name=p["name"],
                        market=Market(p.get("market", "a_share"))
                    )
                    positions.append(Position(
                        stock=stock,
                        quantity=p["quantity"],
                        available_qty=p.get("available_qty", p["quantity"]),
                        cost_price=p["cost_price"],
                        current_price=p.get("current_price", p["cost_price"]),
                        side=PositionSide(p.get("side", "long"))
                    ))

                self._portfolio = Portfolio(
                    positions=positions,
                    cash=data.get("cash", 0.0)
                )
                self._last_update = datetime.now()
            except Exception as e:
                print(f"加载持仓文件失败: {e}")

    def _save(self):
        """保存持仓到文件"""
        data = {
            "positions": [
                {
                    "symbol": p.stock.symbol,
                    "name": p.stock.name,
                    "market": p.stock.market.value,
                    "quantity": p.quantity,
                    "available_qty": p.available_qty,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                    "side": p.side.value
                }
                for p in self._portfolio.positions
            ],
            "cash": self._portfolio.cash,
            "updated_at": datetime.now().isoformat()
        }

        # 确保目录存在
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def get_portfolio(self) -> Portfolio:
        """获取当前持仓"""
        return self._portfolio

    async def get_positions(self) -> List[Position]:
        """获取持仓列表"""
        return self._portfolio.positions

    async def refresh(self) -> None:
        """刷新持仓数据（更新当前价格）"""
        if not self.market_provider or not self._portfolio.positions:
            return

        positions_by_market: dict[Market, List[Position]] = {}
        for position in self._portfolio.positions:
            positions_by_market.setdefault(position.stock.market, []).append(position)

        async def refresh_market_positions(market: Market, positions: List[Position]) -> None:
            symbols = [position.stock.symbol for position in positions]
            quote_map = {}

            try:
                quotes = await self.market_provider.get_quotes(symbols, market)
                quote_map = {quote.stock.symbol: quote for quote in quotes}
            except Exception as e:
                print(f"批量刷新 {market.value} 失败: {e}")

            for position in positions:
                quote = quote_map.get(position.stock.symbol)
                if quote is None:
                    try:
                        quote = await self.market_provider.get_quote(
                            position.stock.symbol,
                            position.stock.market
                        )
                    except Exception as e:
                        print(f"刷新 {position.stock.symbol} 价格失败: {e}")
                        quote = None

                if quote:
                    position.current_price = quote.price

        await asyncio.gather(
            *(refresh_market_positions(market, positions) for market, positions in positions_by_market.items())
        )
        self._last_update = datetime.now()
        self._save()

    # 手动管理方法
    def add_position(
        self,
        symbol: str,
        name: str,
        quantity: int,
        cost_price: float,
        market: Market = Market.A_SHARE,
        available_qty: Optional[int] = None,
        current_price: Optional[float] = None
    ):
        """添加持仓（如果已存在则更新）"""
        # 检查是否已存在
        existing_idx = None
        for idx, p in enumerate(self._portfolio.positions):
            if p.stock.symbol == symbol:
                existing_idx = idx
                break

        stock = Stock(symbol=symbol, name=name, market=market)
        position = Position(
            stock=stock,
            quantity=quantity,
            available_qty=available_qty if available_qty is not None else quantity,
            cost_price=cost_price,
            current_price=current_price if current_price is not None else cost_price
        )

        if existing_idx is not None:
            # 更新现有持仓
            self._portfolio.positions[existing_idx] = position
        else:
            # 添加新持仓
            self._portfolio.positions.append(position)

        self._save()

    def remove_position(self, symbol: str):
        """删除持仓"""
        self._portfolio.positions = [
            p for p in self._portfolio.positions
            if p.stock.symbol != symbol
        ]
        self._save()

    def update_position(
        self,
        symbol: str,
        quantity: Optional[int] = None,
        cost_price: Optional[float] = None,
        available_qty: Optional[int] = None
    ):
        """更新持仓"""
        for p in self._portfolio.positions:
            if p.stock.symbol == symbol:
                if quantity is not None:
                    p.quantity = quantity
                if cost_price is not None:
                    p.cost_price = cost_price
                if available_qty is not None:
                    p.available_qty = available_qty
                break
        self._save()

    def set_cash(self, cash: float):
        """设置现金"""
        self._portfolio.cash = cash
        self._save()

    def import_from_csv(self, csv_file: str):
        """
        从 CSV 导入持仓

        CSV 格式：
        代码,名称,数量,成本价,可卖数量
        000001,平安银行,1000,12.50,1000
        """
        import csv

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.add_position(
                    symbol=row["代码"],
                    name=row["名称"],
                    quantity=int(row["数量"]),
                    cost_price=float(row["成本价"]),
                    available_qty=int(row.get("可卖数量", row["数量"]))
                )
