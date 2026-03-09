"""
命令行界面（CLI）— 交易助手终端版
"""
import asyncio
from typing import Optional
from datetime import datetime
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.live import Live

from config import get_config
from core.llm import LLMConfig
from core.models import AgentContext, MessageRole, Alert, AlertLevel
from providers.llm import create_llm_provider, LLMProviderType, DEFAULT_MODELS
from providers.market_data import (
    AKShareProvider,
    GoogleFinanceProvider,
    MultiSourceProvider
)
from providers.portfolio.manual import ManualPortfolioProvider
from agents.trader import TraderAgent
from .position_manager import PositionManager


class TradingAssistantCLI:
    """交易助手命令行界面"""

    def __init__(self):
        self.console = Console()
        self.config = get_config()
        self.agent: Optional[TraderAgent] = None
        self.market_provider: Optional[AKShareProvider] = None
        self.portfolio_provider: Optional[ManualPortfolioProvider] = None
        self.position_manager: Optional[PositionManager] = None
        self.running = False

    def setup(self):
        """初始化系统"""
        self.console.print("[bold cyan]正在初始化交易助手...[/bold cyan]")

        # 获取当前 API 组配置
        api_group = self.config.get_current_api_group()
        if not api_group:
            self.console.print("[bold red]错误: 未找到有效的 API 组配置[/bold red]")
            return False

        # 检查 API Key
        api_key = api_group.get("api_key", "")
        if not api_key:
            self.console.print(f"[bold red]错误: API 组 '{api_group.get('name')}' 未配置 API Key[/bold red]")
            return False

        # 获取当前模型
        current_model = self.config.get_current_model()

        # 确定 provider 类型
        provider_type_str = api_group.get("provider_type", "openai")
        try:
            provider_type = LLMProviderType(provider_type_str)
        except ValueError:
            provider_type = LLMProviderType.OPENAI  # 默认使用 OpenAI 兼容格式

        llm_config = LLMConfig(
            api_key=api_key,
            model=current_model,
            base_url=api_group.get("base_url") or None,
            max_tokens=self.config.llm_config.get("max_tokens", 4096),
            temperature=self.config.llm_config.get("temperature", 0.0)
        )

        try:
            # 获取自定义 headers（如果有）
            custom_headers = api_group.get("headers")
            llm_provider = create_llm_provider(provider_type, llm_config, custom_headers)
            self.agent = TraderAgent(llm_provider)
            self.agent.start_conversation()
            self.console.print(
                f"[green]✓[/green] API 组: {api_group.get('name')} | "
                f"模型: {current_model}"
            )
        except Exception as e:
            self.console.print(f"[bold red]LLM 初始化失败: {e}[/bold red]")
            return False

        # 初始化市场数据 Provider（使用多数据源）
        try:
            # 创建多个数据源
            google_provider = GoogleFinanceProvider()
            akshare_provider = AKShareProvider()

            # 组合数据源（Google Finance 优先，AKShare 备选）
            self.market_provider = MultiSourceProvider([
                google_provider,  # 优先：支持全球市场，稳定性好
                akshare_provider  # 备选：A股专用，免费
            ])

            self.console.print(f"[green]✓[/green] 市场数据: {self.market_provider.name}")
        except Exception as e:
            self.console.print(f"[yellow]⚠[/yellow] 市场数据初始化失败: {e}")

        # 初始化持仓 Provider
        portfolio_cfg = self.config.portfolio_config
        try:
            self.portfolio_provider = ManualPortfolioProvider(
                data_file=portfolio_cfg.get("data_file", "data/portfolio.json"),
                market_provider=self.market_provider
            )
            self.console.print(f"[green]✓[/green] 持仓管理: {self.portfolio_provider.name}")
        except Exception as e:
            self.console.print(f"[yellow]⚠[/yellow] 持仓管理初始化失败: {e}")

        # 初始化持仓管理器
        if self.portfolio_provider and self.market_provider:
            self.position_manager = PositionManager(
                console=self.console,
                portfolio_provider=self.portfolio_provider,
                market_provider=self.market_provider
            )

        self.console.print("\n[bold green]初始化完成！[/bold green]\n")
        return True

    def show_welcome(self):
        """显示欢迎信息"""
        welcome = """
# 交易助手 CLI

你的私人交易搭档，帮你做更理性的决策。

## 可用命令
- `/help` - 显示帮助
- `/portfolio` - 查看持仓
- `/add <代码> <名称> <数量> <成本价>` - 添加持仓
- `/import <图片路径>` - 从截图导入持仓
- `/refresh` - 刷新持仓价格
- `/models` - 查看和切换模型
- `/apis` - 查看和切换 API 组
- `/history` - 查看对话历史
- `/clear` - 清空对话历史
- `/quit` - 退出

直接输入问题开始对话。
        """
        self.console.print(Markdown(welcome))

    async def handle_command(self, command: str) -> bool:
        """
        处理命令

        Returns:
            True 继续运行，False 退出
        """
        parts = command.strip().split()
        cmd = parts[0].lower()

        if cmd == "/quit" or cmd == "/exit":
            return False

        elif cmd == "/help":
            self.show_welcome()

        elif cmd == "/portfolio":
            await self.show_portfolio()

        elif cmd == "/add":
            if len(parts) < 5:
                self.console.print("[red]用法: /add <代码> <名称> <数量> <成本价>[/red]")
            else:
                await self.add_position(parts[1], parts[2], int(parts[3]), float(parts[4]))

        elif cmd == "/import":
            if len(parts) < 2:
                self.console.print("[red]用法: /import <图片路径>[/red]")
            else:
                await self.import_portfolio_from_image(parts[1])

        elif cmd == "/refresh":
            await self.refresh_portfolio()

        elif cmd == "/models":
            await self.show_and_switch_model()

        elif cmd == "/apis":
            await self.show_and_switch_api()

        elif cmd == "/history":
            self.show_history()

        elif cmd == "/clear":
            self.agent.clear_conversation()
            self.console.print("[green]对话历史已清空[/green]")

        else:
            self.console.print(f"[red]未知命令: {cmd}[/red]")
            self.console.print("输入 /help 查看可用命令")

        return True

    async def show_portfolio(self):
        """显示持仓"""
        if not self.portfolio_provider:
            self.console.print("[yellow]持仓管理未启用[/yellow]")
            return

        portfolio = await self.portfolio_provider.get_portfolio()
        summary = portfolio.to_summary()
        self.console.print(Panel(summary, title="持仓信息", border_style="cyan"))

    async def add_position(self, symbol: str, name: str, quantity: int, cost_price: float):
        """添加持仓"""
        if not self.portfolio_provider:
            self.console.print("[yellow]持仓管理未启用[/yellow]")
            return

        self.portfolio_provider.add_position(symbol, name, quantity, cost_price)
        self.console.print(f"[green]✓[/green] 已添加持仓: {name}({symbol}) {quantity}股 @ ¥{cost_price}")

    async def refresh_portfolio(self):
        """刷新持仓"""
        if not self.portfolio_provider:
            self.console.print("[yellow]持仓管理未启用[/yellow]")
            return

        with self.console.status("[cyan]正在刷新持仓价格...[/cyan]"):
            await self.portfolio_provider.refresh()

        self.console.print("[green]✓[/green] 持仓价格已更新")
        await self.show_portfolio()

    def show_history(self):
        """显示对话历史"""
        history = self.agent.get_conversation_history(20)
        if not history:
            self.console.print("[yellow]暂无对话历史[/yellow]")
            return

        for msg in history:
            role_color = "cyan" if msg["role"] == "user" else "green"
            self.console.print(f"\n[{role_color}]{msg['role'].upper()}[/{role_color}] ({msg['timestamp']})")
            self.console.print(msg["content"])

    async def show_and_switch_model(self):
        """显示并切换模型"""
        models = self.config.get_available_models()
        current_model = self.config.get_current_model()

        # 显示可用模型
        table = Table(title="可用模型")
        table.add_column("序号", justify="center", style="cyan")
        table.add_column("模型 ID", style="yellow")
        table.add_column("名称", style="green")
        table.add_column("描述")
        table.add_column("当前", justify="center")

        for idx, model in enumerate(models, 1):
            is_current = "✓" if model["id"] == current_model else ""
            table.add_row(
                str(idx),
                model["id"],
                model["name"],
                model.get("description", ""),
                is_current
            )

        self.console.print(table)

        # 提示切换
        choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("\n输入序号切换模型（回车跳过）: ")
        )

        if choice.strip().isdigit():
            idx = int(choice.strip())
            if 1 <= idx <= len(models):
                selected_model = models[idx - 1]
                if self.config.switch_model(selected_model["id"]):
                    self.console.print(f"[green]✓[/green] 已切换到模型: {selected_model['name']}")
                    self.console.print("[yellow]请重启程序以应用更改[/yellow]")
                else:
                    self.console.print("[red]切换失败[/red]")
            else:
                self.console.print("[red]无效的序号[/red]")

    async def show_and_switch_api(self):
        """显示并切换 API 组"""
        api_groups = self.config.get_all_api_groups()
        current_group_name = self.config.get("current_api_group", "")

        # 显示可用 API 组
        table = Table(title="API 组列表")
        table.add_column("序号", justify="center", style="cyan")
        table.add_column("组名", style="yellow")
        table.add_column("名称", style="green")
        table.add_column("描述")
        table.add_column("当前", justify="center")

        group_keys = list(api_groups.keys())
        for idx, key in enumerate(group_keys, 1):
            group = api_groups[key]
            is_current = "✓" if key == current_group_name else ""
            table.add_row(
                str(idx),
                key,
                group.get("name", ""),
                group.get("description", ""),
                is_current
            )

        self.console.print(table)

        # 提示切换
        choice = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.console.input("\n输入序号切换 API 组（回车跳过）: ")
        )

        if choice.strip().isdigit():
            idx = int(choice.strip())
            if 1 <= idx <= len(group_keys):
                selected_key = group_keys[idx - 1]
                if self.config.switch_api_group(selected_key):
                    self.console.print(f"[green]✓[/green] 已切换到 API 组: {api_groups[selected_key]['name']}")
                    self.console.print("[yellow]请重启程序以应用更改[/yellow]")
                else:
                    self.console.print("[red]切换失败[/red]")
            else:
                self.console.print("[red]无效的序号[/red]")

    async def import_portfolio_from_image(self, image_path: str):
        """从图片导入持仓"""
        import os
        import base64

        if not os.path.exists(image_path):
            self.console.print(f"[red]错误: 文件不存在: {image_path}[/red]")
            return

        if not self.portfolio_provider:
            self.console.print("[yellow]持仓管理未启用[/yellow]")
            return

        if not self.position_manager:
            self.console.print("[yellow]持仓管理器未初始化[/yellow]")
            return

        self.console.print(f"[cyan]正在识别图片: {image_path}[/cyan]")

        try:
            # 读取图片并编码为 base64
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            # 确定图片类型
            ext = os.path.splitext(image_path)[1].lower()
            media_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp"
            }.get(ext, "image/jpeg")

            # 构建识别提示
            prompt = """请分析这张持仓截图，提取所有股票持仓信息。

要求以 JSON 格式返回，格式如下：
{
  "positions": [
    {
      "symbol": "股票代码（如 600519）",
      "name": "股票名称",
      "quantity": 持仓数量（整数）,
      "cost_price": 成本价（浮点数）,
      "available_qty": 可卖数量（整数，如果没有就等于 quantity）
    }
  ],
  "cash": 可用资金（如果有的话，没有则为 0）
}

注意：
1. 只返回 JSON，不要有其他文字
2. 股票代码要完整（6位数字）
3. 价格保留2位小数
"""

            # 调用 LLM 识别（支持多模态的模型）
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]

            # 使用 agent 的 LLM provider
            with self.console.status("[cyan]识别中...[/cyan]"):
                response = await self.agent.llm.chat(messages)

            result_text = response.content.strip()

            # 清理可能的 markdown 代码块
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            # 解析 JSON
            import json
            data = json.loads(result_text)

            # 使用增强的管理器处理导入
            positions = data.get("positions", [])
            cash = data.get("cash", 0.0)

            success = await self.position_manager.review_and_import_positions(
                positions,
                cash
            )

            if success:
                await self.show_portfolio()

        except json.JSONDecodeError as e:
            self.console.print(f"[red]JSON 解析失败: {e}[/red]")
            self.console.print(f"[yellow]返回内容: {result_text[:200]}[/yellow]")
        except Exception as e:
            self.console.print(f"[red]识别失败: {e}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")

    async def chat(self, user_input: str):
        """与 Agent 对话"""
        # 构建上下文
        context = await self.build_context()

        # 流式输出
        self.console.print("\n[bold green]助手:[/bold green] ", end="")

        response_text = ""
        async for chunk in self.agent.chat(user_input, context, stream=True):
            self.console.print(chunk, end="")
            response_text += chunk

        self.console.print("\n")

    async def build_context(self) -> AgentContext:
        """构建 Agent 上下文"""
        context = AgentContext(
            conversation=self.agent.conversation,
            current_time=datetime.now()
        )

        # 添加持仓摘要
        if self.portfolio_provider:
            try:
                portfolio = await self.portfolio_provider.get_portfolio()
                context.portfolio_summary = portfolio.to_summary()
            except:
                pass

        # 判断市场阶段
        now = datetime.now()
        hour = now.hour
        minute = now.minute

        if hour < 9 or (hour == 9 and minute < 30):
            context.session_phase = "盘前准备"
        elif (hour == 9 and minute >= 30) or (9 < hour < 15) or (hour == 15 and minute == 0):
            context.session_phase = "盘中交易"
        else:
            context.session_phase = "盘后复盘"

        return context

    async def run(self):
        """运行主循环"""
        if not self.setup():
            return

        self.show_welcome()
        self.running = True

        try:
            while self.running:
                try:
                    # 获取用户输入
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.console.input("\n[bold cyan]你:[/bold cyan] ")
                    )

                    if not user_input.strip():
                        continue

                    # 处理命令
                    if user_input.startswith("/"):
                        should_continue = await self.handle_command(user_input)
                        if not should_continue:
                            break
                    else:
                        # 对话
                        await self.chat(user_input)

                except KeyboardInterrupt:
                    self.console.print("\n[yellow]使用 /quit 退出[/yellow]")
                except Exception as e:
                    self.console.print(f"\n[bold red]错误: {e}[/bold red]")

        finally:
            self.console.print("\n[cyan]再见！祝你交易顺利 📈[/cyan]")


def main():
    """主入口"""
    cli = TradingAssistantCLI()
    asyncio.run(cli.run())


if __name__ == "__main__":
    main()
