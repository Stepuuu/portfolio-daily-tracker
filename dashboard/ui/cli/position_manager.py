"""
持仓管理增强功能 — 截图导入、验证、冲突合并
"""
from typing import List, Dict, Any, Optional
from rich.table import Table
import asyncio


class PositionManager:
    """持仓数据管理器"""

    def __init__(self, console, portfolio_provider, market_provider):
        self.console = console
        self.portfolio_provider = portfolio_provider
        self.market_provider = market_provider

    async def review_and_import_positions(
        self,
        recognized_positions: List[Dict[str, Any]],
        recognized_cash: float = 0.0
    ) -> bool:
        """
        审查并导入识别的持仓

        返回是否成功导入
        """
        if not recognized_positions:
            self.console.print("[yellow]未识别到持仓信息[/yellow]")
            return False

        # 1. 显示识别结果表格
        self._show_recognized_positions(recognized_positions, recognized_cash)

        # 2. 验证股票代码
        validated_positions = await self._validate_positions(recognized_positions)

        if not validated_positions:
            self.console.print("[red]所有持仓验证失败[/red]")
            return False

        # 3. 让用户选择要导入的持仓
        selected_positions = await self._select_positions(validated_positions)

        if not selected_positions:
            self.console.print("[yellow]未选择任何持仓[/yellow]")
            return False

        # 4. 让用户编辑数据（如果需要）
        edit_choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("是否需要编辑数据？(y/n) [n]: ")
        )
        if edit_choice.strip().lower() in ['y', 'yes']:
            selected_positions = await self._edit_positions(selected_positions)

        # 5. 检查是否有重复持仓
        conflicts = await self._check_conflicts(selected_positions)
        merge_strategy = "merge"

        if conflicts:
            merge_strategy = await self._handle_conflicts(conflicts)
            if merge_strategy == "cancel":
                self.console.print("[yellow]已取消导入[/yellow]")
                return False

        # 6. 最终确认
        final_choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("\n确认导入以上持仓？(y/n) [y]: ")
        )
        if final_choice.strip().lower() not in ['', 'y', 'yes']:
            self.console.print("[yellow]已取消导入[/yellow]")
            return False

        # 7. 执行导入
        return await self._execute_import(selected_positions, recognized_cash, conflicts, merge_strategy)

    def _show_recognized_positions(self, positions: List[Dict], cash: float):
        """显示识别结果"""
        table = Table(title="识别结果", show_header=True, header_style="bold cyan")
        table.add_column("序号", justify="center", style="dim")
        table.add_column("股票代码", style="yellow")
        table.add_column("股票名称", style="green")
        table.add_column("数量", justify="right")
        table.add_column("成本价", justify="right")
        table.add_column("可卖数量", justify="right")

        for idx, p in enumerate(positions, 1):
            table.add_row(
                str(idx),
                p['symbol'],
                p['name'],
                str(p['quantity']),
                f"¥{p['cost_price']:.2f}",
                str(p.get('available_qty', p['quantity']))
            )

        self.console.print(table)

        if cash > 0:
            self.console.print(f"\n[cyan]识别到可用资金: ¥{cash:,.2f}[/cyan]")

    async def _validate_positions(self, positions: List[Dict]) -> List[Dict]:
        """验证股票代码和价格"""
        validated = []

        for idx, p in enumerate(positions, 1):
            self.console.print(f"\n[cyan]验证 {idx}/{len(positions)}: {p['name']}({p['symbol']})[/cyan]", end=" ")

            # 验证股票代码格式
            if not self._is_valid_stock_code(p['symbol']):
                self.console.print("[red]✗ 代码格式错误[/red]")
                fix_choice = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.console.input(f"  是否手动修正代码？(y/n) [y]: ")
                )
                if fix_choice.strip().lower() in ['', 'y', 'yes']:
                    new_symbol = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.console.input(f"  请输入正确的代码: ")
                    )
                    p['symbol'] = new_symbol
                else:
                    continue

            # 尝试从市场获取股票信息验证
            if self.market_provider:
                try:
                    from core.models import Market
                    stock_info = await self.market_provider.get_stock_info(
                        p['symbol'],
                        Market.A_SHARE
                    )

                    if stock_info:
                        self.console.print("[green]✓[/green]")
                        # 如果名称不一致，提示用户
                        if stock_info.name != p['name']:
                            self.console.print(f"  [yellow]注意: 实际名称为 {stock_info.name}[/yellow]")
                            name_choice = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: self.console.input(f"  是否使用实际名称？(y/n) [y]: ")
                            )
                            if name_choice.strip().lower() in ['', 'y', 'yes']:
                                p['name'] = stock_info.name
                    else:
                        self.console.print("[yellow]⚠ 未找到股票信息[/yellow]")
                        import_choice = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.console.input(f"  仍要导入？(y/n) [n]: ")
                        )
                        if import_choice.strip().lower() not in ['y', 'yes']:
                            continue
                except Exception as e:
                    self.console.print(f"[yellow]⚠ 验证失败: {e}[/yellow]")
            else:
                self.console.print("[yellow]⚠ 无法验证（市场数据未启用）[/yellow]")

            validated.append(p)

        return validated

    def _is_valid_stock_code(self, code: str) -> bool:
        """验证股票代码格式"""
        # A股：6位数字
        # 沪市：60xxxx
        # 深市：00xxxx, 30xxxx
        return code.isdigit() and len(code) == 6

    async def _select_positions(self, positions: List[Dict]) -> List[Dict]:
        """让用户选择要导入的持仓"""
        self.console.print("\n[cyan]选择要导入的持仓:[/cyan]")
        self.console.print("  输入序号（多个用逗号分隔，如: 1,2,3）")
        self.console.print("  或输入 'all' 导入全部")
        self.console.print("  或输入 'none' 取消")

        choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("  选择: ")
        )
        choice = choice.strip().lower()

        if choice == "none":
            return []
        elif choice == "all":
            return positions
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(",")]
                selected = []
                for idx in indices:
                    if 1 <= idx <= len(positions):
                        selected.append(positions[idx - 1])
                    else:
                        self.console.print(f"[yellow]警告: 序号 {idx} 无效，已跳过[/yellow]")
                return selected
            except ValueError:
                self.console.print("[red]输入格式错误[/red]")
                return []

    async def _edit_positions(self, positions: List[Dict]) -> List[Dict]:
        """让用户编辑持仓数据"""
        edited = []

        for idx, p in enumerate(positions, 1):
            self.console.print(f"\n[cyan]编辑 {idx}/{len(positions)}: {p['name']}({p['symbol']})[/cyan]")

            # 编辑数量
            qty_default = p['quantity']
            new_qty = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.console.input(f"  持仓数量 [{qty_default}]: ")
            )
            p['quantity'] = int(new_qty) if new_qty.strip() else qty_default

            # 编辑成本价
            cost_default = p['cost_price']
            new_cost = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.console.input(f"  成本价 [{cost_default:.2f}]: ")
            )
            p['cost_price'] = float(new_cost) if new_cost.strip() else cost_default

            # 编辑可卖数量
            default_avail = p.get('available_qty', p['quantity'])
            new_avail = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.console.input(f"  可卖数量 [{default_avail}]: ")
            )
            p['available_qty'] = int(new_avail) if new_avail.strip() else default_avail

            edited.append(p)

        return edited

    async def _check_conflicts(self, new_positions: List[Dict]) -> Optional[Dict]:
        """检查是否有重复持仓"""
        current_portfolio = await self.portfolio_provider.get_portfolio()
        current_symbols = {p.stock.symbol: p for p in current_portfolio.positions}

        conflicts = {}
        for p in new_positions:
            if p['symbol'] in current_symbols:
                conflicts[p['symbol']] = {
                    'new': p,
                    'existing': current_symbols[p['symbol']]
                }

        return conflicts if conflicts else None

    async def _handle_conflicts(self, conflicts: Dict) -> str:
        """处理重复持仓"""
        self.console.print("\n[yellow]检测到重复持仓:[/yellow]")

        for symbol, conflict in conflicts.items():
            existing = conflict['existing']
            new = conflict['new']

            self.console.print(f"\n{new['name']}({symbol}):")
            self.console.print(f"  现有: {existing.quantity}股 @ ¥{existing.cost_price:.2f}")
            self.console.print(f"  新增: {new['quantity']}股 @ ¥{new['cost_price']:.2f}")

        self.console.print("\n[cyan]处理方式:[/cyan]")
        self.console.print("  1. 替换 - 用新数据替换现有持仓")
        self.console.print("  2. 合并 - 合并数量，重新计算成本价")
        self.console.print("  3. 跳过 - 保留现有持仓，不导入重复项")
        self.console.print("  4. 取消 - 取消整个导入操作")

        choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("  选择 (1-4) [2]: ")
        )
        choice = choice.strip() or "2"

        strategies = {
            "1": "replace",
            "2": "merge",
            "3": "skip",
            "4": "cancel"
        }

        return strategies.get(choice, "merge")

    async def _execute_import(
        self,
        positions: List[Dict],
        cash: float,
        conflicts: Optional[Dict] = None,
        merge_strategy: str = "merge"
    ) -> bool:
        """执行导入"""
        try:
            for p in positions:
                if conflicts and p['symbol'] in conflicts:
                    if merge_strategy == "skip":
                        continue
                    elif merge_strategy == "replace":
                        self.portfolio_provider.remove_position(p['symbol'])
                    elif merge_strategy == "merge":
                        # 合并持仓
                        existing = conflicts[p['symbol']]['existing']
                        new_qty = existing.quantity + p['quantity']
                        new_cost = (
                            existing.quantity * existing.cost_price +
                            p['quantity'] * p['cost_price']
                        ) / new_qty

                        self.portfolio_provider.update_position(
                            p['symbol'],
                            quantity=new_qty,
                            cost_price=new_cost
                        )
                        continue

                # 添加新持仓
                self.portfolio_provider.add_position(
                    symbol=p['symbol'],
                    name=p['name'],
                    quantity=p['quantity'],
                    cost_price=p['cost_price'],
                    available_qty=p.get('available_qty', p['quantity'])
                )

            # 设置现金
            if cash > 0:
                self.portfolio_provider.set_cash(cash)

            # 自动刷新市场数据
            self.console.print("\n[cyan]正在刷新市场数据...[/cyan]")
            await self.portfolio_provider.refresh()

            self.console.print("[green]✓ 导入成功并已更新行情[/green]")
            return True

        except Exception as e:
            self.console.print(f"\n[red]导入失败: {e}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
            return False
